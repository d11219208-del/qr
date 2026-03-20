from flask import Blueprint, render_template, request, jsonify, render_template_string, redirect, url_for, session
import json
import base64  
import psycopg2.extras
import traceback  
import bcrypt  # 💡 新增：引入 bcrypt 用來驗證密碼
#引入綠界發票api
from ecpay_invoice import issue_ecpay_invoice, invalid_ecpay_invoice, print_ecpay_invoice
# 🛡️ 引入我們在 utils.py 寫好的雙重防護罩
from utils import login_required, role_required
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

# ==========================================
# 🛡️ 登入與登出系統
# ==========================================

# ==========================================
# 🛡️ 登入與登出系統
# ==========================================

@kitchen_bp.route('/login', methods=['GET', 'POST'])
def login():
    """處理登入"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return render_template('login.html', error="請輸入帳號和密碼")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, password_hash, role FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            
            if user:
                user_id, hashed_pw, role = user
                
                if bcrypt.checkpw(password.encode('utf-8'), hashed_pw.encode('utf-8')):
                    session['user_id'] = user_id
                    session['username'] = username
                    session['role'] = role
                    
                    # 💡 修正：把「判斷是否為 admin」的囉嗦邏輯拿掉！
                    # 只要是在廚房登入成功，通通導向廚房看板！
                    return redirect(url_for('kitchen.kitchen_panel'))
                else:
                    return render_template('login.html', error="密碼錯誤")
            else:
                return render_template('login.html', error="找不到此帳號")
                
        except Exception as e:
            print(f"Login Error: {e}")
            return render_template('login.html', error="系統發生錯誤，請稍後再試")
        finally:
            cur.close()
            conn.close()
            
    return render_template('login.html')

@kitchen_bp.route('/logout')
def logout():
    """處理登出"""
    session.clear() # 清除通行證
    return redirect(url_for('kitchen.login'))


# --- 1. 廚房看板主頁 ---
@kitchen_bp.route('/')
@login_required          # 🛡️ 防護 1：必須登入
def kitchen_panel():
    return render_template('kitchen.html')


# --- 2. 檢查新訂單 API ---
@kitchen_bp.route('/check_new_orders')
#@login_required          # 🛡️ 防護 1：必須登入
def check_new_orders():
    try:
        # 【關鍵修改 1】：接收前端傳來的最後一次看過的序號 (預設為 0)
        last_seq = request.args.get('last_seq', 0, type=int)

        utc_start, utc_end = get_tw_time_range()

        conn = get_db_connection()
        cur = conn.cursor()
        
        # SQL 查詢：加入發票相關的 5 個欄位
        query = """
            SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json,
                   customer_name, customer_phone, customer_address, scheduled_for, delivery_fee, order_type,
                   invoice_number, invoice_status, tax_id, carrier_type, carrier_num
            FROM orders 
            WHERE created_at >= %s AND created_at <= %s
            ORDER BY 
                CASE WHEN status = 'Pending' THEN 0 
                     WHEN status = 'Completed' THEN 1 
                     ELSE 2 END, 
                daily_seq ASC
        """
        try:
            cur.execute(query, (utc_start, utc_end))
        except Exception as e:
            conn.rollback() 
            print(f"SQL Fallback triggered (check_new_orders): {e}")
            # Fallback (加入發票相關的 5 個欄位)
            query_fallback = """
                SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json,
                       customer_name, customer_phone, customer_address, scheduled_for, delivery_fee, 'unknown',
                       invoice_number, invoice_status, tax_id, carrier_type, carrier_num
                FROM orders 
                WHERE created_at >= %s AND created_at <= %s
                ORDER BY status, daily_seq ASC
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
            # 解包變數 (確保變數數量擴充至 20)
            oid, table, raw_items, total, status, created, order_lang, seq_num, c_json, \
            c_name, c_phone, c_addr, c_schedule, c_fee, c_type, \
            inv_num, inv_status, tax_id, carrier_type, carrier_num = o
            
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

            # 🧾 【新增】發票資訊區塊
            inv_html = "<div style='margin-top:6px; padding-top:4px; border-top:1px dashed #ccc; font-size:0.95em; color:#475569;'>"
            
            # 處理發票狀態
            inv_status_str = str(inv_status).strip() if inv_status else 'Not Issued'
            if inv_status_str == 'Issued':
                status_badge = "<span style='color:#10b981; font-weight:bold;'>✅ 已開立</span>"
            elif inv_status_str == 'Void':
                status_badge = "<span style='color:#ef4444; font-weight:bold;'>❌ 已作廢</span>"
            else:
                status_badge = "<span style='color:#f59e0b; font-weight:bold;'>⏳ 未開立</span>"
                
            inv_num_display = str(inv_num).strip() if inv_num else "無"
            inv_html += f"<div style='margin-bottom:2px;'>🧾 發票: {inv_num_display} {status_badge}</div>"
            
            # 處理統編
            if tax_id and str(tax_id).strip():
                inv_html += f"<div style='margin-bottom:2px;'>🏢 統編: <span style='font-weight:bold; color:#0f172a;'>{tax_id}</span></div>"
                
            # 處理載具
            if carrier_type and str(carrier_type).strip():
                ctype = str(carrier_type).strip()
                cname = "載具"
                if ctype == '3': cname = "📱 手機條碼"
                elif ctype == '2': cname = "💳 自然人憑證"
                elif ctype in ['don', '4']: cname = "❤️ 愛心捐贈"
                
                cnum = str(carrier_num).strip() if carrier_num else ""
                inv_html += f"<div>{cname}: <span style='font-weight:bold; color:#0f172a;'>{cnum}</span></div>"
                
            inv_html += "</div>"
            info_html += inv_html

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
                # 👇 修改處：在 Completed 狀態下放入三個按鈕
                buttons += f"""
                <div style="display:flex; flex-direction:column; gap:5px;">
                    <button onclick='askPrintType({oid})' class='btn btn-print' style='width:100%;'>🖨️ 補印單據</button>
                    
                    <div style="display:flex; gap:5px;">
                        <button onclick='if(confirm(\"⚠️ 確定只要作廢發票，並將此單更改為【作廢狀態】嗎？\")) action(\"/kitchen/cancel/{oid}\")' class='btn' style='flex:1; background:#f44336; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;'>🗑️ 作廢訂單</button>
                        
                        <button onclick='if(confirm(\"⚠️ 確定要作廢發票並重新修改此單嗎？\")) {{ fetch(\"/kitchen/cancel/{oid}\").then(() => {{ window.open(\"/menu?edit_oid={oid}&lang=zh\", \"_blank\"); window.location.reload(); }}); }}' class='btn' style='flex:1; background:#ff9800; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;'>✏️ 作廢並修改</button>
                    </div>
                </div>
                """

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

