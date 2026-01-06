import os
import psycopg2
import json
from flask import Flask, request, redirect, url_for
from datetime import datetime

app = Flask(__name__)

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 1. 資料庫初始化 (只在第一次或需要重置時執行) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 建立菜單表 (Products)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price INTEGER NOT NULL,
                category VARCHAR(50)
            );
        ''')
        
        # 建立訂單表 (Orders)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                customer_name VARCHAR(50),
                table_number VARCHAR(10),
                items TEXT NOT NULL, 
                total_price INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # 檢查菜單是否為空，如果是空的就加一點預設菜色
        cur.execute('SELECT count(*) FROM products;')
        if cur.fetchone()[0] == 0:
            default_menu = [
                ('招牌牛肉麵', 180, '主食'),
                ('古早味排骨飯', 120, '主食'),
                ('燙青菜', 40, '小菜'),
                ('滷蛋', 15, '小菜'),
                ('珍珠奶茶', 60, '飲料'),
                ('冰紅茶', 30, '飲料')
            ]
            cur.executemany('INSERT INTO products (name, price, category) VALUES (%s, %s, %s)', default_menu)

        conn.commit()
        return "系統初始化成功！資料表與預設菜單已建立。<br><a href='/'>前往點餐首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 點餐首頁 ---
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_db_connection()
    cur = conn.cursor()

    # 如果是送出訂單
    if request.method == 'POST':
        customer_name = request.form.get('customer_name')
        table_number = request.form.get('table_number')
        
        # 獲取勾選的商品 ID 列表
        selected_item_ids = request.form.getlist('items')
        
        if not selected_item_ids:
            return "錯誤：您沒有選擇任何餐點。<a href='/'>重試</a>"

        # 計算總價並整理商品名稱
        total_price = 0
        ordered_items_names = []
        
        # 為了安全，我們重新查詢資料庫獲取價格
        for pid in selected_item_ids:
            cur.execute("SELECT name, price FROM products WHERE id = %s", (pid,))
            product = cur.fetchone()
            if product:
                ordered_items_names.append(product[0])
                total_price += product[1]
        
        # 將商品列表轉成文字儲存
        items_str = ", ".join(ordered_items_names)

        # 寫入訂單
        cur.execute(
            "INSERT INTO orders (customer_name, table_number, items, total_price) VALUES (%s, %s, %s, %s)",
            (customer_name, table_number, items_str, total_price)
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('kitchen'))

    # 如果是 GET (顯示菜單)
    cur.execute("SELECT * FROM products ORDER BY category, id")
    products = cur.fetchall()
    cur.close()
    conn.close()

    # 簡單的 CSS 美化
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>線上點餐系統</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: '微軟正黑體', sans-serif; background-color: #f4f4f9; padding: 20px; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            h1 { text-align: center; color: #333; }
            .menu-item { display: flex; justify-content: space-between; border-bottom: 1px solid #eee; padding: 10px 0; align-items: center; }
            .menu-item label { flex-grow: 1; margin-left: 10px; cursor: pointer; }
            .price { font-weight: bold; color: #e91e63; }
            .input-group { margin-bottom: 15px; }
            .input-group label { display: block; margin-bottom: 5px; font-weight: bold; }
            input[type="text"], input[type="number"] { width: 100%; padding: 8px; box-sizing: border-box; }
            button { width: 100%; padding: 12px; background-color: #4CAF50; color: white; border: none; font-size: 18px; border-radius: 5px; cursor: pointer; margin-top: 20px; }
            button:hover { background-color: #45a049; }
            .nav { text-align: center; margin-bottom: 20px; }
            .nav a { margin: 0 10px; text-decoration: none; color: #007bff; }
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/">📋 點餐頁面</a> | <a href="/kitchen">👨‍🍳 廚房/訂單看板</a>
        </div>
        <div class="container">
            <h1>🍴 美味菜單</h1>
            <form method="POST">
                <div class="input-group">
                    <label>桌號 / 取餐號：</label>
                    <input type="text" name="table_number" placeholder="例如：A1 或 您的手機後三碼" required>
                </div>
                <div class="input-group">
                    <label>顧客暱稱：</label>
                    <input type="text" name="customer_name" placeholder="例如：王先生">
                </div>
                
                <h3>請選擇餐點：</h3>
    """
    
    current_category = ""
    for p in products:
        # p = (id, name, price, category)
        if p[3] != current_category:
            html += f"<h4 style='background:#eee; padding:5px;'>{p[3]}</h4>"
            current_category = p[3]
            
        html += f"""
        <div class="menu-item">
            <input type="checkbox" name="items" value="{p[0]}" id="p_{p[0]}">
            <label for="p_{p[0]}">
                {p[1]} 
                <span class="price">${p[2]}</span>
            </label>
        </div>
        """

    html += """
                <button type="submit">送出訂單</button>
            </form>
        </div>
    </body>
    </html>
    """
    return html

# --- 3. 廚房看板 (查看訂單) ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
    # 撈出所有訂單，最新的在上面
    cur.execute("SELECT * FROM orders ORDER BY created_at DESC")
    orders = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>廚房看板</title>
        <meta http-equiv="refresh" content="10"> <style>
            body { font-family: '微軟正黑體', sans-serif; background-color: #222; color: white; padding: 20px; }
            .nav { text-align: center; margin-bottom: 20px; }
            .nav a { color: #4CAF50; text-decoration: none; font-size: 1.2em; }
            .order-card { background-color: #333; border-left: 5px solid #ff9800; margin-bottom: 15px; padding: 15px; border-radius: 5px; }
            .order-header { display: flex; justify-content: space-between; border-bottom: 1px solid #555; padding-bottom: 10px; margin-bottom: 10px; }
            .table-num { font-size: 1.5em; font-weight: bold; color: #ff9800; }
            .items { font-size: 1.2em; line-height: 1.6; }
            .time { color: #888; font-size: 0.8em; }
            .total { text-align: right; color: #4CAF50; font-weight: bold; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/">⬅️ 回到點餐頁面</a>
        </div>
        <h1 style="text-align:center;">👨‍🍳 廚房接單系統 (即時)</h1>
    """
    
    if not orders:
        html += "<h3 style='text-align:center; color:#777;'>目前沒有訂單...</h3>"

    for order in orders:
        # order = (id, name, table, items, total, status, time)
        # 注意：這裡就是剛剛報錯的地方，請確保下方的 f""" 和 """ 是完整的
        html += f"""
        <div class="order-card">
            <div class="order-header">
                <span class="table-num">桌號：{order[2]}</span>
                <span>{order[1]} (ID: {order[0]})</span>
            </div>
            <div class="items">
                {order[3]}
            </div>
            <div class="total">總計：${order[4]}</div>
            <div class="time">下單時間：{order[6]}</div>
        </div>
        """

    html += "</body></html>"
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
