import os
import psycopg2
from flask import Flask, request, redirect, url_for
from datetime import datetime, date

app = Flask(__name__)

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 1. 資料庫初始化 (保留原邏輯 + 自動升級欄位) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. 建立基礎表格 (如果不存在)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price INTEGER NOT NULL,
                category VARCHAR(50),
                image_url TEXT,
                is_available BOOLEAN DEFAULT TRUE
            );
        ''')
        
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

        # 2. 自動升級舊資料庫 (嘗試加入 is_available 欄位)
        # 這是為了讓您舊的資料表也能支援「完售」功能
        try:
            cur.execute("ALTER TABLE products ADD COLUMN is_available BOOLEAN DEFAULT TRUE;")
            conn.commit()
        except psycopg2.errors.DuplicateColumn:
            conn.rollback() # 如果欄位已經存在，就忽略錯誤

        # 3. 如果是完全空的資料庫，才插入預設菜單
        cur.execute('SELECT count(*) FROM products;')
        if cur.fetchone()[0] == 0:
            default_menu = [
                ('招牌牛肉麵', 180, '主食', 'https://i.ibb.co/vz1k3j1/beef-noodle.jpg', True),
                ('古早味排骨飯', 120, '主食', 'https://i.ibb.co/MCTvVqL/pork-rice.jpg', True),
                ('燙青菜', 40, '小菜', 'https://i.ibb.co/Xkz2zt3/vegetables.jpg', True),
                ('滷蛋', 15, '小菜', 'https://i.ibb.co/hWz6qg8/egg.jpg', True),
                ('珍珠奶茶', 60, '飲料', 'https://i.ibb.co/JtdjvX3/bubble-tea.jpg', True),
                ('冰紅茶', 30, '飲料', 'https://i.ibb.co/jyn2V2t/black-tea.jpg', True)
            ]
            cur.executemany('INSERT INTO products (name, price, category, image_url, is_available) VALUES (%s, %s, %s, %s, %s)', default_menu)
            conn.commit()

        return "資料庫初始化/升級完成！舊資料已保留。<br><a href='/'>前往首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 顧客端點餐首頁 ---
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
        
        for pid in selected_item_ids:
            # 只允許購買上架中的商品 (is_available = TRUE)
            cur.execute("SELECT name, price FROM products WHERE id = %s AND is_available = TRUE", (pid,))
            product = cur.fetchone()
            if product:
                ordered_items_names.append(f"{product[0]} (${product[1]})")
                total_price += product[1]
        
        if not ordered_items_names:
            return "錯誤：您選的商品可能已完售。<a href='/'>重試</a>"

        items_str = " + ".join(ordered_items_names)

        cur.execute(
            "INSERT INTO orders (table_number, items, total_price) VALUES (%s, %s, %s) RETURNING id",
            (table_number, items_str, total_price)
        )
        new_order_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('order_success', order_id=new_order_id))

    # 抓取所有商品 (包含完售的，以便顯示「已售完」)
    try:
        cur.execute("SELECT * FROM products ORDER BY category, id")
        products = cur.fetchall()
    except:
        return "系統更新中，請先執行 <a href='/init_db'>/init_db</a>"
        
    cur.close()
    conn.close()

    table_input_html = f'<input type="text" name="table_number" value="{table_from_url}" readonly>' if table_from_url else '<input type="text" name="table_number" placeholder="桌號" required>'
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>線上點餐</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: sans-serif; padding: 10px; background: #f8f9fa; margin: 0; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 15px; border-radius: 8px; }}
            .menu-item {{ display: flex; align-items: center; border-bottom: 1px solid #eee; padding: 15px 0; }}
            .menu-img {{ width: 80px; height: 80px; object-fit: cover; border-radius: 8px; margin-right: 15px; }}
            .price {{ color: #e91e63; font-weight: bold; }}
            
            /* 完售樣式 */
            .sold-out {{ opacity: 0.5; background-color: #f9f9f9; pointer-events: none; }}
            .sold-out-badge {{ background: #999; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; margin-left: 5px; }}
            
            .category-title {{ background: #e9ecef; padding: 8px; margin-top: 20px; border-left: 4px solid #28a745; font-weight: bold; }}
            button {{ width: 100%; padding: 15px; background: #28a745; color: white; border: none; font-size: 1.2em; border-radius: 5px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2 style="text-align:center">🍴 歡迎點餐</h2>
            <form method="POST">
                <div style="background:#fff3cd; padding:10px; margin-bottom:10px; border-radius:5px;">桌號：{table_input_html}</div>
    """
    
    current_category = ""
    for p in products:
        # p: id, name, price, category, image_url, is_available
        # 注意：如果不小心沒有 is_available 欄位 (舊資料)，預設為 True
        is_available = p[5] if len(p) > 5 else True
        
        if p[3] != current_category:
            html += f"<div class='category-title'>{p[3]}</div>"
            current_category = p[3]
            
        img = p[4] if p[4] else "https://via.placeholder.com/150"
        
        sold_out_class = "" if is_available else "sold-out"
        sold_out_text = "" if is_available else "<span class='sold-out-badge'>已售完</span>"
        checkbox_disabled = "" if is_available else "disabled"
        
        html += f"""
        <div class="menu-item {sold_out_class}">
            <img src="{img}" class="menu-img">
            <div style="flex-grow:1">
                <b>{p[1]}</b> {sold_out_text}<br>
                <span class="price">${p[2]}</span>
            </div>
            <input type="checkbox" name="items" value="{p[0]}" style="transform:scale(1.5)" {checkbox_disabled}>
        </div>
        """

    html += """
                <button type="submit" onclick="return confirm('確認送出訂單？')">送出訂單</button>
            </form>
        </div>
    </body>
    </html>
    """
    return html

# --- 3. 下單成功頁面 ---
@app.route('/order_success')
def order_success():
    order_id = request.args.get('order_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    cur.close()
    conn.close()

    if not order: return "查無此訂單"
    items_list = order[2].replace(" + ", "<br>➕ ")

    return f"""
    <!DOCTYPE html>
    <html>
    <head> <meta name="viewport" content="width=device-width, initial-scale=1"> </head>
    <body style="font-family: sans-serif; text-align: center; padding: 20px; background: #f4f4f9;">
        <div style="background: white; padding: 20px; border-radius: 10px; max-width: 400px; margin: 0 auto;">
            <h1 style="color:#28a745">✅ 下單成功</h1>
            <h3>桌號：{order[1]}</h3>
            <div style="text-align:left; background:#eee; padding:15px; margin:10px 0;">{items_list}<hr><div style="text-align:right; font-weight:bold;">總計：${order[3]}</div></div>
            <p style="color:red">請至櫃台結帳，謝謝！</p>
            <a href="/">回到首頁</a>
        </div>
    </body>
    </html>
    """

# --- 4. 廚房訂單看板 ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
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
            .btn-done { background: #28a745; color: white; border: none; padding: 8px; border-radius: 5px; cursor: pointer; float: right; }
            .header-bar { display: flex; justify-content: space-between; align-items: center; }
            a { color: #4CAF50; text-decoration: none; margin-left: 10px; }
            .nav-btn { background: #007bff; color: white; padding: 8px 15px; border-radius: 5px; }
        </style>
    </head>
    <body>
        <div class="header-bar">
            <h2>👨‍🍳 訂單看板</h2>
            <div>
                <button onclick="enableAudio()" style="background:#e91e63; border:none; color:white; padding:8px;">🔊 開聲音</button>
                <a href="/kitchen/menu" class="nav-btn" style="background:#673ab7;">🛠️ 管理菜單</a>
                <a href="/daily_report" class="nav-btn" target="_blank">🖨️ 結帳單</a>
            </div>
        </div>
        <hr style="border-color:#444;">
        
        <div id="order-container">
    """
    
    for order in orders:
        status_class = "completed" if order[4] == 'Completed' else ""
        btn_html = f"<button class='btn-done' onclick=\"completeOrder({order[0]})\">完成</button>" if order[4] != 'Completed' else ""
        html += f"""
        <div class="order-card {status_class}">
            {btn_html}
            <div style="font-size:1.4em; color:#ff9800">桌號：{order[1]} <span style="font-size:0.6em; color:#ccc">({order[5]})</span></div>
            <div style="font-size:1.1em; margin-top:5px;">{order[2]}</div>
            <div style="text-align:right; color:#888;">${order[3]}</div>
        </div>
        """

    html += f"""
        </div>
        <audio id="notification-sound" src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" preload="auto"></audio>
        <script>
            let currentOrderCount = {len(orders)};
            function enableAudio() {{ document.getElementById('notification-sound').play().catch(e=>alert("請允許播放")); alert("音效已開啟"); }}
            function completeOrder(id) {{ if(confirm('確定完成？')) fetch('/complete/'+id).then(()=>location.reload()); }}
            setInterval(() => location.reload(), 10000);
            
            let savedCount = localStorage.getItem('orderCount');
            if (savedCount && parseInt(savedCount) < currentOrderCount) {{
                document.getElementById('notification-sound').play().catch(e=>console.log("需互動"));
            }}
            localStorage.setItem('orderCount', currentOrderCount);
        </script>
    </body>
    </html>
    """
    return html

# --- 5. [新功能] 菜單管理後台 ---
@app.route('/kitchen/menu', methods=['GET', 'POST'])
def kitchen_menu():
    conn = get_db_connection()
    cur = conn.cursor()

    # 新增菜色
    if request.method == 'POST' and 'add_item' in request.form:
        name = request.form['name']
        price = request.form['price']
        category = request.form['category']
        image_url = request.form['image_url']
        cur.execute("INSERT INTO products (name, price, category, image_url, is_available) VALUES (%s, %s, %s, %s, TRUE)", 
                    (name, price, category, image_url))
        conn.commit()
        return redirect(url_for('kitchen_menu'))

    cur.execute("SELECT * FROM products ORDER BY category, id")
    products = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>菜單管理</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; background: #f4f4f9; }
            h2 { border-bottom: 2px solid #ddd; padding-bottom: 10px; }
            .form-box { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 20px; }
            input, select { padding: 8px; margin: 5px 0; width: 100%; box-sizing: border-box; }
            table { width: 100%; border-collapse: collapse; background: white; }
            th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
            th { background: #eee; }
            .btn { padding: 5px 10px; text-decoration: none; color: white; border-radius: 4px; display: inline-block; margin: 2px; }
            .btn-edit { background: #ff9800; }
            .btn-del { background: #f44336; }
            .btn-stock { background: #28a745; }
            .btn-soldout { background: #9e9e9e; }
            .nav-link { font-size: 1.2em; text-decoration: none; color: #007bff; margin-bottom: 20px; display: inline-block; }
        </style>
    </head>
    <body>
        <a href="/kitchen" class="nav-link">⬅️ 回廚房看板</a>
        <h2>🛠️ 菜單管理</h2>

        <div class="form-box">
            <h3>➕ 新增菜色</h3>
            <form method="POST">
                <input type="hidden" name="add_item" value="1">
                <label>名稱：</label><input type="text" name="name" required>
                <label>價格：</label><input type="number" name="price" required>
                <label>分類：</label><input type="text" name="category" placeholder="例如：主食、飲料" required>
                <label>圖片網址 (ImgBB)：</label><input type="text" name="image_url" placeholder="https://...">
                <button type="submit" style="background:#007bff; color:white; border:none; padding:10px; width:100%; margin-top:10px; border-radius:5px; cursor:pointer;">新增</button>
            </form>
        </div>

        <h3>📋 現有菜單</h3>
        <table>
            <tr>
                <th>圖片</th>
                <th>名稱/分類</th>
                <th>價格</th>
                <th>狀態/操作</th>
            </tr>
    """
    
    for p in products:
        # p: id, name, price, category, image_url, is_available
        is_avail = p[5] if len(p) > 5 else True
        stock_btn = f'<a href="/menu/toggle/{p[0]}" class="btn btn-soldout">設為完售</a>' if is_avail else f'<a href="/menu/toggle/{p[0]}" class="btn btn-stock">設為上架</a>'
        status_text = "<span style='color:green'>販售中</span>" if is_avail else "<span style='color:red'>已售完</span>"
        
        img_src = p[4] if p[4] else ""
        
        html += f"""
        <tr>
            <td><img src="{img_src}" style="width:50px; height:50px; object-fit:cover;"></td>
            <td><b>{p[1]}</b><br><small>{p[3]}</small></td>
            <td>${p[2]}</td>
            <td>
                {status_text}<br>
                {stock_btn}
                <a href="/menu/edit/{p[0]}" class="btn btn-edit">編輯</a>
                <a href="/menu/delete/{p[0]}" class="btn btn-del" onclick="return confirm('確定刪除？')">刪除</a>
            </td>
        </tr>
        """

    html += "</table></body></html>"
    return html

# --- 6. 菜單操作 API (切換狀態/刪除/編輯) ---
@app.route('/menu/toggle/<int:pid>')
def menu_toggle(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    # 切換 TRUE/FALSE
    cur.execute("UPDATE products SET is_available = NOT is_available WHERE id = %s", (pid,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('kitchen_menu'))

@app.route('/menu/delete/<int:pid>')
def menu_delete(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('kitchen_menu'))

@app.route('/menu/edit/<int:pid>', methods=['GET', 'POST'])
def menu_edit(pid):
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        name = request.form['name']
        price = request.form['price']
        category = request.form['category']
        image_url = request.form['image_url']
        cur.execute("UPDATE products SET name=%s, price=%s, category=%s, image_url=%s WHERE id=%s",
                    (name, price, category, image_url, pid))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('kitchen_menu'))

    cur.execute("SELECT * FROM products WHERE id = %s", (pid,))
    p = cur.fetchone()
    cur.close()
    conn.close()

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="font-family: sans-serif; padding: 20px; max-width: 600px; margin: 0 auto;">
        <h2>✏️ 編輯菜色</h2>
        <form method="POST">
            <p>名稱：<input type="text" name="name" value="{p[1]}" required style="width:100%; padding:8px;"></p>
            <p>價格：<input type="number" name="price" value="{p[2]}" required style="width:100%; padding:8px;"></p>
            <p>分類：<input type="text" name="category" value="{p[3]}" required style="width:100%; padding:8px;"></p>
            <p>圖片：<input type="text" name="image_url" value="{p[4]}" style="width:100%; padding:8px;"></p>
            <button type="submit" style="background:#ff9800; color:white; border:none; padding:10px 20px; border-radius:5px; cursor:pointer;">儲存修改</button>
            <a href="/kitchen/menu" style="margin-left:10px;">取消</a>
        </form>
    </body>
    </html>
    """

# --- 7. 其他 API (完成訂單/報表) ---
@app.route('/complete/<int:order_id>')
def complete_order(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status = 'Completed' WHERE id = %s", (order_id,))
    conn.commit()
    cur.close()
    conn.close()
    return "OK"

@app.route('/daily_report')
def daily_report():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date ORDER BY id ASC")
    orders = cur.fetchall()
    total_revenue = sum(order[3] for order in orders)
    today_str = date.today().strftime("%Y-%m-%d")
    cur.close()
    conn.close()

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: monospace; padding: 20px;">
        <button onclick="window.print()">列印</button>
        <h2 style="text-align:center">日結帳單 {today_str}</h2>
        <table style="width:100%; border-collapse:collapse;">
            <tr><th style="border-bottom:1px dashed #000; text-align:left;">桌號/單號</th><th style="border-bottom:1px dashed #000; text-align:right;">金額</th></tr>
    """
    for order in orders:
        html += f"<tr><td style='padding:5px 0;'>#{order[0]} 桌:{order[1]}</td><td style='text-align:right;'>${order[3]}</td></tr>"
    html += f"</table><h3 style='text-align:right;'>總計：${total_revenue}</h3></body></html>"
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