# --- 3. 核心列印路由 (支援 80mm & 精確字體控制) ---
@kitchen_bp.route('/print_order/<int:oid>')
def print_order(oid):
    try:
        # 接收前端傳來的參數
        print_type = request.args.get('type', 'all')
        output_format = request.args.get('format', 'html')
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. 取得訂單資料
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
            cur.execute("""
                SELECT table_number, total_price, daily_seq, content_json, created_at, status,
                       customer_name, customer_phone, customer_address, delivery_fee, scheduled_for, 
                       'unknown', 'zh'
                FROM orders WHERE id=%s
            """, (oid,))
            order = cur.fetchone()

        # 2. 取得產品分類與選項對照表
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
        
        table_num, total_price, seq, content_json, created_at, status, \
        c_name, c_phone, c_addr, c_fee, c_schedule, c_type, c_lang = order
        
        order_lang = str(c_lang).lower()
        c_fee = int(c_fee or 0)
        table_str = str(table_num).strip() if table_num else ""
        c_type = str(c_type).lower() if c_type else 'unknown'
        
        has_contact = (c_phone and str(c_phone).strip() != '' and str(c_phone).lower() != 'none')
        has_addr = (c_addr and str(c_addr).strip() != '' and str(c_addr).lower() != 'none')
        has_schedule = (c_schedule and str(c_schedule).strip() != '' and str(c_schedule).lower() != 'none')
        
        def get_display_table(is_en=False):
            if c_type == 'delivery': return "🛵 Delivery" if is_en else "🛵 外送"
            if c_type == 'takeout': return "🥡 Takeout" if is_en else "🥡 自取"
            if c_type == 'dine_in': return f"Table {table_str}" if is_en else f"桌號 {table_str}"
            is_delivery = (table_str == '外送') or has_addr
            return "Delivery" if is_delivery else (table_str if table_str else "Takeout")

        if isinstance(content_json, str):
            try: items = json.loads(content_json)
            except: items = []
        else:
            items = content_json if isinstance(content_json, list) else [content_json]
        
        time_str = (created_at + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')

        noodle_items, soup_items, other_items = [], [], []
        for item in items:
            p_name = item.get('name_zh') or item.get('name')
            p_cat = product_map.get(p_name, {}).get('cat', 'Other') 
            if p_cat == 'Noodle': noodle_items.append(item)
            elif p_cat == 'Soup': soup_items.append(item)
            else: other_items.append(item)

        def translate_option(p_name, opt_str, lang_code):
            if p_name not in product_map: return opt_str
            p_data = product_map[p_name]
            found_idx = -1
            for l in ['zh', 'en', 'jp', 'kr']:
                if opt_str in p_data[l]:
                    found_idx = p_data[l].index(opt_str)
                    break
            if found_idx != -1:
                target_list = p_data.get(lang_code, [])
                if found_idx < len(target_list): return target_list[found_idx]
            return opt_str

        # --- 預覽 HTML 生成邏輯 ---
        if output_format == 'preview':
            def generate_preview_html(title, item_list, is_receipt=False, lang_override='zh'):
                if not item_list and not is_receipt: return ""
                tbl_name = get_display_table(is_en=(lang_override == 'en'))
                html = f"""
                <div style="width: 400px; background: white; padding: 20px; border: 1px solid #ddd; font-family: 'Courier New', monospace; box-shadow: 0 4px 8px rgba(0,0,0,0.1); margin: 10px;">
                    <div style="text-align: center; border-bottom: 2px solid #000; padding-bottom: 10px;">
                        <h1 style="margin: 5px 0; font-size: 2.5em;">{title}</h1>
                        <div style="font-size: 2em; font-weight: bold;"># {seq:03d}</div>
                        <div style="font-size: 1.8em;">{tbl_name}</div>
                    </div>
                    <div style="font-size: 1.2em; margin: 10px 0; line-height: 1.5;">
                        TIME: {time_str}<br>
                        {f'<span style="background: black; color: white; padding: 2px 5px;">PREORDER: {c_schedule}</span>' if has_schedule else ''}
                    </div>
                    <div style="border-top: 1px dashed #000; margin: 10px 0;"></div>
                """
                for i in item_list:
                    name_zh = i.get('name_zh') or i.get('name')
                    name_to_print = (i.get('name_en') if lang_override == 'en' else name_zh) or name_zh
                    qty = i.get('qty', 1)
                    html += f'<div style="font-weight: bold; font-size: 1.8em; display: flex; justify-content: space-between;"><span>{name_to_print}</span><span>x{qty}</span></div>'
                    raw_opts = i.get('options') or i.get('options_zh') or []
                    if not isinstance(raw_opts, list): raw_opts = [raw_opts]
                    opts_translated = [translate_option(name_zh, str(opt), lang_override) for opt in raw_opts if opt]
                    if opts_translated:
                        html += f'<div style="font-size: 1.1em; padding-left: 10px; margin-bottom: 5px; color: #333;">+ {", ".join(opts_translated)}</div>'
                    html += '<div style="border-top: 1px solid #eee; margin: 5px 0;"></div>'
                if is_receipt:
                    html += f'<div style="text-align: right; margin-top: 15px;">'
                    if c_fee > 0: html += f'運費 Fee: ${c_fee}<br>'
                    html += f'<span style="font-size: 2em; font-weight: bold;">TOTAL: ${int(total_price or 0)}</span>'
                    if c_name: html += f'<br><span style="font-size: 1.2em;">Cust: {c_name}</span>'
                    html += '</div>'
                html += "</div>"
                return html

            receipt_lang = 'en' if order_lang == 'en' else 'zh'
            preview_content = '<div style="display: flex; flex-wrap: wrap; justify-content: center; background: #f4f4f4; min-height: 100vh; padding: 20px;">'
            if print_type in ['all', 'receipt']:
                preview_content += generate_preview_html("結帳單", items, is_receipt=True, lang_override=receipt_lang)
            if print_type in ['all', 'kitchen']:
                if noodle_items: preview_content += generate_preview_html("廚房單-麵區", noodle_items)
                if soup_items: preview_content += generate_preview_html("廚房單-湯區", soup_items)
                if other_items: preview_content += generate_preview_html("廚房單-其他", other_items)
            preview_content += '</div>'
            return render_template_string(preview_content)

        # --- 3. 核心 ESC/POS 生成函數 (80mm & 獨立字體控制) ---
        def generate_content(title, item_list, is_receipt=False, lang_override='zh'):
            if not item_list and not is_receipt: return b""
            
            ESC, GS = b'\x1b', b'\x1d'
            RESET = ESC + b'@'
            BOLD_ON, BOLD_OFF = ESC + b'E\x01', ESC + b'E\x00'
            CENTER, LEFT = ESC + b'a\x01', ESC + b'a\x00'
            CUT = GS + b'V\x42\x00'
            ENCODE = 'big5-hkscs' # 確保印表機支援此編碼，或使用 'gb18030'
            
            # 字體大小設定
            SIZE_X22 = GS + b'!\x22'     # 3x3 標題
            SIZE_X11 = GS + b'!\x11'     # 2x2 重要資訊
            SIZE_X01 = GS + b'!\x01'     # 1x2 拉高字體 (適合閱讀)
            SIZE_NORM = GS + b'!\x00'    # 標準
            
            res = RESET + CENTER
            
            # 1. 標題與序號
            res += SIZE_X22 + BOLD_ON + title.encode(ENCODE, 'replace') + b"\n"
            res += SIZE_X11 + f"NO: #{seq:03d}\n".encode(ENCODE)
            
            # 2. 桌號 / 訂單類型
            tbl_name = get_display_table(is_en=(lang_override == 'en'))
            res += BOLD_ON + tbl_name.encode(ENCODE, 'replace') + b"\n" + BOLD_OFF
            
            # 3. 基礎資訊區 (靠左)
            res += LEFT + SIZE_X01
            res += f"訂單時間: {time_str}\n".encode(ENCODE)
            
            # --- 這裡加入所有資料庫欄位的判斷 ---
            if has_schedule:
                res += BOLD_ON + f"取單時間: {c_schedule}\n".encode(ENCODE) + BOLD_OFF + SIZE_X01
            
            if is_receipt:
                if c_name:
                    res += f"姓名: {c_name}\n".encode(ENCODE, 'replace') + SIZE_X01
                if has_contact:
                    res += f"電話: {c_phone}\n".encode(ENCODE) + SIZE_X01
                if has_addr:
                    # 地址通常較長，使用標準大小避免跑版
                    res += SIZE_X01 + f"地址: {c_addr}\n".encode(ENCODE, 'replace')
        
            # 分隔線
            res += SIZE_NORM + b"-"*48 + b"\n"
            
            # 4. 商品清單
            for i in item_list:
                name_zh = i.get('name_zh') or i.get('name')
                name_to_print = (i.get('name_en') if lang_override == 'en' else name_zh) or name_zh
                qty = i.get('qty', 1)
                
                # 商品名稱 (放大)
                res += SIZE_X11 + BOLD_ON + f"{name_to_print} x{qty}\n".encode(ENCODE, 'replace') + BOLD_OFF
                
                # 客製化選項 (拉高)
                raw_opts = i.get('options') or i.get('options_zh') or []
                if not isinstance(raw_opts, list): raw_opts = [raw_opts]
                opts_translated = [translate_option(name_zh, str(opt), lang_override) for opt in raw_opts if opt]
                
                if opts_translated:
                    opt_str = " + " + ", ".join(opts_translated)
                    res += SIZE_X01 + f"{opt_str}\n".encode(ENCODE, 'replace')
                
                # 商品間分隔線
                res += SIZE_NORM + b"-"*48 + b"\n"
            
            # 5. 結帳區 (僅收據)
            if is_receipt:
                res += LEFT + SIZE_X01
                if c_fee > 0:
                    res += b"\n"
                
                # 總價放大
                label_total = "TOTAL: " if lang_override == 'en' else "總計: "
                res += SIZE_X22 + BOLD_ON + f"{label_total}${int(total_price or 0)}\n".encode(ENCODE) + BOLD_OFF
                
                # 底部備註 (如果是外送單，再次強調地址)
                if has_addr:
                    res += SIZE_NORM + b"*"*48 + b"\n"
                    res += f"Deliver to: {c_addr}\n".encode(ENCODE, 'replace')
        
            res += b"\n" + CUT # 少給一點空白
            return res

            
        # 4. 輸出處理 (Base64)
        if output_format == 'base64':
            # 初始化指令：重置 + 進入中文模式 + 設定字體代碼頁
            init_cmds = b'\x1b\x40\x1c\x26\x1b\x74\x0d'
            tasks = {}
            receipt_lang = 'en' if order_lang == 'en' else 'zh'
            receipt_title = "Receipt" if receipt_lang == 'en' else "結帳單"
            kitchen_lang = 'zh'
            
            if print_type in ['all', 'receipt']:
                tasks["receipt"] = base64.b64encode(
                    init_cmds + generate_content(receipt_title, items, is_receipt=True, lang_override=receipt_lang)
                ).decode('utf-8')
            
            if print_type in ['all', 'kitchen']:
                if noodle_items:
                    tasks["noodle"] = base64.b64encode(
                        init_cmds + generate_content("廚房單-麵區", noodle_items, lang_override=kitchen_lang)
                    ).decode('utf-8')
                if soup_items:
                    tasks["soup"] = base64.b64encode(
                        init_cmds + generate_content("廚房單-湯區", soup_items, lang_override=kitchen_lang)
                    ).decode('utf-8')
                if other_items:
                    tasks["other"] = base64.b64encode(
                        init_cmds + generate_content("廚房單-其他", other_items, lang_override=kitchen_lang)
                    ).decode('utf-8')
            
            return jsonify({"status": "success", "tasks": tasks})

        return "HTML Preview Mode (Not Base64)", 200

    except Exception as e:
        traceback.print_exc()
        return f"Print Error: {str(e)}", 500
        

        
# --- 4. 狀態變更 (完成/作廢/列印) ---

@kitchen_bp.route('/complete/<int:oid>')
@login_required          # 🛡️ 防護 1：必須登入
def complete_order(oid):
    try:
        c = get_db_connection()
        # 🛠️ 修正這裡：使用 psycopg2 的 RealDictCursor 取代 dictionary=True
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) 

        # 1. 取得訂單詳細資訊
        cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
        order = cur.fetchone()

        if not order:
            return "Order not found", 404

        # 2. 更新訂單狀態為 Completed
        cur.execute("UPDATE orders SET status='Completed' WHERE id=%s", (oid,))
        c.commit()

        # 3. 觸發綠界開立發票 (僅限發票尚未開立的情況)
        if order.get('invoice_status') in ['Not Issued', None]:
            try:
                # 傳入訂單資料 (此時 order 字典內已包含 tax_id, carrier_type 等欄位)
                invoice_res = issue_ecpay_invoice(order)
                
                if invoice_res.get('success'):
                    # 取得綠界回傳的發票號碼
                    invoice_no = invoice_res.get('invoice_no')
                    print(f"[{get_current_time_str()}] 🧾 發票開立成功: {invoice_no}")
                    
                    # 📝 將發票號碼存回資料庫，並將狀態改為 'Issued'
                    cur.execute("""
                        UPDATE orders 
                        SET invoice_number=%s, invoice_status='Issued' 
                        WHERE id=%s
                    """, (invoice_no, oid))
                    c.commit()
                else:
                    print(f"[{get_current_time_str()}] ⚠️ 發票開立失敗: {invoice_res.get('message')}")
                    
            except Exception as invoice_e:
                print(f"[{get_current_time_str()}] ❌ 發票系統例外錯誤: {invoice_e}")

        c.close() 
        print(f"[{get_current_time_str()}] ✅ 訂單完成: ID {oid}")
        return "OK"

    except Exception as e:
        print(f"Error completing order: {e}")
        return "Error", 500


