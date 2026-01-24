from flask import Blueprint, render_template, request, jsonify, redirect, url_for
import json
from datetime import datetime, timedelta

kitchen_bp = Blueprint('kitchen', __name__)

def get_tw_time_range(target_date_str=None):
    if target_date_str:
        try:
            target_date_obj = datetime.strptime(target_date_str, '%Y-%m-%d')
        except:
            target_date_obj = datetime.utcnow() + timedelta(hours=8)
    else:
        target_date_obj = datetime.utcnow() + timedelta(hours=8)
    
    tw_start = target_date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
    tw_end = target_date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
    return tw_start - timedelta(hours=8), tw_end - timedelta(hours=8)

# --- 1. 廚房看板主頁 ---
@kitchen_bp.route('/')
def kitchen_panel():
    return render_template('kitchen.html')

# --- 2. 檢查新訂單 API ---
@kitchen_bp.route('/check_new_orders')
def check_new_orders():
    current_max = request.args.get('current_seq', 0, type=int)
    utc_start, utc_end = get_tw_time_range()

    from database import get_db_connection 
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
        html_content = "<div style='grid-column:1/-1;text-align:center;padding:100px;font-size:1.5em;color:#666;'>🍽️ 目前沒有訂單</div>"
    
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

        # 操作連結統一部分前綴
        buttons = ""
        if status == 'Pending':
            buttons += f"<button onclick='action(\"/kitchen/complete/{oid}\")' class='btn btn-main'>✅ 出餐 / 付款</button>"
            buttons += f"""<div class="btn-group">
                <a href='/kitchen/print_order/{oid}' target='_blank' class='btn btn-print'>🖨️ 補印</a>
                <a href='/menu?edit_oid={oid}&lang=zh' target='_blank' class='btn btn-edit'>✏️ 修改</a>
                <button onclick='if(confirm(\"⚠️ 作廢？\")) action(\"/kitchen/cancel/{oid}\")' class='btn btn-void'>🗑️</button>
            </div>"""
        else:
            buttons += f"<div class='btn-group'><a href='/kitchen/print_order/{oid}' target='_blank' class='btn btn-print' style='width:100%'>🖨️ 補印單據</a></div>"

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

