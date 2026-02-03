from flask import Blueprint, render_template, request, jsonify
import json
# import logging  <-- [移除] 不再需要 logging 模組
from datetime import datetime, timedelta
from database import get_db_connection

# [移除] logging.basicConfig 設定
# 現在直接使用 print 輸出到 Console

kitchen_bp = Blueprint('kitchen', __name__)

# --- 輔助函式：取得當前台灣時間字串 (用於 Log) ---
def get_current_time_str():
    # 取得現在時間 (UTC+8) 並格式化
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

# --- 輔助函式：計算台灣時間範圍 ---
def get_tw_time_range(target_date_str=None, end_date_str=None):
    """
    計算台灣時間的 UTC 起始與結束範圍 (用於資料庫查詢)。
    資料庫存 UTC，但查詢依據是台灣的「天」。
    """
    try:
        if target_date_str:
            # 使用者指定日期
            tw_start = datetime.strptime(target_date_str, '%Y-%m-%d')
        else:
            # 預設為現在 (台灣時間)
            tw_start = datetime.utcnow() + timedelta(hours=8)
        
        # 設定為當天 00:00:00
        tw_start = tw_start.replace(hour=0, minute=0, second=0, microsecond=0)

        if end_date_str:
            tw_end = datetime.strptime(end_date_str, '%Y-%m-%d')
        else:
            tw_end = tw_start
        
        # 設定為當天 23:59:59
        tw_end = tw_end.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # 轉回 UTC 供資料庫查詢 (台灣時間 - 8小時)
        return tw_start - timedelta(hours=8), tw_end - timedelta(hours=8)
    except:
        # 發生錯誤時的 fallback
        now = datetime.utcnow() + timedelta(hours=8)
        return now.replace(hour=0, minute=0, second=0) - timedelta(hours=8), \
               now.replace(hour=23, minute=59, second=59) - timedelta(hours=8)


# --- 1. 廚房看板主頁 ---
@kitchen_bp.route('/')
def kitchen_panel():
    return render_template('kitchen.html')