@kitchen_bp.route('/cancel/<int:oid>')
@login_required
def cancel_order(oid):
    try:
        c = get_db_connection()
        # 🛠️ 修正這裡：使用 psycopg2 的 RealDictCursor 取代 dictionary=True
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 1. 先取得訂單資訊
        cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
        order = cur.fetchone()

        if not order:
            return "Order not found", 404

        # 2. 更新訂單狀態為 Cancelled
        cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (oid,))
        c.commit()

        # 3. 觸發綠界發票作廢
        invoice_no = order.get('invoice_number') 
        invoice_status = order.get('invoice_status')
        
        # 只有在「有發票號碼」且「發票狀態為已開立(Issued)」時才呼叫作廢 API
        if invoice_no and invoice_status == 'Issued':
            try:
                void_res = invalid_ecpay_invoice(invoice_no, f"訂單 {oid} 取消作廢")
                
                if void_res.get('success'):
                    print(f"[{get_current_time_str()}] 🗑️ 發票作廢成功: {invoice_no}")
                    
                    # 📝 更新資料庫的發票狀態為 'Void' (已作廢)
                    cur.execute("""
                        UPDATE orders 
                        SET invoice_status='Void' 
                        WHERE id=%s
                    """, (oid,))
                    c.commit()
                else:
                    print(f"[{get_current_time_str()}] ⚠️ 發票作廢失敗: {void_res.get('message')}")
                    
            except Exception as void_e:
                print(f"[{get_current_time_str()}] ❌ 發票作廢系統例外錯誤: {void_e}")

        c.close() 
        print(f"[{get_current_time_str()}] 🗑️ 訂單作廢: ID {oid}")
        return "OK"

    except Exception as e:
        print(f"Error cancelling order: {e}")
        return "Error", 500