# --- 3. 補印功能 (使用內嵌 HTML 解決 500 錯誤) ---
@kitchen_bp.route('/print_order/<int:oid>')
def print_order(oid):
    from database import get_db_connection
    c = get_db_connection()
    cur = c.cursor()
    cur.execute("SELECT table_number, content_json, daily_seq, created_at FROM orders WHERE id=%s", (oid,))
    order = cur.fetchone()
    c.close()
    if not order: return "訂單不存在", 404
    
    table_num, c_json, seq, created = order
    tw_time = created + timedelta(hours=8)
    items = json.loads(c_json) if c_json else []
    
    items_html = ""
    for i in items:
        name = i.get('name_zh', i.get('name', '商品'))
        qty = i.get('qty', 1)
        opts = " / ".join(i.get('options_zh', i.get('options', [])))
        items_html += f"<tr><td colspan='2' style='padding-top:10px;'><b>{name} x {qty}</b></td></tr>"
        if opts: items_html += f"<tr><td colspan='2' style='font-size:12px; padding-bottom:5px;'>└ {opts}</td></tr>"

    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <style>
        body {{ width: 80mm; font-family: sans-serif; padding: 10px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        .header {{ text-align: center; border-bottom: 2px solid #000; padding-bottom: 10px; }}
        .footer {{ text-align: center; border-top: 1px solid #000; margin-top: 20px; padding-top: 10px; font-size: 12px; }}
        @media print {{ .no-print {{ display: none; }} }}
    </style></head>
    <body onload="window.print()">
        <div class="header">
            <h2># {seq:03d} 補列印</h2>
            <div style="font-size: 24px; font-weight: bold;">桌號: {table_num}</div>
            <div>時間: {tw_time.strftime('%Y-%m-%d %H:%M')}</div>
        </div>
        <table>{items_html}</table>
        <div class="footer">請保留此單據作為結帳憑證</div>
        <div class="no-print" style="margin-top:20px; text-align:center;">
            <button onclick="window.close()">關閉視窗</button>
        </div>
    </body></html>
    """

# --- 4. 其他訂單操作 ---
@kitchen_bp.route('/complete/<int:oid>')
def complete_order(oid):
    from database import get_db_connection
    c=get_db_connection(); cur=c.cursor()
    cur.execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,))
    c.commit(); c.close()
    return "OK"

@kitchen_bp.route('/cancel/<int:oid>')
def cancel_order(oid):
    from database import get_db_connection
    c=get_db_connection(); cur=c.cursor()
    cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,))
    c.commit(); c.close()
    return "OK"

# --- 5. 日結報表邏輯 (這部分維持原本你給的程式碼) ---
@kitchen_bp.route('/report')
def daily_report():
    target_date_str = request.args.get('date', (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d'))
    utc_start, utc_end = get_tw_time_range(target_date_str)
    from database import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT content_json, total_price, status FROM orders WHERE created_at >= %s AND created_at <= %s", (utc_start, utc_end))
    rows = cur.fetchall()
    conn.close()
    valid_stats = {}; void_stats = {}
    valid_total = 0; void_total = 0
    valid_count = 0; void_count = 0
    for c_json, price, status in rows:
        stats = void_stats if status == 'Cancelled' else valid_stats
        if status == 'Cancelled': void_total += price; void_count += 1
        else: valid_total += price; valid_count += 1
        try:
            items = json.loads(c_json) if c_json else []
            for i in items:
                name = i.get('name_zh', i.get('name', '未知'))
                qty = int(i.get('qty', 0))
                amt = int(i.get('price', 0)) * qty
                if name not in stats: stats[name] = {'qty': 0, 'amt': 0}
                stats[name]['qty'] += qty; stats[name]['amt'] += amt
        except: continue
    def render_table(stats_dict):
        if not stats_dict: return "<p style='text-align:center;color:#888;'>無銷售資料</p>"
        h = "<table style='width:100%;border-collapse:collapse;font-size:14px;'><thead><tr style='background:#f0f0f0;'><th style='text-align:left;padding:6px;'>品項</th><th style='text-align:right;padding:6px;'>量</th><th style='text-align:right;padding:6px;'>金額</th></tr></thead><tbody>"
        for name, data in sorted(stats_dict.items(), key=lambda x: x[1]['qty'], reverse=True):
            h += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px;'>{name}</td><td style='text-align:right;padding:6px;'>{data['qty']}</td><td style='text-align:right;padding:6px;'>${data['amt']:,}</td></tr>"
        return h + "</tbody></table>"
    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8"><title>日結報表_{target_date_str}</title>
    <style>body {{ font-family: sans-serif; background: #eee; padding: 20px; display: flex; flex-direction: column; align-items: center; }} .ticket {{ background: white; width: 80mm; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); border-radius: 4px; }} .no-print {{ margin-bottom: 20px; background: white; padding: 15px; border-radius: 8px; }} .summary {{ background: #f9f9f9; padding: 10px; border-left: 4px solid #4caf50; margin: 10px 0; }} @media print {{ .no-print {{ display: none; }} body {{ background: white; padding: 0; }} .ticket {{ box-shadow: none; width: 100%; }} }}</style></head>
    <body><div class="no-print"><form action="/kitchen/report" method="get">📅 日期：<input type="date" name="date" value="{target_date_str}" onchange="this.form.submit()"><button type="button" onclick="window.print()">🖨️ 列印</button><a href="/kitchen">🔙 返回</a></form></div><div class="ticket"><h2 style="text-align:center;">日結營收報表</h2><p style="text-align:center;">{target_date_str}</p><div class="summary"><b>✅ 有效訂單</b><br>單數：{valid_count} 筆 / 總額：<span style="color:green;font-weight:bold;">${valid_total:,}</span></div>{render_table(valid_stats)}<div class="summary" style="border-left-color: #f44336; margin-top:20px;"><b>❌ 作廢統計</b><br>單數：{void_count} 筆 / 金額：${void_total:,}</div>{render_table(void_stats)}</div></body></html>
    """