# --- 2. 檢查新訂單 API (核心功能：更新畫面 + 自動列印偵測) ---
@kitchen_bp.route('/check_new_orders')
def check_new_orders():
    # 前端傳來它目前已知的最大序號 (若剛載入頁面則為 0)
    current_max = request.args.get('current_seq', 0, type=int)
    
    # 取得今日時間範圍
    utc_start, utc_end = get_tw_time_range()

    conn = get_db_connection()
    cur = conn.cursor()
    
    # A. 查詢當日所有訂單 (用於刷新看板 HTML)
    # 排序：Pending (0) -> Completed (1) -> Cancelled (2)，同狀態依序號倒序
    query = """
        SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s
        ORDER BY 
            CASE WHEN status = 'Pending' THEN 0 
                 WHEN status = 'Completed' THEN 1 
                 ELSE 2 END, 
            daily_seq DESC
    """
    cur.execute(query, (utc_start, utc_end))
    orders = cur.fetchall()
    
    # B. 取得目前資料庫中的「最大每日流水號」
    cur.execute("SELECT MAX(daily_seq) FROM orders WHERE created_at >= %s AND created_at <= %s", (utc_start, utc_end))
    res_max = cur.fetchone()
    max_seq_val = res_max[0] if res_max and res_max[0] else 0
    
    # C. 偵測需要「自動列印」的新訂單 ID
    # 條件：前端已知序號 > 0 (非首次載入) 且 資料庫序號 > 前端已知序號
    new_order_ids = []
    if current_max > 0 and max_seq_val > current_max:
        cur.execute("SELECT id, daily_seq FROM orders WHERE daily_seq > %s AND created_at >= %s", (current_max, utc_start))
        new_orders_data = cur.fetchall()
        new_order_ids = [r[0] for r in new_orders_data]

        # [修改] 改用 print 輸出 Log
        if new_order_ids:
            seq_list = [f"#{r[1]}" for r in new_orders_data]
            now = get_current_time_str()
            print(f"[{now}] 🔔 偵測到新訂單，準備觸發列印: {', '.join(seq_list)} (IDs: {new_order_ids})")
    
    conn.close()

    # D. 生成 HTML 卡片內容
    html_content = ""
    if not orders: 
        html_content = "<div id='loading-msg' style='grid-column:1/-1;text-align:center;padding:100px;font-size:1.5em;color:#888;'>🍽️ 目前沒有訂單</div>"
    
    for o in orders:
        oid, table, raw_items, total, status, created, order_lang, seq_num, c_json = o
        status_cls = status.lower() # 'pending', 'completed', 'cancelled'
        tw_time = created + timedelta(hours=8)
        
        # 解析 JSON 品項
        items_html = ""
        try:
            cart = json.loads(c_json) if c_json else []
            for item in cart:
                name = item.get('name_zh', item.get('name', '商品'))
                qty = item.get('qty', 1)
                options = item.get('options_zh', item.get('options', []))
                opts_html = f"<div class='item-opts' style='font-size:0.85em; color:#666;'>└ {' / '.join(options)}</div>" if options else ""
                items_html += f"<div class='item-row' style='margin-bottom:5px; border-bottom:1px solid #eee; padding-bottom:3px;'><div class='item-name' style='display:flex; justify-content:space-between; font-weight:bold;'><span>{name}</span><span class='item-qty'>x{qty}</span></div>{opts_html}</div>"
        except: 
            items_html = "<div class='item-row'>資料解析錯誤</div>"

        formatted_total = f"{int(total)}" 
        buttons = ""

        # 根據狀態生成按鈕
        if status == 'Pending':
            buttons += f"""
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding:0 5px;">
                    <span style="font-size:14px; color:#666; font-weight:bold;">應收總計:</span>
                    <span style="font-size:22px; color:#d32f2f; font-weight:900;">${formatted_total}</span>
                </div>
            """
            buttons += f"<button onclick='action(\"/kitchen/complete/{oid}\")' class='btn btn-main' style='width:100%; background:#28a745; color:white; border:none; padding:10px; border-radius:5px; font-weight:bold; cursor:pointer;'>✅ 出餐 / 付款</button>"
            buttons += f"""<div class="btn-group" style="margin-top:8px; display:flex; gap:5px;">
                <button onclick='askPrintType({oid})' class='btn btn-print' style='flex:1; padding:8px;'>🖨️ 列印</button>
                <a href='/menu?edit_oid={oid}&lang=zh' target='_blank' class='btn' style='flex:1; background:#ff9800; color:white; text-decoration:none; text-align:center; padding:8px; border-radius:4px;'>✏️ 修改</a>
                <button onclick='if(confirm(\"⚠️ 確定作廢此單？\")) action(\"/kitchen/cancel/{oid}\")' class='btn btn-void' style='background:#f44336; color:white; border:none; padding:8px; border-radius:4px;'>🗑️</button>
            </div>"""
        elif status == 'Cancelled':
            buttons += f"<div style='text-align:center; color:#d32f2f; font-weight:bold; margin-bottom:5px;'>【此單已作廢】</div>"
            buttons += f"<button onclick='askPrintType({oid})' class='btn btn-print' style='width:100%; padding:8px; opacity:0.6;'>🖨️ 補印作廢單</button>"
        else: # Completed
            buttons += f"""
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding:0 5px; opacity:0.7;">
                    <span style="font-size:13px; color:#666;">實收總計:</span>
                    <span style="font-size:18px; color:#333; font-weight:bold;">${formatted_total}</span>
                </div>
            """
            buttons += f"<button onclick='askPrintType({oid})' class='btn btn-print' style='width:100%; padding:10px;'>🖨️ 補印單據</button>"

        # 組合單張卡片 HTML
        html_content += f"""
        <div class="card {status_cls}" data-id="{oid}" style="background:white; border:1px solid #ddd; border-radius:8px; padding:15px; box-shadow:0 2px 5px rgba(0,0,0,0.1);">
            <div class="card-header" style="display:flex; justify-content:space-between; align-items:start; margin-bottom:10px; border-bottom:2px solid #eee; padding-bottom:5px;">
                <div><div class="seq-num" style="font-size:1.5em; font-weight:900;">#{seq_num:03d}</div><div class="time-stamp" style="font-size:0.8em; color:#666;">{tw_time.strftime('%H:%M')} ({order_lang})</div></div>
                <div class="table-num" style="background:#333; color:white; padding:4px 10px; border-radius:4px; font-weight:bold;">桌號 {table}</div>
            </div>
            <div class="items" style="min-height:80px;">{items_html}</div>
            <div class="actions" style="margin-top:15px;">{buttons}</div>
        </div>"""
        
    # 回傳 JSON 供前端 JS 使用
    return jsonify({
        'html': html_content, 
        'max_seq': max_seq_val, 
        'new_ids': new_order_ids
    })


