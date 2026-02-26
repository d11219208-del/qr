from flask import Blueprint, render_template, request, jsonify
import json
import base64  # 用於 RawBT 編碼
import traceback 
from datetime import datetime, timedelta
from database import get_db_connection

kitchen_bp = Blueprint('kitchen', __name__)

# --- 輔助函式：取得當前台灣時間字串 (用於 Log) ---
def get_current_time_str():
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

# --- 輔助函式：計算台灣時間範圍 ---
def get_tw_time_range(target_date_str=None, end_date_str=None):
    try:
        if target_date_str and 'T' in target_date_str:
            tw_start = datetime.strptime(target_date_str, '%Y-%m-%dT%H:%M')
            is_specific_time = True
        elif target_date_str:
            tw_start = datetime.strptime(target_date_str, '%Y-%m-%d')
            is_specific_time = False
        else:
            tw_start = datetime.utcnow() + timedelta(hours=8)
            is_specific_time = False
        
        if not is_specific_time:
            tw_start = tw_start.replace(hour=0, minute=0, second=0, microsecond=0)

        if end_date_str and 'T' in end_date_str:
            tw_end = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
        elif end_date_str:
            tw_end = datetime.strptime(end_date_str, '%Y-%m-%d')
            tw_end = tw_end.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            tw_end = tw_start.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        return tw_start - timedelta(hours=8), tw_end - timedelta(hours=8)

    except Exception as e:
        print(f"Time Range Error: {e}")
        now = datetime.utcnow() + timedelta(hours=8)
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8), \
               now.replace(hour=23, minute=59, second=59, microsecond=999999) - timedelta(hours=8)


# --- 1. 廚房看板主頁 ---
@kitchen_bp.route('/')
def kitchen_panel():
    return render_template('kitchen.html')


