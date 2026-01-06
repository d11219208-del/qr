import os
import psycopg2
import json
from flask import Flask, request, redirect, url_for
from datetime import datetime, date

app = Flask(__name__)

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 1. 資料庫初始化 (安全版：不會刪除舊資料) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 建立菜單表 (如果不小心沒圖，用預設圖)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price INTEGER NOT NULL,
                category VARCHAR(50),
                image_url TEXT
            );
        ''')
        
        # 建立訂單表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                table_number VARCHAR(10),
                items TEXT NOT NULL, 
                total_price INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # 檢查是否有菜單，沒有才新增 (避免重複)
        cur.execute('SELECT count(*) FROM products;')
        if cur.fetchone()[0] == 0:
            default_menu = [
                ('招牌牛肉麵', 180, '主食', 'https://i.ibb.co/vz1k3j1/beef-noodle.jpg'),
                ('古早味排骨飯', 120, '主食', 'https://i.ibb.co/MCTvVqL/pork-rice.jpg'),
                ('燙青菜', 40, '小菜', 'https://i.ibb.co/Xkz2zt3/vegetables.jpg'),
                ('滷蛋', 15, '小菜', 'https://i.ibb.co/hWz6qg8/egg.jpg'),
                ('珍珠奶茶', 60, '飲料', 'https://i.ibb.co/JtdjvX3/bubble-tea.jpg'),
                ('冰紅茶', 30, '飲料', 'https://i.ibb.co/jyn2V2t/black-tea.jpg')
            ]
            cur.executemany('INSERT INTO products (name, price, category, image_url) VALUES (%s, %s, %s, %s)', default_menu)

        conn.commit()
        return "資料庫初始化完成（已保留舊資料）。<br><a href='/'>前往首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 危險區域：清除所有資料 (需手動呼叫) ---
@app.route('/reset_db_danger')
def reset_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS products;')
    cur.execute('DROP TABLE IF EXISTS orders;')
    conn.commit()
    cur.close()
    conn.close()
    return "警告：所有資料已清空。請重新執行 <a href='/init_db'>/init_db</a>"

# --- 2. 點餐首頁 ---
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    table_from_url = request.args.get('table', '')

    if request.method == 'POST':
        table_number = request.form.get('table_number')
        selected_item_ids = request.form.getlist('items')
        
        if not selected_item_ids:
            return "錯誤：未選擇餐點。<a href='/'>重試</a>"

        total_price = 0
        ordered_items_names = []
        
        # 查詢價格與名稱
        for pid in selected_item_ids:
            cur.execute("SELECT name, price FROM products WHERE id = %s", (pid,))
            product = cur.fetchone()
            if product:
                ordered_items_names.append(f"{product[0]} (${product[1]})")
                total_price += product[1]
        
        items_str = " + ".join(ordered_items_names)

        # 寫入訂單
        cur.execute(
            "INSERT INTO orders (table_number, items, total_price) VALUES (%s, %s, %s) RETURNING id",
            (table_number, items_str, total_price)
        )
        new_order_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        # 導向成功頁面，並帶上 ID 以便查詢剛點的內容
        return redirect(url_for('order_success', order_id=new_order_id))

    try:
        cur.execute("SELECT * FROM products ORDER BY category, id")
        products = cur.fetchall()
    except:
        return "請先執行 <a href='/init_db'>/init_db</a>"
        
    cur.close()
    conn.close()

    # (此處 HTML 保持原樣，僅省略部分 CSS 以節省篇幅，功能不變)
    table_input_html = f'<input type="text" name="table_number" value="{table_from_url}" readonly>' if table_from_url else '<input type="text" name="table_number" placeholder="桌號" required>'
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>線上點餐</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: sans-serif; padding: 10px; background: #f8f9fa; }}
            .menu-item {{ display: flex; align-items: center; border-bottom: 1px solid #ddd; padding: 10px 0; }}
            .menu-img {{ width: 70px; height: 70px; object-fit: cover; border-radius: 5px; margin-right: 10px; }}
            .price {{ color: #e91e63; font-weight: bold; }}
            button {{ width: 100%; padding: 15px; background: #28a745; color: white; border: none; font-size: 1.2em; border-radius: 5px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <h2 style="text-align:center">🍴 點餐系統</h2>
        <form method="POST">
            <div style="background:#fff3cd; padding:10px; margin-bottom:10px;">桌號：{table_input_html}</div>
    """
    
    current_category = ""
    for p in products:
        if p[3] != current_category:
            html += f"<h3 style='background:#e9ecef; padding:5px;'>{p[3]}</h3>"
            current_category = p[3]
        img = p[4] if p[4] else "https://via.placeholder.com/150"
        html += f"""
        <div class="menu-item">
            <img src="{img}" class="menu-img">
            <div style="flex-grow:1">
                <b>{p[1]}</b><br><span class="price">${p[2]}</span>
            </div>
            <input type="checkbox" name="items" value="{p[0]}" style="transform:scale(1.5)">
        </div>
        """

    html += """
            <button type="submit" onclick="return confirm('確認送出？')">送出訂單</button>
        </form>
    </body>
    </html>
    """
    return html

# --- 3. 下單成功 (顯示明細) ---
@app.route('/order_success')
def order_success():
    order_id = request.args.get('order_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    cur.close()
    conn.close()

    if not order:
        return "查無此訂單"

    # order: id, table, items, total, status, time
    items_list = order[2].replace(" + ", "<br>➕ ") # 讓顯示更漂亮

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: sans-serif; text-align: center; padding: 20px; background: #f4f4f9; }}
            .card {{ background: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); max-width: 400px; margin: 0 auto; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1 style="color:#28a745">✅ 下單成功</h1>
            <h3>桌號：{order[1]}</h3>
            <div style="text-align:left; background:#eee; padding:15px; margin:10px 0; border-radius:5px;">
                {items_list}
                <hr>
                <div style="text-align:right; font-weight:bold; font-size:1.2em;">總計：${order[3]}</div>
            </div>
            <p style="color:red">請至櫃台結帳，謝謝！</p>
            <a href="/">回到首頁</a>
        </div>
    </body>
    </html>
    """

# --- 4. 廚房看板 (含出餐功能、音效、報表) ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
    # 只顯示未完成的訂單，或者全部顯示但標記狀態
    # 這裡邏輯：顯示所有今日訂單，但完成的會變灰
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date ORDER BY created_at DESC")
    orders = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>廚房端</title>
        <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
        <style>
            body { font-family: sans-serif; background: #222; color: white; padding: 10px; }
            .order-card { background: #333; border-left: 10px solid #ff9800; margin-bottom: 10px; padding: 10px; border-radius: 5px; }
            .completed { border-left: 10px solid #28a745; opacity: 0.6; }
            .btn-done { background: #28a745; color: white; border: none; padding: 10px; border-radius: 5px; cursor: pointer; float: right; }
            .header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
            .report-btn { background: #007bff; color: white; text-decoration: none; padding: 10px 20px; border-radius: 5px; }
        </style>
    </head>
    <body>
        <div class="header-bar">
            <h1>👨‍🍳 廚房接單</h1>
            <div>
                <button onclick="enableAudio()" style="background:#e91e63; color:white; border:none; padding:10px;">🔊 開啟音效</button>
                <a href="/daily_report" class="report-btn" target="_blank">🖨️ 列印今日結帳單</a>
            </div>
        </div>

        <div id="order-container">
    """
    
    # 產生訂單列表
    order_count = len(orders)
    for order in orders:
        # order: id, table, items, total, status, time
        status_class = "completed" if order[4] == 'Completed' else ""
        btn_html = ""
        if order[4] != 'Completed':
            btn_html = f"<button class='btn-done' onclick=\"completeOrder({order[0]})\">出餐完成</button>"
        
        html += f"""
        <div class="order-card {status_class}">
            {btn_html}
            <div style="font-size:1.5em; color:#ff9800">桌號：{order[1]} <span style="font-size:0.6em; color:#ccc">({order[5]})</span></div>
            <div style="font-size:1.2em; margin-top:5px;">{order[2]}</div>
            <div style="text-align:right; color:#888;">${order[3]}</div>
        </div>
        """

    # 這裡加入 JavaScript：自動刷新 + 音效
    html += f"""
        </div>

        <audio id="notification-sound" src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" preload="auto"></audio>

        <script>
            // 儲存目前的訂單數量
            let currentOrderCount = {order_count};
            
            // 啟用音效 (瀏覽器限制，必須手動點一次才能自動播)
            function enableAudio() {{
                document.getElementById('notification-sound').play().then(() => {{
                    document.getElementById('notification-sound').pause();
                    alert("音效已開啟！有新單會 '叮咚' ");
                }}).catch(e => alert("請允許網站播放聲音"));
            }}

            // 標記完成
            function completeOrder(orderId) {{
                if(!confirm('確定已出餐？')) return;
                fetch('/complete/' + orderId).then(() => window.location.reload());
            }}

            // 自動刷新邏輯 (每 10 秒檢查一次)
            setInterval(() => {{
                // 這裡我們簡單做：直接刷新頁面。
                // 為了播放音效，我們可以用 localStorage 存數量，刷新後對比
                location.reload(); 
            }}, 10000);

            // 頁面載入時檢查是否要播音效
            let savedCount = localStorage.getItem('orderCount');
            if (savedCount && parseInt(savedCount) < currentOrderCount) {{
                // 如果現在的單比存的還多 -> 播聲音
                let audio = document.getElementById('notification-sound');
                audio.play().catch(e => console.log("等待使用者互動以播放音效"));
            }}
            localStorage.setItem('orderCount', currentOrderCount);
        </script>
    </body>
    </html>
    """
    return html

# --- 5. 標記訂單完成 API ---
@app.route('/complete/<int:order_id>')
def complete_order(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status = 'Completed' WHERE id = %s", (order_id,))
    conn.commit()
    cur.close()
    conn.close()
    return "OK"

# --- 6. 每日結帳單 (列印用) ---
@app.route('/daily_report')
def daily_report():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 抓取「今天」的所有訂單
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date ORDER BY id ASC")
    orders = cur.fetchall()
    
    # 計算總額
    total_revenue = sum(order[3] for order in orders)
    today_str = date.today().strftime("%Y-%m-%d")

    cur.close()
    conn.close()

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>結帳單 {today_str}</title>
        <style>
            body {{ font-family: 'Courier New', monospace; padding: 20px; max-width: 800px; margin: 0 auto; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th, td {{ border-bottom: 1px dashed #000; padding: 8px; text-align: left; }}
            .total {{ text-align: right; font-size: 1.5em; font-weight: bold; margin-top: 20px; }}
            @media print {{
                .no-print {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <button class="no-print" onclick="window.print()" style="font-size:20px; padding:10px;">🖨️ 列印此頁</button>
        
        <h2 style="text-align:center">日結帳單</h2>
        <p>日期：{today_str}</p>
        <p>總單數：{len(orders)}</p>

        <table>
            <tr>
                <th>單號</th>
                <th>桌號</th>
                <th>金額</th>
                <th>狀態</th>
            </tr>
    """
    for order in orders:
        status_text = "已完結" if order[4] == 'Completed' else "未完成"
        html += f"""
        <tr>
            <td>#{order[0]}</td>
            <td>{order[1]}</td>
            <td>${order[3]}</td>
            <td>{status_text}</td>
        </tr>
        """

    html += f"""
        </table>
        <div class="total">本日營業額：${total_revenue}</div>
    </body>
    </html>
    """
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