# --- 3. 補印功能 (整合分區列印 - 80mm Auto長度版 - 桌號優化) ---
@kitchen_bp.route('/print_order/<int:oid>')
def print_order(oid):
    # 參數 type: 'all' (預設), 'kitchen', 'receipt'
    print_type = request.args.get('type', 'all')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT table_number, total_price, daily_seq, content_json, created_at, status FROM orders WHERE id=%s", (oid,))
    order = cur.fetchone()
    
    if not order:
        conn.close()
        now = get_current_time_str()
        print(f"[{now}] ⚠️ 列印失敗：找不到訂單 ID {oid}")
        return "訂單不存在", 404
    
    table_num, total_price, seq, content_json, created_at, status = order
    items = json.loads(content_json) if content_json else []
    
    # 時間調整
    time_str = (created_at + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')

    now = get_current_time_str()
    print(f"[{now}] 🖨️ 列印生成 | 序號: #{seq:03d} | 桌號: {table_num} | 模式: {print_type} | ID: {oid}")

    # 取得產品分類對照表
    cur.execute("SELECT name, print_category FROM products")
    product_map = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()

    # 分類邏輯
    noodle_items, soup_items, other_items = [], [], []
    for item in items:
        p_name = item.get('name_zh') or item.get('name')
        p_cat = product_map.get(p_name, 'Noodle') # 預設為麵區
        
        if p_cat == 'Noodle': noodle_items.append(item)
        elif p_cat == 'Soup': soup_items.append(item)
        else: other_items.append(item)

    # --- CSS 樣式設定 (80mm Auto Height) ---
    # 關鍵修改：
    # 1. @page { size: 80mm auto; } 讓瀏覽器知道高度是自動的
    # 2. body { height: auto; } 允許內容撐開
    # 3. .ticket { padding-bottom: 20px; } 底部留白防止切刀切到文字
    style = """
    <style>
        @page {
            size: 80mm auto;  /* 寬度固定，高度自動 */
            margin: 0mm;      /* 移除瀏覽器預設頁面邊距 */
        }
        body { 
            font-family: 'Microsoft JhengHei', sans-serif; 
            width: 78mm;      /* 設定內容寬度略小於 80mm 防止溢出 */
            height: auto;     /* 高度自動 */
            margin: 0 auto; 
            padding: 2px; 
            color: #000;
            background: #fff;
        }
        .ticket { 
            width: 100%;
            display: block;
            border-bottom: 3px dashed #000; 
            padding: 10px 0 30px 0; /* 底部增加 Padding 防止切刀切到字 */
            margin-bottom: 10px;
            page-break-after: always; /* 每張單據後強制換頁(切刀) */
            position: relative; 
            box-sizing: border-box;
        }
        .ticket:last-child {
            page-break-after: auto; /* 最後一張不一定要強制換頁，視驅動而定 */
        }
        .void-watermark { 
            position: absolute; top: 30%; left: 5%; 
            font-size: 50px; color: rgba(0,0,0,0.2); 
            transform: rotate(-30deg); border: 5px solid rgba(0,0,0,0.2); 
            padding: 10px; z-index: 100; pointer-events: none; font-weight: 900;
        }
        .head { text-align: center; margin-bottom: 10px; }
        .head h2 { font-size: 22px; margin: 0; background: #000; color: #fff; padding: 5px; border-radius: 4px; -webkit-print-color-adjust: exact; }
        .head h1 { font-size: 48px; margin: 5px 0; line-height: 1; }
        
        /* 桌號區塊優化 */
        .info-box { border-bottom: 3px solid #000; padding-bottom: 5px; margin-bottom: 10px; }
        .table-row { display: flex; justify-content: center; align-items: baseline; gap: 15px; }
        .table-label { font-size: 24px; font-weight: bold; }
        .table-val { font-size: 42px; font-weight: 900; line-height: 1; }
        .time-row { font-size: 14px; text-align: center; margin-top: 5px; }

        /* 品項樣式 */
        .item-row { display: flex; justify-content: space-between; align-items: flex-start; margin-top: 10px; line-height: 1.2; }
        .item-name { font-size: 24px; font-weight: 900; width: 85%; word-wrap: break-word; } /* 防止長字串撐破 */
        .item-qty { font-size: 24px; font-weight: 900; white-space: nowrap; }
        .opt { font-size: 18px; font-weight: bold; color: #000; padding-left: 15px; margin-top: 2px; margin-bottom: 5px; }

        .total { text-align: right; font-size: 24px; font-weight: 900; margin-top: 15px; padding-top: 10px; border-top: 2px solid #000; }
    </style>
    """

    def generate_html(title, item_list, is_receipt=False):
        if not item_list: return ""
        
        void_mark = "<div class='void-watermark'>作廢單</div>" if status == 'Cancelled' else ""
        
        h = f"<div class='ticket'>{void_mark}<div class='head'><h2>{title}</h2><h1>#{seq:03d}</h1></div>"
        
        h += f"""
        <div class='info-box'>
            <div class='table-row'>
                <span class='table-label'>桌號 Table</span>
                <span class='table-val'>{table_num}</span>
            </div>
            <div class='time-row'>{time_str}</div>
        </div>
        """
        
        for i in item_list:
            name = i.get('name_zh') or i.get('name')
            qty = i.get('qty', 1)
            opts = i.get('options_zh') or i.get('options', [])
            
            h += f"<div class='item-row'><span class='item-name'>{name}</span><span class='item-qty'>x{qty}</span></div>"
            if opts:
                h += f"<div class='opt'>└ {', '.join(opts)}</div>"
        
        if is_receipt: 
            h += f"<div class='total'>總計 Total: ${int(total_price)}</div>"
            
        return h + "</div>"

    content = ""
    if print_type == 'receipt': 
        content = generate_html("結帳單 Receipt", items, is_receipt=True)
    elif print_type == 'kitchen':
        content += generate_html("廚房單 - 麵區", noodle_items)
        content += generate_html("廚房單 - 湯區", soup_items)
        content += generate_html("廚房單 - 其他", other_items)
    else: # all (自動列印通常走這裡)
        content = generate_html("結帳單 Receipt", items, is_receipt=True)
        content += generate_html("廚房單 - 麵區", noodle_items)
        content += generate_html("廚房單 - 湯區", soup_items)
        content += generate_html("廚房單 - 其他", other_items)

    return f"<html><head>{style}</head><body onload='window.print();setTimeout(()=>window.close(),500);'>{content}</body></html>"


# --- 4. 狀態變更 (完成/作廢) ---
@kitchen_bp.route('/complete/<int:oid>')
def complete_order(oid):
    c=get_db_connection(); cur=c.cursor()
    cur.execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,))
    c.commit(); c.close(); 
    
    # [修改] 改用 print 輸出 Log
    now = get_current_time_str()
    print(f"[{now}] ✅ 訂單完成: ID {oid}")
    return "OK"