# --- 2. 檢查新訂單 API ---
@kitchen_bp.route('/check_new_orders')
def check_new_orders():
    try:
        # 【關鍵修改 1】：接收前端傳來的最後一次看過的序號 (預設為 0)
        last_seq = request.args.get('last_seq', 0, type=int)

        utc_start, utc_end = get_tw_time_range()

        conn = get_db_connection()
        cur = conn.cursor()
        
        # SQL 查詢：確保包含 customer_address
        query = """
            SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json,
                   customer_name, customer_phone, customer_address, scheduled_for, delivery_fee, order_type
            FROM orders 
            WHERE created_at >= %s AND created_at <= %s
            ORDER BY 
                CASE WHEN status = 'Pending' THEN 0 
                     WHEN status = 'Completed' THEN 1 
                     ELSE 2 END, 
                daily_seq DESC
        """
        try:
            cur.execute(query, (utc_start, utc_end))
        except Exception as e:
            conn.rollback() 
            print(f"SQL Fallback triggered (check_new_orders): {e}")
            # Fallback (防止舊資料庫結構缺少 order_type 報錯)
            query_fallback = """
                SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json,
                       customer_name, customer_phone, customer_address, scheduled_for, delivery_fee, 'unknown'
                FROM orders 
                WHERE created_at >= %s AND created_at <= %s
                ORDER BY status, daily_seq DESC
            """
            cur.execute(query_fallback, (utc_start, utc_end))

        orders = cur.fetchall()
        
        # 取得目前最大序號
        cur.execute("SELECT MAX(daily_seq) FROM orders WHERE created_at >= %s AND created_at <= %s", (utc_start, utc_end))
        res_max = cur.fetchone()
        max_seq_val = res_max[0] if res_max and res_max[0] else 0
        
        conn.close()

        html_content = ""
        pending_ids = []

        if not orders: 
            html_content = "<div id='loading-msg' style='grid-column:1/-1;text-align:center;padding:100px;font-size:1.5em;color:#888;'>🍽️ 目前沒有訂單</div>"
        
        for o in orders:
            # 解包變數 (確保變數數量 = 15)
            oid, table, raw_items, total, status, created, order_lang, seq_num, c_json, \
            c_name, c_phone, c_addr, c_schedule, c_fee, c_type = o
            
            status_cls = status.lower()
            tw_time = created + timedelta(hours=8)
            
            # 【關鍵修改 2】：只有當狀態是 Pending，且單號「大於」前端已知的 last_seq 時，才視為真正的新訂單
            if status == 'Pending' and seq_num > last_seq:
                pending_ids.append(oid)

            # 資料預處理
            table_str = str(table).strip() if table else ""
            c_fee = int(c_fee or 0)
            c_type = str(c_type).lower() if c_type else 'unknown'
            
            # 判斷是否為外送/外帶/預約
            has_contact = (c_phone and str(c_phone).strip() != '' and str(c_phone).strip().lower() != 'none')
            has_addr = (c_addr and str(c_addr).strip() != '' and str(c_addr).strip().lower() != 'none')
            has_schedule = (c_schedule and str(c_schedule).strip() != '' and str(c_schedule).lower() != 'none')

            # 邏輯判斷
            if c_type == 'delivery':
                is_delivery = True
                display_table = "🛵 外送"
            elif c_type == 'takeout':
                is_delivery = False
                display_table = "🥡 自取"
            elif c_type == 'dine_in':
                is_delivery = False
                display_table = f"桌號 {table_str}"
            else:
                # 舊邏輯 Fallback
                is_delivery = (table_str == '外送') or has_addr
                if is_delivery:
                    display_table = "🛵 外送"
                elif table_str:
                    display_table = f"桌號 {table_str}"
                else:
                    display_table = "🥡 外帶"

            # 組合詳細資訊 (HTML)
            info_html = ""
            
            # 預約時間顯示 (醒目)
            if has_schedule:
                info_html += f"<div style='background:#fff9c4; color:#f57f17; padding:4px; border-radius:4px; margin-bottom:4px; font-weight:bold; border:1px solid #fbc02d;'>🕒 預約: {c_schedule}</div>"

            # 姓名
            if c_name and str(c_name).strip() and str(c_name).lower() != 'none': 
                info_html += f"<div>👤 {c_name}</div>"
            
            # 電話
            if has_contact:
                info_html += f"<div>📞 {c_phone}</div>"
            
            # 地址顯示
            if has_addr:
                info_html += f"<div style='margin-top:2px; line-height:1.2; border-top:1px dashed #aaa; padding-top:2px; font-weight:bold; color:#bf360c;'>📍 {c_addr}</div>"

            # 將詳細資訊嵌入桌號區塊
            if info_html:
                table_html = f"<div class='table-num' style='flex-direction:column; padding:5px;'><div>{display_table}</div><div style='font-size:0.5em; font-weight:normal; text-align:left; width:100%; margin-top:5px; color:#333; word-break:break-all;'>{info_html}</div></div>"
            else:
                table_html = f"<div class='table-num'>{display_table}</div>"

            # 解析商品 JSON
            items_html = ""
            try:
                if isinstance(c_json, str):
                    cart = json.loads(c_json)
                elif isinstance(c_json, (list, dict)):
                    cart = c_json if isinstance(c_json, list) else [c_json]
                else:
                    cart = []

                for item in cart:
                    name = item.get('name_zh', item.get('name', '商品'))
                    qty = item.get('qty', 1)
                    options = item.get('options_zh', item.get('options', []))
                    opts_html = f"<div class='item-opts'>└ {' / '.join(options)}</div>" if options else ""
                    items_html += f"<div class='item-row'><div class='item-name'><span>{name}</span><span class='item-qty'>x{qty}</span></div>{opts_html}</div>"
            except Exception as e: 
                items_html = "<div class='item-row'>資料解析錯誤</div>"

            formatted_total = f"{int(total or 0)}" 
            
            # 運費顯示邏輯
            fee_html = ""
            if c_fee > 0:
                fee_html = f"<span style='font-size:12px; color:#888; margin-right:5px;'>(含運 ${c_fee})</span>"

            buttons = ""
            print_btn_html = f"<button onclick='askPrintType({oid})' class='btn btn-print' style='flex:1;'>🖨️ 列印</button>"

            if status == 'Pending':
                buttons += f"""
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding:0 5px;">
                        <span style="font-size:14px; color:#666; font-weight:bold;">應收總計:</span>
                        <div>{fee_html}<span style="font-size:22px; color:#d32f2f; font-weight:900;">${formatted_total}</span></div>
                    </div>
                """
                buttons += f"<button onclick='action(\"/kitchen/complete/{oid}\")' class='btn btn-main' style='width:100%; margin-bottom:8px;'>✅ 出餐 / 付款</button>"
                buttons += f"""<div class="btn-group" style="display:flex; gap:5px;">
                    {print_btn_html}
                    <a href='/menu?edit_oid={oid}&lang=zh' target='_blank' class='btn' style='flex:1; background:#ff9800; color:white;'>✏️ 修改</a>
                    <button onclick='if(confirm(\"⚠️ 確定作廢此單？\")) action(\"/kitchen/cancel/{oid}\")' class='btn btn-void' style='width:50px;'>🗑️</button>
                </div>"""
            elif status == 'Cancelled':
                buttons += f"<div style='text-align:center; color:#d32f2f; font-weight:bold; margin-bottom:5px;'>【此單已作廢】</div>"
                buttons += f"<button onclick='askPrintType({oid})' class='btn btn-print' style='width:100%; opacity:0.6;'>🖨️ 補印作廢單</button>"
            else: # Completed
                buttons += f"""
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding:0 5px; opacity:0.7;">
                        <span style="font-size:13px; color:#666;">實收總計:</span>
                        <div>{fee_html}<span style="font-size:18px; color:#333; font-weight:bold;">${formatted_total}</span></div>
                    </div>
                """
                buttons += f"<button onclick='askPrintType({oid})' class='btn btn-print' style='width:100%;'>🖨️ 補印單據</button>"

            html_content += f"""
            <div class="card {status_cls}" data-id="{oid}">
                <div class="card-header">
                    <div><div class="seq-num">#{seq_num:03d}</div><div class="time-stamp">{tw_time.strftime('%H:%M')} ({order_lang})</div></div>
                    {table_html}
                </div>
                <div class="items">{items_html}</div>
                <div class="actions">{buttons}</div>
            </div>"""
            
        return jsonify({
            'html': html_content, 
            'max_seq': max_seq_val, 
            'new_ids': pending_ids 
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'html': f"載入錯誤: {str(e)}", 'max_seq': 0, 'new_ids': []})


