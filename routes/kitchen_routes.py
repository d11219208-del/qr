from flask import Blueprint, render_template, request, jsonify, redirect, url_for
import json
from datetime import datetime, timedelta
# 假設您的 get_db_connection 定義在主程式或 common 模組
# from app import get_db_connection 

kitchen_bp = Blueprint('kitchen', __name__)

def get_tw_time_range():
    """計算台灣時間的當日起始與結束（UTC）"""
    tw_now = datetime.utcnow() + timedelta(hours=8)
    tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
    utc_start = tw_start - timedelta(hours=8)
    utc_end = tw_end - timedelta(hours=8)
    return utc_start, utc_end

@kitchen_bp.route('/kitchen')
def kitchen_panel():
    return render_template('kitchen.html')

@kitchen_bp.route('/check_new_orders')
def check_new_orders():
    current_max = request.args.get('current_seq', 0, type=int)
    utc_start, utc_end = get_tw_time_range()
    time_filter = f"created_at >= '{utc_start}' AND created_at <= '{utc_end}'"

    from app import get_db_connection # 延遲匯入避免循環引用
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 抓取訂單 (Pending 優先)
    cur.execute(f"""
        SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json 
        FROM orders WHERE {time_filter} 
        ORDER BY CASE WHEN status = 'Pending' THEN 0 ELSE 1 END, daily_seq DESC
    """)
    orders = cur.fetchall()
    
    # 抓取最大序號
    cur.execute(f"SELECT MAX(daily_seq) FROM orders WHERE {time_filter}")
    res_max = cur.fetchone()
    max_seq_val = res_max[0] if res_max and res_max[0] else 0
    
    # 判斷新訂單 IDs (用於觸發自動列印)
    new_order_ids = []
    if current_max > 0:
        cur.execute(f"SELECT id FROM orders WHERE daily_seq > %s AND {time_filter} ORDER BY daily_seq ASC", (current_max,))
        new_order_ids = [r[0] for r in cur.fetchall()]
    conn.close()

    # 此處邏輯由前端 kitchen.html 的 JavaScript 處理或維持 HTML 生成
    # 建議為了方便，這裡回傳 JSON 資料由前端渲染，但根據您的需求，以下維持原 HTML 生成邏輯回傳
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
        except: items_html = "資料解析錯誤"

        # 按鈕邏輯
        buttons = f"<button onclick='action(\"/kitchen/complete/{oid}\")' class='btn btn-main'>✅ 出餐 / 付款</button>" if status == 'Pending' else ""
        buttons += f"""<div class="btn-group">
            <a href='/print_order/{oid}' target='_blank' class='btn btn-print'>🖨️ 補印</a>
            <a href='/menu?edit_oid={oid}&lang=zh' target='_blank' class='btn btn-edit'>✏️ 修改</a>
            <button onclick='if(confirm(\"⚠️ 作廢？\")) action(\"/order/cancel/{oid}\")' class='btn btn-void'>🗑️</button>
        </div>"""

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

@kitchen_bp.route('/kitchen/report')
def daily_report():
    # ... 您原本的 report 邏輯 ...
    # 記得最後 return render_template('report.html', ...) 或直接回傳字串
    pass

@kitchen_bp.route('/kitchen/complete/<int:oid>')
def complete_order(oid):
    from app import get_db_connection
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect(url_for('kitchen.kitchen_panel'))

@kitchen_bp.route('/order/cancel/<int:oid>')
def cancel_order(oid):
    from app import get_db_connection
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect(url_for('kitchen.kitchen_panel'))