# 👇 新增：列印發票的專屬路由
@kitchen_bp.route('/print_invoice/<invoice_no>')
@login_required
def print_invoice_route(invoice_no):
    """
    接收前端的列印請求，向綠界索取發票 HTML 並回傳給瀏覽器
    """
    try:
        print(f"[{get_current_time_str()}] 🖨️ 準備列印發票: {invoice_no}")
        
        # 呼叫 ecpay_invoice.py 裡的函數
        result = print_ecpay_invoice(invoice_no)
        
        if result.get("success"):
            # 綠界成功回傳 HTML，我們直接把它吐給瀏覽器
            return result.get("html")
        else:
            # 如果失敗，回傳簡單的錯誤畫面
            error_msg = result.get("message")
            return f"<h1>列印失敗</h1><p>綠界回傳錯誤: {error_msg}</p>", 400
            
    except Exception as e:
        print(f"Error printing invoice: {e}")
        return f"<h1>系統錯誤</h1><p>{str(e)}</p>", 500

        
# --- 5. 銷售排名 API ---
@kitchen_bp.route('/sales_ranking')
@login_required          # 🛡️ 防護 1：必須登入
@role_required('admin')  # 🛡️ 防護 2：必須是 admin 才能進後台
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
@login_required          # 🛡️ 防護 1：必須登入
@role_required('admin')  # 🛡️ 防護 2：必須是 admin 才能進後台
def daily_report():
    # --- 1. 時間處理 (台灣時區 UTC+8) ---
    now_tw = datetime.utcnow() + timedelta(hours=8)
    target_date_str = request.args.get('date') or now_tw.strftime('%Y-%m-%d')
    
    # 取得資料庫查詢範圍
    try:
        utc_start, utc_end = get_tw_time_range(target_date_str)
    except:
        utc_start, utc_end = now_tw.replace(hour=0, minute=0), now_tw.replace(hour=23, minute=59)

    output_format = request.args.get('format', 'html')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # --- 2. 取得產品數據 ---
    cur.execute("SELECT name, price FROM products")
    price_map = {row[0]: row[1] for row in cur.fetchall()}
    
    # 有效訂單
    cur.execute("SELECT total_price, content_json FROM orders WHERE created_at >= %s AND created_at <= %s AND status IN ('Pending', 'Completed')", (utc_start, utc_end))
    v_raw = cur.fetchall()
    v_count, v_total = len(v_raw), sum([r[0] for r in v_raw if r[0]])

    # 作廢訂單
    cur.execute("SELECT total_price, content_json FROM orders WHERE created_at >= %s AND created_at <= %s AND status = 'Cancelled'", (utc_start, utc_end))
    x_raw = cur.fetchall()
    x_count, x_total = len(x_raw), sum([r[0] for r in x_raw if r[0]])
    conn.close()

    def agg(rows):
        result = {}
        for r in rows:
            if not r[1]: continue
            try:
                items = json.loads(r[1]) if isinstance(r[1], str) else r[1]
                for i in items:
                    name = i.get('name_zh', i.get('name', '商品'))
                    qty = int(float(i.get('qty', 1)))
                    p_val = i.get('price')
                    price = int(float(p_val)) if p_val is not None else price_map.get(name, 0)
                    if name not in result: result[name] = {'qty':0, 'amt':0}
                    result[name]['qty'] += qty
                    result[name]['amt'] += (qty * price)
            except: continue
        return result

    v_stats = agg(v_raw)
    x_stats = agg(x_raw)

    # --- 3. 生成 ESC/POS 二進制 (所有文字放大至 x11) ---
    if output_format == 'blob':
        ESC, GS = b'\x1b', b'\x1d'
        ENCODE = 'cp950'
        SIZE_LARGE = GS + b'!\x11' # 倍寬倍高 (x11)
        
        res = ESC + b'@' # 初始化
        res += SIZE_LARGE # 設定全域最小尺寸為 x11
        
        # 標題區 (置中)
        res += ESC + b'a\x01'
        res += "日結營收報表\n".encode(ENCODE)
        res += f"{target_date_str}\n".encode(ENCODE)
        res += f"時間:{now_tw.strftime('%H:%M:%S')}\n".encode(ENCODE)
        res += b"="*16 + b"\n"  # 字體變大，分隔線縮短為 16 個
        
        # 有效營收 (靠左)
        res += b"\n" + ESC + b'a\x00'
        res += ESC + b'E\x01' + "有效營收\n".encode(ENCODE) + ESC + b'E\x00'
        res += f"單數: {v_count}\n".encode(ENCODE)
        res += f"總計: ${v_total:,}\n".encode(ENCODE)
        res += b"\n" + ESC + b'a\x01' + b"-"*20 + b"\n"
        
        # 作廢統計
        res += ESC + b'a\x00'
        res += ESC + b'E\x01' + "作廢統計\n".encode(ENCODE) + ESC + b'E\x00'
        res += f"單數: {x_count}\n".encode(ENCODE)
        res += f"額度: ${x_total:,}\n".encode(ENCODE)
        res += ESC + b'a\x01' + b"="*16 + b"\n"
        
        # 商品銷售明細 (字大，建議名稱與數據分行或截短)
        res += b"\n" + ESC + b'a\x00'
        res += ESC + b'E\x01' + "銷售明細\n".encode(ENCODE) + ESC + b'E\x00'
        if not v_stats:
            res += "無\n".encode(ENCODE)
        else:
            for k, v in sorted(v_stats.items(), key=lambda x:x[1]['qty'], reverse=True):
                # 因為字體大，採「名稱」一行，「數量金額」一行
                res += f"{k[:16]}\n".encode(ENCODE, 'replace')
                res += f"  x{v['qty']:>2} ${v['amt']:,}\n".encode(ENCODE)
        res += b"\n" + ESC + b'a\x01' + b"-"*20 + b"\n"
        
        # 作廢商品明細
        res += ESC + b'a\x00'
        res += ESC + b'E\x01' + "作廢明細\n".encode(ENCODE) + ESC + b'E\x00'
        if not x_stats:
            res += "無\n".encode(ENCODE)
        else:
            for k, v in sorted(x_stats.items(), key=lambda x:x[1]['qty'], reverse=True):
                res += f"{k[:16]}\n".encode(ENCODE, 'replace')
                res += f"  x{v['qty']:>2} ${v['amt']:,}\n".encode(ENCODE)
        res += b"\n" + ESC + b'a\x01' + b"="*16 + b"\n"
        
        # 簽名區
        res += b"\n" + ESC + b'a\x00'
        res += "經手人簽名:\n\n\n".encode(ENCODE)
        res += "________________\n".encode(ENCODE)
        res += "- End Report -\n\n".encode(ENCODE)
        res += b"\n\n\n" + GS + b'V\x42\x00' # 切刀
        
        return jsonify({"status": "success", "blob": base64.b64encode(res).decode('utf-8')})

   # --- 4. HTML 頁面渲染 ---
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>日結報表_{target_date_str}</title>
        <style>
            body {{ 
                font-family: "Microsoft JhengHei", sans-serif; 
                background: #eee; 
                display: flex; 
                flex-direction: column; 
                align-items: center; 
                padding: 20px; 
            }}
            .ticket {{ 
                background: white; 
                width: 80mm; 
                padding: 20px; 
                text-align: center; 
                border: 1px solid #ccc; 
                box-sizing: border-box; 
                box-shadow: 0 4px 10px rgba(0,0,0,0.1);
            }}
            
            /* 按鈕容器 */
            .no-print {{ 
                margin-bottom: 20px; 
                display: flex; 
                align-items: center; 
                justify-content: center; 
                gap: 15px; 
                flex-wrap: wrap;
            }}
            
            /* 日期選擇器樣式優化 */
            #dateInput {{
                padding: 8px;
                border-radius: 6px;
                border: 1px solid #ccc;
                font-size: 16px;
                height: 40px;
                box-sizing: border-box;
            }}
            
            /* 基礎按鈕樣式 - 確保所有按鈕結構完全一致 */
            .btn-base {{ 
                height: 40px;
                padding: 0 25px; 
                font-weight: bold; 
                font-size: 16px;
                cursor: pointer; 
                border-radius: 8px; 
                border: none; 
                transition: all 0.2s ease;
                text-decoration: none;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                outline: none;
                white-space: nowrap;
            }}

            .btn-base:active {{ transform: translateY(1px); box-shadow: none; }}
            .btn-base:hover {{ opacity: 0.9; box-shadow: 0 4px 8px rgba(0,0,0,0.15); }}

            /* 列印報表按鈕 - 綠色 */
            .btn-print {{ background: #27ae60; color: white; }}

            /* 返回看板按鈕 - 深灰色 (樣式已與列印按鈕完全對齊) */
            .btn-close {{ background: #555; color: white; }}

            .detail-list {{ font-size: 13px; text-align: left; line-height: 1.6; }}
            .section-title {{ text-align: left; border-bottom: 1px solid #000; margin-top: 15px; font-weight: bold; }}
            .line-divider {{ margin: 10px 0; overflow: hidden; white-space: nowrap; }}

            /* 列印時隱藏不需要的元件 */
            @media print {{
                .no-print, #usbStatus {{ display: none; }}
                body {{ background: white; padding: 0; }}
                .ticket {{ border: none; box-shadow: none; width: 100%; }}
            }}
        </style>
    </head>
    <body onload="autoConnectUSB()">
        <div class="no-print">
            <input type="date" id="dateInput" value="{target_date_str}" onchange="location.href='?date='+this.value">
            <button id="btnPrint" class="btn-base btn-print" onclick="handlePrintClick()">🖨️ 列印報表</button>
            <button class="btn-base btn-close" onclick="window.close()">🔙 返回看板</button>
        </div>
        
        <div id="usbStatus" style="font-size:12px; margin-bottom:15px; color:#666; font-weight: bold;">偵測印表機中...</div>

        <div class="ticket">
            <h2 style="margin:0;">日結營收報表</h2>
            <div style="font-size:14px;">{target_date_str}</div>
            <div style="font-size:12px;">列印時間: {now_tw.strftime('%H:%M:%S')}</div>
            <div class="line-divider">==========================</div>
            
            <div style="text-align:left;"><b>有效營收</b></div>
            <div style="text-align:left;">訂單: {v_count} 單  總計: ${v_total:,}</div>
            <div class="line-divider">--------------------------</div>
            
            <div style="text-align:left;"><b>作廢統計</b></div>
            <div style="text-align:left;">作廢: {x_count} 單  作廢額: ${x_total:,}</div>
            <div class="line-divider">==========================</div>
            
            <div class="section-title">商品銷售明細</div>
            <div class="detail-list">
                {"".join([f"<div>{k} x{v['qty']} ${v['amt']:,}</div>" for k, v in v_stats.items()]) if v_stats else "<div>無</div>"}
            </div>
            
            <div class="line-divider">--------------------------</div>
            <div class="section-title">作廢商品明細</div>
            <div class="detail-list">
                {"".join([f"<div>{k} x{v['qty']} ${v['amt']:,}</div>" for k, v in x_stats.items()]) if x_stats else "<div>無</div>"}
            </div>
            <div class="line-divider">==========================</div>
            
            <br><br><div>經手人簽名</div><br><br>
            <div>____________________</div>
            <div style="font-size:12px; margin-top:10px;">- End of Report -</div>
        </div>

        <script>
            let device = null;

            // --- 重要修復：關閉或離開頁面時釋放 USB 資源 ---
            window.addEventListener('beforeunload', async () => {{
                if (device && device.opened) {{
                    try {{
                        await device.releaseInterface(device.configuration.interfaces[0].interfaceNumber);
                        await device.close();
                        console.log("USB 資源已釋放");
                    }} catch (err) {{
                        console.error("釋放失敗", err);
                    }}
                }}
            }});

            async function autoConnectUSB() {{
                const statusDiv = document.getElementById('usbStatus');
                try {{
                    const devices = await navigator.usb.getDevices();
                    if (devices.length > 0) {{
                        device = devices[0];
                        if (!device.opened) await device.open();
                        await device.selectConfiguration(1);
                        await device.claimInterface(device.configuration.interfaces[0].interfaceNumber);
                        statusDiv.innerText = "✅ 已自動連接: " + (device.productName || "USB 印表機");
                        statusDiv.style.color = "green";
                    }} else {{
                        statusDiv.innerText = "ℹ️ 尚未授權印表機，請點擊列印按鈕進行選取";
                    }}
                }} catch (err) {{
                    statusDiv.innerText = "⚠️ 連線異常: " + err.message;
                    statusDiv.style.color = "red";
                }}
            }}

            async function handlePrintClick() {{
                const statusDiv = document.getElementById('usbStatus');
                if (!device) {{
                    try {{
                        device = await navigator.usb.requestDevice({{ filters: [] }});
                        await device.open();
                        await device.selectConfiguration(1);
                        await device.claimInterface(device.configuration.interfaces[0].interfaceNumber);
                        statusDiv.innerText = "✅ 已連線: " + device.productName;
                        statusDiv.style.color = "green";
                    }} catch (e) {{ 
                        return alert("未選擇或無法使用裝置: " + e.message); 
                    }}
                }}

                try {{
                    const date = document.getElementById('dateInput').value;
                    const res = await fetch(`/kitchen/report?date=${{date}}&format=blob`);
                    if (!res.ok) throw new Error("後端產生報表失敗");
                    
                    const data = await res.json();
                    if (!data.blob) throw new Error("未收到列印數據");
                    
                    const binaryString = window.atob(data.blob);
                    const bytes = new Uint8Array(binaryString.length);
                    for (let i = 0; i < binaryString.length; i++) {{
                        bytes[i] = binaryString.charCodeAt(i);
                    }}

                    // 自動尋找 OUT 節點
                    const interface = device.configuration.interfaces[0];
                    const endpoint = interface.alternate.endpoints.find(e => e.direction === 'out').endpointNumber;
                    
                    await device.transferOut(endpoint, bytes);
                    statusDiv.innerText = "✨ 報表列印中...";
                    setTimeout(() => {{ 
                        statusDiv.innerText = "✅ 已連線: " + (device.productName || "USB 印表機"); 
                    }}, 3000);

                }} catch (err) {{
                    alert("列印失敗: " + err.message);
                    console.error(err);
                }}
            }}
        </script>
    </body>
    </html>
    """