# --- 3. 核心列印路由 (已優化：支援 WebUSB Base54 繁體中文 & 原有功能) ---
@kitchen_bp.route('/print_order/<int:oid>')
def print_order(oid):
    try:
        print_type = request.args.get('type', 'all')
        output_format = request.args.get('format', 'html') # 支援 html, raw, base64
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. SQL 查詢：讀取所有訂單欄位
        query = """
            SELECT table_number, total_price, daily_seq, content_json, created_at, status,
                   customer_name, customer_phone, customer_address, delivery_fee, scheduled_for, 
                   order_type, lang
            FROM orders WHERE id=%s
        """
        try:
            cur.execute(query, (oid,))
            order = cur.fetchone()
        except Exception as e:
            conn.rollback() 
            print(f"SQL Fallback triggered: {e}")
            cur.execute("""
                SELECT table_number, total_price, daily_seq, content_json, created_at, status,
                       customer_name, customer_phone, customer_address, delivery_fee, scheduled_for, 
                       'unknown', 'zh'
                FROM orders WHERE id=%s
            """, (oid,))
            order = cur.fetchone()

        # 2. 獲取產品資料與自定義選項 Mapping (用於動態翻譯)
        cur.execute("""
            SELECT name, print_category, 
                   custom_options, custom_options_en, custom_options_jp, custom_options_kr 
            FROM products
        """)
        product_map = {}
        for row in cur.fetchall():
            p_name = row[0]
            def split_opts(opt_str):
                if not opt_str: return []
                return [o.strip() for o in opt_str.split(',') if o.strip()]
            product_map[p_name] = {
                'cat': row[1] or 'Other',
                'zh': split_opts(row[2]),
                'en': split_opts(row[3]),
                'jp': split_opts(row[4]),
                'kr': split_opts(row[5])
            }
        conn.close()
        
        if not order:
            return "訂單不存在", 404
        
        # 3. 解析訂單資料 (正確對應 c_lang)
        table_num, total_price, seq, content_json, created_at, status, \
        c_name, c_phone, c_addr, c_fee, c_schedule, c_type, c_lang = order
        
        c_fee = int(c_fee or 0)
        table_str = str(table_num).strip() if table_num else ""
        c_type = str(c_type).lower() if c_type else 'unknown'
        c_lang = str(c_lang).lower() if c_lang else 'zh' # 這是關鍵，結帳單將使用此語系
        
        has_contact = (c_phone and str(c_phone).strip() != '' and str(c_phone).lower() != 'none')
        has_addr = (c_addr and str(c_addr).strip() != '' and str(c_addr).lower() != 'none')
        has_schedule = (c_schedule and str(c_schedule).strip() != '' and str(c_schedule).lower() != 'none')
        
        if c_type == 'delivery':
            display_tbl_name = "🛵 外送"
        elif c_type == 'takeout':
            display_tbl_name = "🥡 自取"
        elif c_type == 'dine_in':
            display_tbl_name = f"桌號 {table_str}"
        else:
            is_delivery = (table_str == '外送') or has_addr
            display_tbl_name = "外送" if is_delivery else (table_str if table_str else "外帶")

        if isinstance(content_json, str):
            try: items = json.loads(content_json)
            except: items = []
        else:
            items = content_json if isinstance(content_json, list) else [content_json]
        
        time_str = (created_at + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')

        # 4. 廚房單分區邏輯
        noodle_items, soup_items, other_items = [], [], []
        for item in items:
            p_name = item.get('name_zh') or item.get('name')
            p_cat = product_map.get(p_name, {}).get('cat', 'Other') 
            if p_cat == 'Noodle': noodle_items.append(item)
            elif p_cat == 'Soup': soup_items.append(item)
            else: other_items.append(item)

        # 5. 翻譯函數
        def translate_option(p_name, opt_str, target_lang):
            if p_name not in product_map: return opt_str
            p_data = product_map[p_name]
            found_idx = -1
            for lang in ['zh', 'en', 'jp', 'kr']:
                if opt_str in p_data[lang]:
                    found_idx = p_data[lang].index(opt_str)
                    break
            if found_idx != -1:
                target_list = p_data.get(target_lang, [])
                if found_idx < len(target_list): return target_list[found_idx]
            return opt_str

        # 6. 核心內容生成函數 (包含 ESC/POS 位元指令)
        def generate_content(title, item_list, is_receipt=False):
            if not item_list and not is_receipt: return b"" 
            
            # --- ESC/POS 指令定義 ---
            ESC = b'\x1b'
            GS = b'\x1d'
            RESET = ESC + b'@'
            BOLD_ON = ESC + b'E\x01'
            BOLD_OFF = ESC + b'E\x00'
            DBL_SIZE = GS + b'!\x11'  # 16進制 0x11 = 寬2倍 + 高2倍
            NORMAL_SIZE = GS + b'!\x00'
            CENTER = ESC + b'a\x01'
            LEFT = ESC + b'a\x00'
            
            # 決定編碼 (結帳單非中文語系需用 UTF-8 或對應 Page，此處預設 Big5 供中文機型)
            # 若印表機支援 UTF-8，建議統一用 UTF-8
            ENCODE = 'big5-hkscs' 

            res = RESET + CENTER + BOLD_ON + DBL_SIZE + title.encode(ENCODE, 'replace') + b"\n"
            res += NORMAL_SIZE + f"NO: #{seq:03d}\n".encode(ENCODE)
            res += DBL_SIZE + f"{display_tbl_name}\n".encode(ENCODE, 'replace') + NORMAL_SIZE
            res += LEFT + f"TIME: {time_str}\n".encode(ENCODE)
            
            if has_schedule: res += f"🕒預約: {c_schedule}\n".encode(ENCODE, 'replace')
            if c_name: res += f"👤客戶: {c_name}\n".encode(ENCODE, 'replace')
            if has_addr: res += f"📍地址: {c_addr}\n".encode(ENCODE, 'replace')
            
            res += b"-"*32 + b"\n"
            
            for i in item_list:
                name_zh = i.get('name_zh') or i.get('name')
                qty = i.get('qty', 1)
                
                # 語系切換邏輯
                target_lang = c_lang if is_receipt else 'zh'
                display_name = name_zh
                if target_lang != 'zh':
                    display_name = i.get(f"name_{target_lang}") or i.get('name_en') or name_zh
                
                # 品項名稱放大
                res += BOLD_ON + DBL_SIZE + f"{display_name} x{qty}\n".encode(ENCODE, 'replace') + NORMAL_SIZE + BOLD_OFF
                
                # 選項翻譯
                raw_opts = i.get('options') or i.get('options_zh') or []
                opts_list = (raw_opts if isinstance(raw_opts, list) else [raw_opts])
                translated_opts = [translate_option(name_zh, str(opt), target_lang) for opt in opts_list]
                
                if translated_opts:
                    res += f"  ({', '.join(translated_opts)})\n".encode(ENCODE, 'replace')
            
            res += b"-"*32 + b"\n"
            if is_receipt:
                res += DBL_SIZE + BOLD_ON + f"TOTAL: ${int(total_price or 0)}\n".encode(ENCODE) + NORMAL_SIZE + BOLD_OFF
            
            res += b"\n\n\n\n" # 留白
            return res

        # 7. HTML 內容生成 (供 RawBT 或 網頁預覽，保持原有功能)
        def generate_html_content(title, item_list, is_receipt=False):
            if not item_list and not is_receipt: return ""
            void_mark = "<div class='void-watermark'>作廢單</div>" if status == 'Cancelled' else ""
            h = f"<div class='ticket'>{void_mark}<div class='head'><h2>{title}</h2><h1>#{seq:03d}</h1></div>"
            h += f"<div class='info-box'><div class='table-row'><span class='table-val'>{display_tbl_name}</span></div>"
            h += f"<div class='time-row'>下單: {time_str}</div></div>"
            if has_schedule: h += f"<div class='schedule-row'>🕒 預約: {c_schedule}</div>"
            if is_delivery or has_contact or (c_name and str(c_name).strip()):
                h += f"<div class='customer-info'>"
                if c_name: h += f"<div class='cust-row'>👤 {c_name}</div>"
                if has_contact: h += f"<div class='cust-row'>📞 {c_phone}</div>"
                if has_addr: h += f"<div class='addr-row'>📍 {c_addr}</div>"
                h += f"</div>"
            for i in item_list:
                name_zh = i.get('name_zh') or i.get('name')
                target_lang = c_lang if is_receipt else 'zh'
                main_name = i.get(f"name_{target_lang}") or i.get('name_en') or name_zh if target_lang != 'zh' else name_zh
                sub_name = name_zh if target_lang != 'zh' else ""
                
                raw_opts = i.get('options') or i.get('options_zh') or []
                opts_display = [translate_option(name_zh, str(opt), target_lang) for opt in (raw_opts if isinstance(raw_opts, list) else [raw_opts])]
                
                h += f"<div class='item-row'><div class='name-col'><span class='item-name-main'>{main_name}</span>"
                if sub_name: h += f"<span class='item-name-sub'>{sub_name}</span>"
                h += f"</div><span class='item-qty'>x{i.get('qty', 1)}</span></div>"
                if opts_display: h += f"<div class='opt'>└ {', '.join(opts_display)}</div>"
            if is_receipt:
                if c_fee > 0: h += f"<div class='fee-row'>小計: ${int(total_price - c_fee)}</div><div class='fee-row'>運費: ${c_fee}</div>"
                h += f"<div class='total'>Total: ${int(total_price or 0)}</div>"
            return h + "</div>"

        # 8. 輸出格式判斷
        if output_format == 'base64':
            full_bin_payload = b""
            # 初始化指令 (ESC @), 進入漢字模式 (FS &), 設定 CodePage 為 950 (ESC t 13)
            init_cmds = b'\x1b\x40\x1c\x26\x1b\x74\x0d'
            full_bin_payload += init_cmds
            
            if print_type in ['all', 'receipt']:
                full_bin_payload += generate_content("結帳單 Receipt", items, is_receipt=True)
            if print_type in ['all', 'kitchen']:
                if noodle_items: full_bin_payload += generate_content("廚房單-麵區", noodle_items)
                if soup_items: full_bin_payload += generate_content("廚房單-湯區", soup_items)
                if other_items: full_bin_payload += generate_content("廚房單-其他", other_items)
            
            # 切紙指令
            full_bin_payload += b'\x1d\x56\x42\x00'
            
            return jsonify({
                "status": "success",
                "blob": base64.b64encode(full_bin_payload).decode('utf-8')
            })

        # --- 以下為 HTML / RawBT 輸出邏輯 ---
        html_content = ""
        if print_type in ['all', 'receipt']:
            html_content += generate_html_content("結帳單 Receipt", items, is_receipt=True)
        if print_type in ['all', 'kitchen']:
            if noodle_items: html_content += generate_html_content("廚房單 - 麵區", noodle_items)
            if soup_items: html_content += generate_html_content("廚房單 - 湯區", soup_items)
            if other_items: html_content += generate_html_content("廚房單 - 其他", other_items)

        style = "<style>@page { size: 80mm auto; margin: 0mm; } body { font-family: 'Microsoft JhengHei', sans-serif; width: 78mm; margin: 0 auto; padding: 2px; } .ticket { border-bottom: 3px dashed #000; padding: 10px 0 30px 0; margin-bottom: 10px; page-break-after: always; position: relative; } .head h2 { font-size: 24px; margin: 0; border: 2px solid #000; padding: 4px 10px; display: inline-block; font-weight: 900; } .head h1 { font-size: 42px; margin: 5px 0; font-weight: 900; } .info-box { border-bottom: 2px solid #000; padding-bottom: 5px; } .table-row { text-align: center; } .table-val { font-size: 36px; font-weight: 900; } .time-row { font-size: 14px; text-align: center; } .customer-info { border: 2px solid #000; padding: 6px; font-size: 18px; font-weight: bold; } .addr-row { font-size: 24px; font-weight: 900; border-top: 1px dashed #000; } .schedule-row { font-size: 22px; font-weight: 900; background: #000; color: #fff; text-align: center; } .item-row { display: flex; justify-content: space-between; font-size: 24px; font-weight: 900; margin-top: 5px;} .item-name-sub { font-size: 16px; display: block; color: #555; } .opt { font-size: 18px; font-weight: bold; padding-left: 10px; } .total { text-align: right; font-size: 28px; font-weight: 900; border-top: 2px solid #000; margin-top: 10px; }</style>"
        
        if output_format == 'raw':
            return html_content

        # Android RawBT Intent
        rawbt_html_source = f"<html><head><meta charset='utf-8'>{style}</head><body>{html_content}</body></html>"
        b64_html = base64.b64encode(rawbt_html_source.encode('utf-8')).decode('utf-8')
        intent_url = f"intent:base64,{b64_html}#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;S.jobName=Order_{seq};S.editor=false;end;"

        return f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Print Order</title>{style}</head><body>{html_content}<script>if(/android/i.test(navigator.userAgent)){{window.location.href='{intent_url}';setTimeout(function(){{if(window.opener)window.close();}},1500);}}</script></body></html>"

    except Exception as e:
        traceback.print_exc()
        return f"Print Error: {str(e)}", 500


# --- 4. 狀態變更 (完成/作廢) ---
@kitchen_bp.route('/complete/<int:oid>')
def complete_order(oid):
    try:
        c=get_db_connection(); cur=c.cursor()
        cur.execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,))
        c.commit(); c.close(); 
        print(f"[{get_current_time_str()}] ✅ 訂單完成: ID {oid}")
        return "OK"
    except Exception as e:
        print(f"Error completing order: {e}")
        return "Error", 500

