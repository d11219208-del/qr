from flask import Blueprint, render_template, request, jsonify, redirect, url_for
import json
from datetime import datetime, timedelta
from database import get_db_connection 

kitchen_bp = Blueprint('kitchen', __name__)

def get_tw_time_range(target_date_str=None, end_date_str=None):
    """計算台灣時間的 UTC 起始與結束範圍 (支援區間查詢)"""
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
        
        # 返回 UTC 時間以供資料庫查詢
        return tw_start - timedelta(hours=8), tw_end - timedelta(hours=8)
    except:
        now = datetime.utcnow() + timedelta(hours=8)
        return now.replace(hour=0, minute=0, second=0) - timedelta(hours=8), \
               now.replace(hour=23, minute=59, second=59) - timedelta(hours=8)

# --- 1. 廚房看板主頁 ---
@kitchen_bp.route('/')
def kitchen_panel():
    return render_template('kitchen.html')

# --- 2. 檢查新訂單 API ---
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
        html_content = "<div style='grid-column:1/-1;text-align:center;padding:100px;font-size:1.5em;color:#888;'>🍽️ 目前沒有訂單</div>"
    
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

        buttons = ""
        if status == 'Pending':
            buttons += f"<button onclick='action(\"/kitchen/complete/{oid}\")' class='btn btn-main'>✅ 出餐 / 付款</button>"
            buttons += f"""<div class="btn-group">
                <a href='/kitchen/print_order/{oid}' target='_blank' class='btn btn-print'>🖨️ 補印</a>
                <a href='/menu?edit_oid={oid}&lang=zh' target='_blank' class='btn btn-edit' style='background:#ff9800; color:white;'>✏️ 修改</a>
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

# --- 3. 商品銷售排名 API (新增功能) ---
@kitchen_bp.route('/sales_ranking')
def sales_ranking():
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    utc_start, utc_end = get_tw_time_range(start_str, end_str)

    conn = get_db_connection()
    cur = conn.cursor()
    # 僅統計已完成(Completed)的訂單
    cur.execute("SELECT content_json FROM orders WHERE created_at >= %s AND created_at <= %s AND status = 'Completed'", (utc_start, utc_end))
    rows = cur.fetchall()
    conn.close()

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

    # 轉換為前端所需的格式並排序
    sorted_data = [{"name": k, "total_qty": v} for k, v in sorted(stats.items(), key=lambda item: item[1], reverse=True)]
    return jsonify(sorted_data)

# --- 4. 補印功能 ---
@kitchen_bp.route('/print_order/<int:oid>')
def print_order(oid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, table_number, total_price, status, created_at, daily_seq, content_json, lang FROM orders WHERE id=%s", (oid,))
    o = cur.fetchone()
    conn.close()
    if not o: return "No Data", 404

    oid_db, table_num, total_val, status, created_at, daily_seq, c_json, order_lang = o
    seq = f"{daily_seq:03d}"
    items = json.loads(c_json) if c_json else []
    tw_time = created_at + timedelta(hours=8)
    time_str = tw_time.strftime('%Y-%m-%d %H:%M:%S')

    def mk_ticket(t_name, item_list, show_total=False, is_kitchen=False):
        if not item_list: return ""
        h = f"<div class='ticket'><div class='head'><h2>{t_name}</h2><h1>#{seq}</h1><p>Table: {table_num}</p><small>{time_str}</small></div><hr>"
        for i in item_list:
            qty = i.get('qty', 1)
            d_name = i.get('name_zh', '商品') if is_kitchen else i.get('name_zh')
            ops = i.get('options_zh', [])
            h += f"<div class='row'><span>{qty} x {d_name}</span><span></span></div>"
            if ops: h += f"<div class='opt'>└ {', '.join(ops)}</div>"
        if show_total: h += f"<hr><div style='text-align:right;font-size:1.2em;font-weight:bold;'>Total: ${total_val}</div>"
        return h + "</div><div class='break'></div>"

    body = mk_ticket("結帳單 (Receipt)", items, show_total=True)
    return f"<html><body onload='window.print();window.close();'>{body}</body></html>" # 簡化列印模板以節省篇幅

# --- 5. 狀態變更 ---
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

# --- 6. 日結報表 (強化版統計與專業渲染) ---
@kitchen_bp.route('/report')
def daily_report():
    target_date_str = request.args.get('date')
    if not target_date_str:
        target_date_str = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d')
    
    utc_start, utc_end = get_tw_time_range(target_date_str)
    
    from database import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    
    # 查詢有效單與作廢單
    cur.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE created_at >= %s AND created_at <= %s AND status != 'Cancelled'", (utc_start, utc_end))
    valid_count, valid_total = cur.fetchone()
    
    cur.execute("SELECT content_json FROM orders WHERE created_at >= %s AND created_at <= %s AND status != 'Cancelled'", (utc_start, utc_end))
    valid_rows = cur.fetchall()
    
    cur.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE created_at >= %s AND created_at <= %s AND status = 'Cancelled'", (utc_start, utc_end))
    void_count, void_total = cur.fetchone()
    
    cur.execute("SELECT content_json FROM orders WHERE created_at >= %s AND created_at <= %s AND status = 'Cancelled'", (utc_start, utc_end))
    void_rows = cur.fetchall()
    conn.close()

    # 統計品項函數 (整合您的強力偵測邏輯)
    def agg_items(rows):
        stats = {}
        for r in rows:
            if not r[0]: continue
            try:
                items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                if isinstance(items, dict): items = [items]
                for i in items:
                    name = i.get('name_zh', i.get('name', '未知品項'))
                    try:
                        qty = int(float(i.get('qty', 0)))
                    except:
                        qty = 0

                    line_total = 0
                    raw_price = i.get('price') or i.get('unit_price') or i.get('cost') or i.get('amount')
                    raw_line_total = i.get('total') or i.get('subtotal') or i.get('item_total')

                    try:
                        if raw_price is not None:
                            line_total = int(float(raw_price) * qty)
                        elif raw_line_total is not None:
                            line_total = int(float(raw_line_total))
                    except:
                        line_total = 0

                    if name not in stats:
                        stats[name] = {'qty': 0, 'total_amt': 0}
                    stats[name]['qty'] += qty
                    stats[name]['total_amt'] += line_total
            except: continue
        return stats

    valid_stats = agg_items(valid_rows)
    void_stats = agg_items(void_rows)

    def render_table(stats_dict):
        if not stats_dict: return "<p style='text-align:center;color:#888;padding:10px;'>無銷售資料</p>"
        h = """<table style='width:100%; border-collapse:collapse; font-size:14px; margin-top:5px;'>
            <thead><tr style='border-bottom:2px solid #444; background-color: #f0f0f0;'>
            <th style='text-align:left; padding: 6px;'>品項</th>
            <th style='text-align:right; padding: 6px;'>數量</th>
            <th style='text-align:right; padding: 6px;'>金額</th></tr></thead><tbody>"""
        for name, data in sorted(stats_dict.items(), key=lambda x: x[1]['qty'], reverse=True):
            color = "color:red;" if data['total_amt'] == 0 else ""
            h += f"""<tr style='border-bottom: 1px dotted #ccc;'>
                <td style='padding:8px 4px;'>{name}</td>
                <td style='text-align:right; padding:8px 4px;'>{data['qty']}</td>
                <td style='text-align:right; padding:8px 4px; {color}'>${data['total_amt']:,}</td></tr>"""
        return h + "</tbody></table>"

    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <title>日結報表_{target_date_str}</title>
    <style>
        body {{ font-family: sans-serif; background: #eee; padding: 20px; display: flex; flex-direction: column; align-items: center; }} 
        .ticket {{ background: white; width: 85mm; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.15); border-radius: 4px; }} 
        .summary-box {{ margin-bottom: 15px; padding: 12px; border-radius: 8px; border-left: 5px solid #28a745; background: #f8f9fa; }} 
        .void-box {{ border-left-color: #dc3545; background: #fff5f5; }}
        .no-print {{ margin-bottom: 20px; background: white; padding: 15px; border-radius: 8px; }}
        .btn {{ padding: 8px 15px; border-radius: 4px; text-decoration: none; color: white; border: none; cursor: pointer; }}
        @media print {{ .no-print {{ display: none; }} body {{ background: white; padding: 0; }} .ticket {{ box-shadow: none; width: 100%; }} }}
    </style></head>
    <body>
        <div class="no-print">
            <form action="/kitchen/report" method="get">
                📅 日期：<input type="date" name="date" value="{target_date_str}" onchange="this.form.submit()">
                <button type="button" class="btn" style="background:#28a745" onclick="window.print()">🖨️ 列印</button>
                <a href="/kitchen" class="btn" style="background:#6c757d">🔙 返回</a>
            </form>
        </div>
        <div class="ticket">
            <h2 style="text-align:center;border-bottom:2px solid #333;padding-bottom:10px;">日結營收報表</h2>
            <p style="text-align:center;">營業日期: <b>{target_date_str}</b></p>
            <div class="summary-box">
                <b>✅ 有效訂單統計</b><br>單數：{valid_count or 0} 筆<br>總額：<span style="color:#28a745;font-size:1.2em;font-weight:bold;">${int(valid_total or 0):,}</span>
            </div>
            <b>[ 商品銷售明細 ]</b>
            {render_table(valid_stats)}
            <div class="summary-box void-box" style="margin-top:20px;">
                <b>❌ 作廢/取消統計</b><br>單數：{void_count or 0} 筆<br>金額：${int(void_total or 0):,}
            </div>
            {render_table(void_stats)}
            <p style="text-align:center; font-size:11px; color:#999; margin-top:20px;">製表時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </body></html>
    """

