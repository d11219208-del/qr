from flask import Blueprint, render_template, request, jsonify
from database import get_db_connection
from datetime import datetime, timedelta

# 建立藍圖
kitchen_bp = Blueprint('kitchen', __name__)

# --- 廚房看板主頁面 ---
@kitchen_bp.route('/')
def kitchen_board():
    # 這裡回傳 HTML，原本 app.py 裡的 KITCHEN_HTML 字串要移到 templates/kitchen.html
    return render_template('kitchen.html')

# --- 取得待處理訂單 API (供看板輪詢使用) ---
@kitchen_bp.route('/orders')
def get_kitchen_orders():
    conn = get_db_connection()
    cur = conn.cursor()
    # 抓取今天 Pending 或 Preparing 的訂單
    cur.execute("""
        SELECT id, table_number, items, total_price, status, daily_seq, created_at, need_receipt 
        FROM orders 
        WHERE created_at::date = CURRENT_DATE 
          AND status IN ('Pending', 'Preparing')
        ORDER BY created_at ASC
    """)
    rows = cur.fetchall()
    
    orders = []
    for r in rows:
        # 計算已下單時間（分鐘）
        wait_time = int((datetime.now() - r[6]).total_seconds() / 60)
        orders.append({
            "id": r[0],
            "table_number": r[1],
            "items": r[2],
            "total_price": r[3],
            "status": r[4],
            "daily_seq": r[5],
            "wait_time": wait_time,
            "need_receipt": r[7]
        })
    
    cur.close()
    conn.close()
    return jsonify(orders)

# --- 更新訂單狀態 API ---
@kitchen_bp.route('/update_status/<int:order_id>', methods=['POST'])
def update_status(order_id):
    new_status = request.json.get('status') # 例如 'Preparing', 'Completed', 'Cancelled'
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE orders SET status = %s WHERE id = %s", (new_status, order_id))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)})
    finally:
        cur.close()
        conn.close()