@kitchen_bp.route('/cancel/<int:oid>')
def cancel_order(oid):
    try:
        c=get_db_connection(); cur=c.cursor()
        cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,))
        c.commit(); c.close(); 
        print(f"[{get_current_time_str()}] 🗑️ 訂單作廢: ID {oid}")
        return "OK"
    except Exception as e:
        print(f"Error cancelling order: {e}")
        return "Error", 500


# --- 5. 銷售排名 API ---
@kitchen_bp.route('/sales_ranking')
def sales_ranking():
    start_time_str = request.args.get('start_time')
    end_time_str = request.args.get('end_time')
    utc_start, utc_end = get_tw_time_range(start_time_str, end_time_str)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT content_json FROM orders 
        WHERE created_at >= %s AND created_at <= %s 
        AND status IN ('Pending', 'Completed')
    """, (utc_start, utc_end))
    rows = cur.fetchall()
    conn.close()
    
    stats = {}
    for r in rows:
        if not r[0]: continue
        try:
            items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            if not isinstance(items, list): items = []
            for i in items:
                name = i.get('name_zh', i.get('name', '未知品項'))
                qty = int(float(i.get('qty', 1)))
                stats[name] = stats.get(name, 0) + qty
        except: continue
        
    sorted_data = [{"name": k, "count": v} for k, v in sorted(stats.items(), key=lambda item: item[1], reverse=True)]
    return jsonify(sorted_data)


# --- 6. 日結報表 (HTML) - 補完部分 ---
@kitchen_bp.route('/report')
def daily_report():
    target_date_str = request.args.get('date') or (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d')
    utc_start, utc_end = get_tw_time_range(target_date_str)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 取得產品價格表
    cur.execute("SELECT name, price FROM products")
    price_map = {row[0]: row[1] for row in cur.fetchall()}
    
    # 統計：有效訂單 (Pending + Completed)
    cur.execute("""
        SELECT COUNT(*), SUM(total_price), content_json 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s 
        AND status IN ('Pending', 'Completed')
        GROUP BY id
    """, (utc_start, utc_end))
    v_rows = cur.fetchall()
    
    v_count = len(v_rows)
    v_total = sum([r[1] for r in v_rows if r[1]])

    # 統計：作廢訂單 (Cancelled)
    cur.execute("""
        SELECT COUNT(*), SUM(total_price), content_json 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s 
        AND status = 'Cancelled'
        GROUP BY id
    """, (utc_start, utc_end))
    x_rows = cur.fetchall()

    x_count = len(x_rows)
    x_total = sum([r[1] for r in x_rows if r[1]])
    conn.close()

    # 聚合商品統計函式
    def agg(rows):
        res = {}
        for r in rows:
            if not r[2]: continue
            try:
                items = json.loads(r[2]) if isinstance(r[2], str) else r[2]
                if not isinstance(items, list): items = []
                for i in items:
                    name = i.get('name_zh', i.get('name', '商品'))
                    qty = int(float(i.get('qty', 1)))
                    price_val = i.get('price')
                    # 如果訂單內沒存價格，查價格表
                    price = int(float(price_val)) if price_val is not None else price_map.get(name, 0)
                    
                    if name not in res: res[name] = {'qty':0, 'amt':0}
                    res[name]['qty'] += qty
                    res[name]['amt'] += (qty * price)
            except: continue
        return res

    v_stats = agg(v_rows)
    x_stats = agg(x_rows)

    # 產生表格 HTML 函式 (移除所有顏色，改為純黑白線條)
    def tbl(stats_dict):
        if not stats_dict: return "<p style='text-align:center; color:#000; font-weight:bold;'>無數據</p>"
        h = "<table class='report-table'><thead><tr><th style='text-align:left;'>品項</th><th style='text-align:right;'>數量</th><th style='text-align:right;'>金額</th></tr></thead><tbody>"
        for k, v in sorted(stats_dict.items(), key=lambda x:x[1]['qty'], reverse=True):
            h += f"<tr><td>{k}</td><td style='text-align:right;'>{v['qty']}</td><td style='text-align:right;'>${v['amt']:,}</td></tr>"
        return h + "</tbody></table>"

    # 最終 HTML 輸出 (純黑白 + 80mm 自動長度設定)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>日結報表_{target_date_str}</title>
        <style>
            /* 螢幕預覽時的背景 (列印時會隱藏) */
            body {{ font-family: 'Microsoft JhengHei', sans-serif; background: #f4f4f4; display:flex; flex-direction:column; align-items:center; padding:20px; color: #000; }}
            
            /* 單據本體設定 */
            .ticket {{ background: white; width: 78mm; padding: 0; color: #000; }}
            
            /* 黑白化區塊設定 */
            .summary {{ padding: 10px; margin: 10px 0; border: 2px solid #000; font-weight: bold; }}
            .void-sum {{ padding: 10px; margin: 10px 0; border: 2px dashed #000; font-weight: bold; }}
            .header {{ text-align:center; border-bottom: 2px dashed #000; padding-bottom:10px; margin-bottom:10px; }}
            .section-title {{ font-size:18px; font-weight:bold; margin-top:15px; border-bottom: 2px solid #000; padding-bottom:5px; margin-bottom: 5px; color: #000; }}
            
            h1 {{ margin:0; font-size:24px; font-weight: 900; }}
            p {{ margin:5px 0; color: #000; }}
            .big-num {{ font-size:20px; font-weight:900; }}
            
            /* 表格黑白線條設定 */
            .report-table {{ width:100%; border-collapse:collapse; margin-top:10px; color: #000; }}
            .report-table th {{ border-bottom: 2px solid #000; padding-bottom: 5px; font-weight: bold; }}
            .report-table td {{ border-bottom: 1px dashed #000; padding: 5px 0; }}
            
            /* --- 關鍵：專為熱感出單機設計的列印設定 --- */
            @page {{ 
                size: 80mm auto; /* 80mm 寬度，長度自動延伸 */
                margin: 0mm;     /* 消除印表機預設邊界 */
            }}
            
            @media print {{ 
                .no-print {{ display: none !important; }} 
                body {{ background: transparent; padding: 0; margin: 0; }} 
                .ticket {{ width: 80mm; box-shadow: none; border: none; }}
                
                /* 強制所有內容為純黑白，避免印表機灰階化導致字體變淡 */
                * {{ color: #000 !important; background: transparent !important; }}
            }}
        </style>
    </head>
    <body>
        <div class="no-print" style="margin-bottom:20px; text-align:center;">
            <div style="margin-bottom:10px;">
                <label style="font-weight:bold;">選擇日期：</label>
                <input type="date" id="dateInput" value="{target_date_str}" onchange="location.href='/kitchen/report?date='+this.value" style="padding: 5px; font-size: 16px;">
            </div>
            <button onclick="window.print()" style="padding:10px 20px; font-size:16px; background:#000; color:#fff; border:2px solid #000; font-weight:bold; cursor:pointer;">🖨️ 列印報表</button>
            <button onclick="location.href='/kitchen'" style="padding:10px 20px; font-size:16px; background:#fff; color:#000; border:2px solid #000; font-weight:bold; cursor:pointer; margin-left:10px;">🔙 返回看板</button>
        </div>

        <div class="ticket">
            <div class="header">
                <h1>日結營收報表</h1>
                <p style="font-size: 18px; font-weight: bold;">{target_date_str}</p>
                <p style="font-size:12px;">列印時間: {datetime.now().strftime('%H:%M:%S')}</p>
            </div>

            <div class="summary">
                <div>有效營收</div>
                <div style="display:flex; justify-content:space-between; margin-top:5px;">
                    <span>訂單: <span class="big-num">{v_count}</span> 單</span>
                    <span>總計: <span class="big-num">${v_total:,}</span></span>
                </div>
            </div>

            <div class="void-sum">
                <div>作廢統計</div>
                <div style="display:flex; justify-content:space-between; margin-top:5px;">
                    <span>作廢: {x_count} 單</span>
                    <span>作廢額: ${x_total:,}</span>
                </div>
            </div>

            <div class="section-title">商品銷售明細</div>
            {tbl(v_stats)}

            <div class="section-title" style="margin-top:30px;">作廢商品明細</div>
            <div>
                {tbl(x_stats)}
            </div>

            <div style="margin-top:40px; text-align:center; border-top:2px solid #000; padding-top:10px;">
                <p style="font-weight: bold;">經手人簽名</p>
                <br><br>
                <p>____________________</p>
                <p style="font-size: 12px; margin-top: 20px;">- End of Report -</p>
            </div>
        </div>
    </body>
    </html>
    """






