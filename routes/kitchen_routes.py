from flask import Blueprint, render_template, request, jsonify
import json
from datetime import datetime, timedelta
from database import get_db_connection 

kitchen_bp = Blueprint('kitchen', __name__)

def get_tw_time_range(target_date_str=None, end_date_str=None):
    """計算台灣時間的 UTC 起始與結束範圍"""
    try:
        if target_date_str:
            tw_start = datetime.strptime(target_date_str, '%Y-%m-%d')
        else:
            tw_start = datetime.utcnow() + timedelta(hours=8)
        
        tw_start = tw_start.replace(hour=0, minute=0, second=0, microsecond=0)

        if end_date_str:
            tw_end = datetime.strptime(end_date_str, '%Y-%m-%d')
        else:
            tw_end = tw_start
        
        tw_end = tw_end.replace(hour=23, minute=59, second=59, microsecond=999999)
        return tw_start - timedelta(hours=8), tw_end - timedelta(hours=8)
    except:
        now = datetime.utcnow() + timedelta(hours=8)
        return now.replace(hour=0, minute=0, second=0) - timedelta(hours=8), \
               now.replace(hour=23, minute=59, second=59) - timedelta(hours=8)

# --- 1. 廚房看板主頁 ---
@kitchen_bp.route('/')
def kitchen_panel():
    return render_template('kitchen.html')

# --- 2. 檢查新訂單 API (整合 Modal 觸發) ---
@kitchen_bp.route('/check_new_orders')
def check_new_orders():
    current_max = request.args.get('current_seq', 0, type=int)
    utc_start, utc_end = get_tw_time_range()

    conn = get_db_connection()
    cur = conn.cursor()
    
    query = """
        SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json 
        FROM orders 
        WHERE created_at >= %s AND created_at <= %s
        ORDER BY CASE WHEN status = 'Pending' THEN 0 ELSE 1 END, daily_seq DESC
    """
    cur.execute(query, (utc_start, utc_end))
    orders = cur.fetchall()
    
    cur.execute("SELECT MAX(daily_seq) FROM orders WHERE created_at >= %s AND created_at <= %s", (utc_start, utc_end))
    res_max = cur.fetchone()
    max_seq_val = res_max[0] if res_max and res_max[0] else 0
    
    new_order_ids = []
    if current_max > 0:
        cur.execute("SELECT id FROM orders WHERE daily_seq > %s AND created_at >= %s", (current_max, utc_start))
        new_order_ids = [r[0] for r in cur.fetchall()]
    conn.close()

    html_content = ""
    if not orders: 
        html_content = "<div id='loading-msg'>🍽️ 目前沒有訂單</div>"
    
    for o in orders:
        oid, table, raw_items, total, status, created, order_lang, seq_num, c_json = o
        status_cls = status.lower()
        tw_time = created + timedelta(hours=8)
        
        items_html = ""
        try:
            cart = json.loads(c_json) if c_json else []
            for item in cart:
                name = item.get('name_zh', item.get('name', '商品'))
                qty = item.get('qty', 1)
                options = item.get('options_zh', item.get('options', []))
                opts_html = f"<div class='item-opts'>└ {' / '.join(options)}</div>" if options else ""
                items_html += f"<div class='item-row'><div class='item-name'><span>{name}</span><span class='item-qty'>x{qty}</span></div>{opts_html}</div>"
        except: 
            items_html = "<div class='item-row'>資料解析錯誤</div>"

        formatted_total = f"{int(total)}" 
        buttons = ""

        if status == 'Pending':
            buttons += f"""
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding:0 5px;">
                    <span style="font-size:14px; color:#666; font-weight:bold;">應收總計:</span>
                    <span style="font-size:22px; color:#d32f2f; font-weight:900;">${formatted_total}</span>
                </div>
            """
            buttons += f"<button onclick='action(\"/kitchen/complete/{oid}\")' class='btn btn-main'>✅ 出餐 / 付款</button>"
            # 注意這裡：href 改為 onclick='askPrintType({oid})'
            buttons += f"""<div class="btn-group" style="margin-top:8px;">
                <button onclick='askPrintType({oid})' class='btn btn-print'>🖨️ 列印</button>
                <a href='/menu?edit_oid={oid}&lang=zh' target='_blank' class='btn' style='background:#ff9800; color:white; text-decoration:none;'>✏️ 修改</a>
                <button onclick='if(confirm(\"⚠️ 作廢？\")) action(\"/kitchen/cancel/{oid}\")' class='btn btn-void'>🗑️</button>
            </div>"""
        else:
            buttons += f"""
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; padding:0 5px; opacity:0.7;">
                    <span style="font-size:13px; color:#666;">實收總計:</span>
                    <span style="font-size:18px; color:#333; font-weight:bold;">${formatted_total}</span>
                </div>
            """
            buttons += f"<div class='btn-group'><button onclick='askPrintType({oid})' class='btn btn-print' style='width:100%'>🖨️ 補印單據</button></div>"

        html_content += f"""
        <div class="card {status_cls}">
            <div class="card-header">
                <div><div class="seq-num">#{seq_num:03d}</div><div class="time-stamp">{tw_time.strftime('%H:%M')} ({order_lang})</div></div>
                <div class="table-num">桌號 {table}</div>
            </div>
            <div class="items">{items_html}</div>
            <div class="actions">{buttons}</div>
        </div>"""
        
    return jsonify({'html': html_content, 'max_seq': max_seq_val, 'new_ids': new_order_ids})