@kitchen_bp.route('/cancel/<int:oid>')
def cancel_order(oid):
    c=get_db_connection(); cur=c.cursor()
    cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,))
    c.commit(); c.close(); 
    
    # [修改] 改用 print 輸出 Log
    now = get_current_time_str()
    print(f"[{now}] 🗑️ 訂單作廢: ID {oid}")
    return "OK"


# --- 5. 日結報表與銷售排名 ---
@kitchen_bp.route('/sales_ranking')
def sales_ranking():
    start_time_str = request.args.get('start_time') or request.args.get('start')
    end_time_str = request.args.get('end_time') or request.args.get('end')
    
    utc_start, utc_end = None, None

    # 嘗試解析詳細時間 (YYYY-MM-DDTHH:MM)
    if start_time_str and 'T' in start_time_str:
        try:
            tw_start = datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M').replace(second=0)
            tw_end = datetime.strptime(end_time_str, '%Y-%m-%dT%H:%M') if (end_time_str and 'T' in end_time_str) else datetime.now()
            utc_start = tw_start - timedelta(hours=8)
            utc_end = tw_end - timedelta(hours=8)
        except ValueError: pass

    if not utc_start:
        # 如果沒有詳細時間，使用預設日期範圍工具
        utc_start, utc_end = get_tw_time_range(start_time_str, end_time_str)

    conn = get_db_connection()
    cur = conn.cursor()
    
    # 修改：包含 'Pending' (處理中) 和 'Completed' (已完成)
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
            items = json.loads(r[0])
            for i in items:
                # 優先使用 name_zh，若無則用 name，若皆無則顯示 '未知品項'
                name = i.get('name_zh', i.get('name', '未知品項'))
                qty = int(float(i.get('qty', 1)))
                stats[name] = stats.get(name, 0) + qty
        except: continue
        
    sorted_data = [{"name": k, "count": v} for k, v in sorted(stats.items(), key=lambda item: item[1], reverse=True)]
    return jsonify(sorted_data)


