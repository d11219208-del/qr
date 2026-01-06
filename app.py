import os
import psycopg2
from flask import Flask, request, redirect, url_for

app = Flask(__name__)

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 1. 資料庫初始化 (升級版：含圖片與重置功能) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 注意：為了加入圖片欄位，我們這裡會先刪除舊表格重建 (DROP TABLE)
        # 這會清空所有舊資料，請謹慎使用
        cur.execute('DROP TABLE IF EXISTS products;')
        cur.execute('DROP TABLE IF EXISTS orders;')

        # 建立菜單表 (增加 image_url 欄位)
        cur.execute('''
            CREATE TABLE products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price INTEGER NOT NULL,
                category VARCHAR(50),
                image_url TEXT
            );
        ''')
        
        # 建立訂單表
        cur.execute('''
            CREATE TABLE orders (
                id SERIAL PRIMARY KEY,
                table_number VARCHAR(10),
                items TEXT NOT NULL, 
                total_price INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # 預設菜單 (包含 ImgBB 或網路示意圖連結)
        default_menu = [
            ('炒米粉', 50, '主食', 'https://ibb.co/Q7DRP3cY'),
            ('古早味排骨飯', 120, '主食', 'https://i.ibb.co/MCTvVqL/pork-rice.jpg'),
            ('燙青菜', 40, '小菜', 'https://i.ibb.co/Xkz2zt3/vegetables.jpg'),
            ('滷蛋', 15, '小菜', 'https://i.ibb.co/hWz6qg8/egg.jpg'),
            ('珍珠奶茶', 60, '飲料', 'https://i.ibb.co/JtdjvX3/bubble-tea.jpg'),
            ('冰紅茶', 30, '飲料', 'https://i.ibb.co/jyn2V2t/black-tea.jpg')
        ]
        cur.executemany('INSERT INTO products (name, price, category, image_url) VALUES (%s, %s, %s, %s)', default_menu)

        conn.commit()
        return "系統升級成功！資料表已重建（含圖片欄位）。<br><a href='/'>前往點餐首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 點餐首頁 (顧客端) ---
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_db_connection()
    cur = conn.cursor()

    # 自動抓取網址中的桌號參數，例如 /?table=A1
    table_from_url = request.args.get('table', '')

    # 處理訂單送出
    if request.method == 'POST':
        table_number = request.form.get('table_number')
        selected_item_ids = request.form.getlist('items')
        
        if not selected_item_ids:
            return "錯誤：您沒有選擇任何餐點。<a href='/'>重試</a>"

        total_price = 0
        ordered_items_names = []
        
        for pid in selected_item_ids:
            cur.execute("SELECT name, price FROM products WHERE id = %s", (pid,))
            product = cur.fetchone()
            if product:
                ordered_items_names.append(product[0])
                total_price += product[1]
        
        items_str = ", ".join(ordered_items_names)

        cur.execute(
            "INSERT INTO orders (table_number, items, total_price) VALUES (%s, %s, %s)",
            (table_number, items_str, total_price)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        # 導向到「結帳提示頁面」，而不是廚房
        return redirect(url_for('order_success', total=total_price))

    # 顯示菜單
    try:
        cur.execute("SELECT * FROM products ORDER BY category, id")
        products = cur.fetchall()
    except:
        return "資料庫需更新，請先執行 <a href='/init_db'>/init_db</a>"
        
    cur.close()
    conn.close()

    # 根據是否有桌號，決定輸入框的狀態
    table_input_html = ""
    if table_from_url:
        # 如果網址有桌號，就鎖定輸入框，不讓客人改
        table_input_html = f"""
        <div class="input-group">
            <label>目前桌號：</label>
            <input type="text" name="table_number" value="{table_from_url}" readonly style="background-color:#e9ecef; border:1px solid #ced4da;">
        </div>
        """
    else:
        # 如果網址沒桌號（例如外帶），留空給客人填
        table_input_html = """
        <div class="input-group">
            <label>桌號 / 外帶號碼：</label>
            <input type="text" name="table_number" placeholder="請輸入桌號" required>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>線上點餐</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: '微軟正黑體', sans-serif; background-color: #f8f9fa; padding: 10px; margin: 0; }}
            .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 15px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }}
            h1 {{ text-align: center; color: #333; margin-top: 0; }}
            
            /* 菜單項目樣式 */
            .menu-item {{ display: flex; align-items: center; border-bottom: 1px solid #f0f0f0; padding: 15px 0; }}
            .menu-img {{ width: 80px; height: 80px; object-fit: cover; border-radius: 8px; margin-right: 15px; background-color: #eee; }}
            .menu-info {{ flex-grow: 1; }}
            .menu-name {{ font-size: 1.1em; font-weight: bold; color: #333; }}
            .menu-price {{ color: #e91e63; font-weight: bold; margin-top: 5px; }}
            .menu-check {{ transform: scale(1.5); margin-left: 10px; cursor: pointer; }}
            
            /* 輸入框與按鈕 */
            .input-group {{ margin-bottom: 20px; background: #fff3cd; padding: 10px; border-radius: 8px; }}
            .input-group label {{ display: block; margin-bottom: 5px; font-weight: bold; color: #856404; }}
            input[type="text"] {{ width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }}
            
            .submit-btn {{ 
                display: block; width: 100%; padding: 15px; 
                background-color: #28a745; color: white; border: none; 
                font-size: 20px; font-weight: bold; border-radius: 50px; 
                cursor: pointer; margin-top: 20px; position: sticky; bottom: 20px; 
                box-shadow: 0 4px 6px rgba(0,0,0,0.2);
            }}
            .category-title {{ background-color: #f8f9fa; padding: 8px 5px; margin-top: 10px; border-left: 4px solid #28a745; color: #555; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🍴 歡迎點餐</h1>
            <form method="POST">
                {table_input_html}
                
    """
    
    current_category = ""
    for p in products:
        # p = (id, name, price, category, image_url)
        if p[3] != current_category:
            html += f"<div class='category-title'><b>{p[3]}類</b></div>"
            current_category = p[3]
        
        # 處理圖片，如果沒有網址就用預設圖
        img_src = p[4] if p[4] else "https://via.placeholder.com/150?text=No+Image"

        html += f"""
        <div class="menu-item">
            <img src="{img_src}" class="menu-img" alt="{p[1]}">
            <div class="menu-info">
                <div class="menu-name">{p[1]}</div>
                <div class="menu-price">${p[2]}</div>
            </div>
            <input type="checkbox" name="items" value="{p[0]}" class="menu-check">
        </div>
        """

    html += """
                <button type="submit" class="submit-btn" onclick="return confirm('確定要送出訂單嗎？');">送出訂單 ($)</button>
            </form>
        </div>
    </body>
    </html>
    """
    return html

# --- 3. 下單成功頁面 (顧客看到這個) ---
@app.route('/order_success')
def order_success():
    total = request.args.get('total', 0)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: '微軟正黑體', sans-serif; text-align: center; padding: 50px 20px; background-color: #f4f4f9; }}
            .card {{ background: white; padding: 40px; border-radius: 15px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); max-width: 400px; margin: 0 auto; }}
            .icon {{ font-size: 80px; color: #28a745; margin-bottom: 20px; }}
            h1 {{ margin: 0; color: #333; }}
            p {{ color: #666; font-size: 1.2em; margin: 20px 0; }}
            .price {{ font-size: 2em; color: #e91e63; font-weight: bold; margin: 20px 0; }}
            .btn {{ display: inline-block; padding: 10px 30px; background: #007bff; color: white; text-decoration: none; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">✅</div>
            <h1>下單成功！</h1>
            <div class="price">總金額：${total}</div>
            <p>廚房已收到您的訂單。<br><b>請先至櫃台結帳，謝謝！</b></p>
        </div>
    </body>
    </html>
    """

# --- 4. 廚房看板 (隱藏入口，只有店家知道網址) ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
    # 這裡的邏輯不變
    try:
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC")
        orders = cur.fetchall()
    except:
        return "資料庫錯誤"
    cur.close()
    conn.close()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>廚房看板</title>
        <meta http-equiv="refresh" content="10">
        <style>
            body { font-family: '微軟正黑體', sans-serif; background-color: #222; color: white; padding: 20px; }
            .order-card { background-color: #333; border-left: 10px solid #ff9800; margin-bottom: 15px; padding: 15px; border-radius: 5px; }
            .order-header { display: flex; justify-content: space-between; font-size: 1.5em; font-weight: bold; border-bottom: 1px solid #555; padding-bottom: 10px; margin-bottom: 10px; }
            .table-num { color: #ff9800; }
            .items { font-size: 1.3em; line-height: 1.5; color: #fff; }
            .time { color: #888; font-size: 0.8em; margin-top: 10px; text-align: right;}
        </style>
    </head>
    <body>
        <h1 style="text-align:center;">👨‍🍳 廚房接單中</h1>
    """
    
    if not orders:
        html += "<h3 style='text-align:center; color:#777;'>目前沒有訂單...</h3>"

    for order in orders:
        html += f"""
        <div class="order-card">
            <div class="order-header">
                <span class="table-num">桌號：{order[1]}</span>
                <span>${order[3]}</span>
            </div>
            <div class="items">
                {order[2]}
            </div>
            <div class="time">{order[5]}</div>
        </div>
        """
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