# --- 3. 補印功能 (整合分區列印邏輯) ---
@kitchen_bp.route('/print_order/<int:oid>')
def print_order(oid):
    print_type = request.args.get('type', 'all')
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT table_number, total_price, daily_seq, content_json, created_at FROM orders WHERE id=%s", (oid,))
    order = cur.fetchone()
    if not order:
        conn.close()
        return "訂單不存在", 404
    
    table_num, total_price, seq, content_json, created_at = order
    items = json.loads(content_json) if content_json else []
    time_str = (created_at + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')

    # 獲取產品與其列印分區的對照表
    cur.execute("SELECT name, print_category FROM products")
    product_map = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()

    # 分類邏輯
    noodle_items = []
    soup_items = []
    other_items = []

    for item in items:
        p_name = item.get('name_zh') or item.get('name')
        p_cat = product_map.get(p_name, 'Noodle') # 預設為面區
        
        if p_cat == 'Noodle':
            noodle_items.append(item)
        elif p_cat == 'Soup':
            soup_items.append(item)
        else:
            other_items.append(item)

    style = """
    <style>
        body { font-family: 'Microsoft JhengHei', sans-serif; width: 58mm; margin: 0; padding: 2mm; }
        .ticket { border-bottom: 2px dashed #000; padding: 10px 0; page-break-after: always; }
        .head { text-align: center; }
        .head h1 { font-size: 40px; margin: 5px 0; }
        .head h2 { font-size: 18px; margin: 0; background: #333; color: #fff; padding: 2px; }
        .info { font-size: 14px; font-weight: bold; margin-bottom: 5px; }
        .item { display: flex; justify-content: space-between; font-size: 17px; font-weight: bold; margin: 4px 0; }
        .opt { font-size: 13px; color: #666; padding-left: 10px; margin-bottom: 5px; border-left: 2px solid #ccc; }
        .total { text-align: right; font-size: 18px; font-weight: bold; margin-top: 10px; border-top: 1px solid #000; }
        @media print { .no-print { display: none; } }
    </style>
    """

    def generate_html(title, item_list, is_receipt=False):
        if not item_list: return ""
        h = f"<div class='ticket'><div class='head'><h2>{title}</h2><h1>#{seq:03d}</h1></div>"
        h += f"<div class='info'>桌號: {table_num}<br>時間: {time_str}</div><hr style='border:0; border-top:1px solid #000;'>"
        for i in item_list:
            name = i.get('name_zh') or i.get('name')
            qty = i.get('qty', 1)
            opts = i.get('options_zh') or i.get('options', [])
            h += f"<div class='item'><span>{name}</span><span>x{qty}</span></div>"
            if opts: h += f"<div class='opt'>└ {', '.join(opts)}</div>"
        
        if is_receipt:
            h += f"<div class='total'>總計: ${int(total_price)}</div>"
        return h + "</div>"

    content = ""
    if print_type == 'receipt':
        content = generate_html("結帳單 (Receipt)", items, is_receipt=True)
    elif print_type == 'kitchen':
        content += generate_html("廚房單 - 面區", noodle_items)
        content += generate_html("廚房單 - 湯區", soup_items)
        content += generate_html("廚房單 - 其他", other_items)
    else: # all
        content = generate_html("結帳單 (Receipt)", items, is_receipt=True)
        content += generate_html("廚房單 - 面區", noodle_items)
        content += generate_html("廚房單 - 湯區", soup_items)
        content += generate_html("廚房單 - 其他", other_items)

    return f"<html><head>{style}</head><body onload='window.print();setTimeout(()=>window.close(),500);'>{content}</body></html>"

# --- 4. 狀態變更與報表 (保留原功能) ---
@kitchen_bp.route('/complete/<int:oid>')
def complete_order(oid):
    c=get_db_connection(); cur=c.cursor()
    cur.execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,))
    c.commit(); c.close()
    return "OK"

@kitchen_bp.route('/cancel/<int:oid>')
def cancel_order(oid):
    c=get_db_connection(); cur=c.cursor()
    cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,))
    c.commit(); c.close()
    return "OK"

@kitchen_bp.route('/sales_ranking')
def sales_ranking():
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    utc_start, utc_end = get_tw_time_range(start_str, end_str)
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT content_json FROM orders WHERE created_at >= %s AND created_at <= %s AND status = 'Completed'", (utc_start, utc_end))
    rows = cur.fetchall(); conn.close()
    stats = {}
    for r in rows:
        if not r[0]: continue
        try:
            items = json.loads(r[0])
            for i in items:
                name = i.get('name_zh', i.get('name', '未知品項'))
                qty = int(float(i.get('qty', 1)))
                stats[name] = stats.get(name, 0) + qty
        except: continue
    sorted_data = [{"name": k, "total_qty": v} for k, v in sorted(stats.items(), key=lambda item: item[1], reverse=True)]
    return jsonify(sorted_data)

@kitchen_bp.route('/report')
def daily_report():
    # ... (您的報表程式碼保持不變，它已經處理得很完善了)
    # 建議在此處也調用上面定義的 get_tw_time_range 確保時區統一
    target_date_str = request.args.get('date') or (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d')
    # ... 剩下的報表邏輯 ...
    return "報表頁面內容" # 這裡請保留您原有的 HTML Return