@kitchen_bp.route('/report')
def daily_report():
    target_date_str = request.args.get('date') or (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d')
    # 取得當天的 UTC 時間範圍
    utc_start, utc_end = get_tw_time_range(target_date_str)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 建立產品價格對照表 (避免 JSON 內價格遺失時使用)
    cur.execute("SELECT name, price FROM products")
    price_map = {row[0]: row[1] for row in cur.fetchall()}
    
    # --- 統計有效單 (包含 處理中 + 完成) ---
    # 1. 計算總單數與總金額
    cur.execute("""
        SELECT COUNT(*), SUM(total_price) 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s 
        AND status IN ('Pending', 'Completed')
    """, (utc_start, utc_end))
    v_count, v_total = cur.fetchone()

    # 2. 取得有效單的詳細內容 (用於計算各品項銷售)
    cur.execute("""
        SELECT content_json 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s 
        AND status IN ('Pending', 'Completed')
    """, (utc_start, utc_end))
    v_rows = cur.fetchall()
    
    # --- 統計作廢單 (Cancelled) ---
    cur.execute("""
        SELECT COUNT(*), SUM(total_price) 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s 
        AND status = 'Cancelled'
    """, (utc_start, utc_end))
    x_count, x_total = cur.fetchone()
    
    cur.execute("""
        SELECT content_json 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s 
        AND status = 'Cancelled'
    """, (utc_start, utc_end))
    x_rows = cur.fetchall()
    
    conn.close()

    # 聚合統計函式 (解析 JSON 並加總數量與金額)
    def agg(rows):
        res = {}
        for r in rows:
            if not r[0]: continue
            try:
                items = json.loads(r[0])
                for i in items:
                    name = i.get('name_zh', i.get('name', '商品'))
                    qty_val = i.get('qty')
                    qty = int(float(qty_val)) if qty_val is not None else 1
                    
                    price_val = i.get('price')
                    if price_val is not None: 
                        price = int(float(price_val))
                    else: 
                        price = price_map.get(name, 0)
                    
                    if name not in res: res[name] = {'qty':0, 'amt':0}
                    res[name]['qty'] += qty
                    res[name]['amt'] += (qty * price)
            except: continue
        return res

    v_stats = agg(v_rows) # 有效單統計
    x_stats = agg(x_rows) # 作廢單統計

    # 產生 HTML 表格的函式
    def tbl(stats_dict):
        if not stats_dict: return "<p style='text-align:center;color:#888;'>無銷售數據</p>"
        h = "<table style='width:100%; border-collapse:collapse; margin-top:10px;'><thead><tr style='border-bottom:2px solid #333;'><th style='text-align:left;'>品項</th><th style='text-align:right;'>數</th><th style='text-align:right;'>額</th></tr></thead><tbody>"
        for k, v in sorted(stats_dict.items(), key=lambda x:x[1]['qty'], reverse=True):
            h += f"<tr style='border-bottom:1px solid #eee;'><td>{k}</td><td style='text-align:right;'>{v['qty']}</td><td style='text-align:right;'>${v['amt']:,}</td></tr>"
        return h + "</tbody></table>"

    # 回傳完整的 HTML 報表頁面
    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>日結報表_{target_date_str}</title>
    <style>
        body {{ font-family: sans-serif; background: #f4f4f4; display:flex; flex-direction:column; align-items:center; padding:20px; }}
        .ticket {{ background: white; width: 80mm; padding: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); border-radius: 5px; }}
        .summary {{ background: #e8f5e9; padding: 10px; border-radius: 5px; margin: 10px 0; border-left: 5px solid #2e7d32; }}
        .void-sum {{ background: #ffebee; border-left-color: #c62828; }}
        @media print {{ .no-print {{ display: none; }} body {{ background: white; padding: 0; }} .ticket {{ box-shadow: none; width: 100%; }} }}
    </style></head>
    <body>
        <div class="no-print" style="margin-bottom:20px;">
            <input type="date" id="dateInput" value="{target_date_str}" onchange="location.href='/kitchen/report?date='+this.value">
            <button onclick="window.print()">列印</button> <button onclick="location.href='/kitchen'">返回</button>
        </div>
        <div class="ticket">
            <h2 style="text-align:center; margin:0;">日結營收報表</h2>
            <p style="text-align:center; font-size:14px;">日期: {target_date_str}</p>
            <div class="summary"><b>✅ 有效營收 (含進行中)</b><br>單數: {v_count} | 總計: <span style="font-size:1.2em; color:#2e7d32;">${int(v_total or 0):,}</span></div>
            {tbl(v_stats)}
            <div class="summary void-sum" style="margin-top:20px;"><b>❌ 作廢統計</b><br>單數: {x_count} | 金額: ${int(x_total or 0):,}</div>
            {tbl(x_stats)}
            <p style="text-align:center; font-size:10px; color:#999; margin-top:20px;">列印時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        </div>
    </body></html>
    """

