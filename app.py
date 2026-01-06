import os
import psycopg2
import json
import time
import threading
import requests
from flask import Flask, request, redirect, url_for, jsonify
from datetime import datetime, date

app = Flask(__name__)

# --- 1. 防止 Render 休眠機制 (Self-Ping) ---
def keep_alive():
    """每 14 分鐘自我 Ping 一次，防止 Render 進入休眠"""
    while True:
        try:
            time.sleep(14 * 60)  # 14分鐘
            # 替換成您自己的 Render 網址，如果還不知道，先用 localhost 測試
            # 注意：Render 免費版最好的防休眠方式還是使用 UptimeRobot 外部 Ping
            print("正在執行自我喚醒...")
            requests.get("http://127.0.0.1:10000/") 
        except Exception as e:
            print(f"喚醒失敗 (可能是剛啟動或是網址未設定): {e}")

# 啟動背景執行緒
threading.Thread(target=keep_alive, daemon=True).start()

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 2. 資料庫初始化 (修改：會清空訂單記錄) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 建立/確保 products 表格存在
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price INTEGER NOT NULL,
                category VARCHAR(50),
                image_url TEXT,
                is_available BOOLEAN DEFAULT TRUE,
                custom_options TEXT
            );
        ''')
        
        # 建立/確保 orders 表格存在
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

        # --- 重要：清空所有訂單記錄 (User要求) ---
        cur.execute("DELETE FROM orders;") 
        
        # 補足欄位 (針對舊資料庫升級)
        try:
            cur.execute("ALTER TABLE products ADD COLUMN custom_options TEXT;")
        except:
            conn.rollback()

        # 預設菜單 (如果產品表是空的才加)
        cur.execute('SELECT count(*) FROM products;')
        if cur.fetchone()[0] == 0:
            default_menu = [
                ('招牌牛肉麵', 180, '主食', 'https://i.ibb.co/vz1k3j1/beef-noodle.jpg', True, '不要蔥,加辣,麵軟,麵硬'),
                ('古早味排骨飯', 120, '主食', 'https://i.ibb.co/MCTvVqL/pork-rice.jpg', True, '半飯,多汁'),
                ('燙青菜', 40, '小菜', 'https://i.ibb.co/Xkz2zt3/vegetables.jpg', True, '不要蒜,醬油少,清燙'),
                ('珍珠奶茶', 60, '飲料', 'https://i.ibb.co/JtdjvX3/bubble-tea.jpg', True, '半糖,微糖,無糖,去冰,少冰')
            ]
            cur.executemany('INSERT INTO products (name, price, category, image_url, is_available, custom_options) VALUES (%s, %s, %s, %s, %s, %s)', default_menu)
            conn.commit()

        conn.commit()
        return "資料庫初始化完成！<br><b>訂單記錄已清空</b>。<br><a href='/'>前往首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 3. 顧客端首頁 (購物車模式) ---
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    table_from_url = request.args.get('table', '')

    if request.method == 'POST':
        # 這裡接收的是 JSON 格式的購物車資料
        table_number = request.form.get('table_number')
        cart_json = request.form.get('cart_data')
        
        if not cart_json:
            return "錯誤：購物車是空的"

        cart_items = json.loads(cart_json) # 解析 JSON
        
        total_price = 0
        items_display_list = []

        # 處理購物車內的每一項
        for item in cart_items:
            # item = {'id': 1, 'name': '牛肉麵', 'price': 180, 'options': ['加辣'], 'qty': 1}
            p_name = item['name']
            p_price = int(item['price'])
            p_qty = int(item['qty'])
            p_opts = item.get('options', [])
            
            # 組合顯示字串： 牛肉麵 (加辣) x 1
            opts_str = f"({','.join(p_opts)})" if p_opts else ""
            display_str = f"{p_name} {opts_str} x{p_qty}"
            
            items_display_list.append(display_str)
            total_price += (p_price * p_qty)

        items_final_str = " + ".join(items_display_list)

        cur.execute(
            "INSERT INTO orders (table_number, items, total_price) VALUES (%s, %s, %s) RETURNING id",
            (table_number, items_final_str, total_price)
        )
        new_order_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('order_success', order_id=new_order_id))

    # 抓取產品
    try:
        cur.execute("SELECT * FROM products ORDER BY category, id")
        products = cur.fetchall()
    except:
        return "請先執行 <a href='/init_db'>/init_db</a>"
    
    cur.close()
    conn.close()
    
    # 將產品資料轉為 JSON 給前端 JavaScript 使用
    products_list = []
    for p in products:
        products_list.append({
            'id': p[0], 'name': p[1], 'price': p[2], 'category': p[3],
            'image_url': p[4] if p[4] else "https://via.placeholder.com/150",
            'is_available': p[5],
            'custom_options': p[6].split(',') if p[6] else []
        })

    return render_frontend(table_from_url, products_list)

def render_frontend(table_number, products_data):
    # 這是前端頁面的 HTML 結構
    products_json = json.dumps(products_data)
    
    table_input = f'<input type="text" id="table_number" name="table_number" value="{table_number}" readonly>' if table_number else '<input type="text" id="table_number" name="table_number" placeholder="請輸入桌號" required>'

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>線上點餐</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
        <style>
            body {{ font-family: 'Microsoft JhengHei', sans-serif; margin: 0; padding-bottom: 80px; background: #f4f7f6; }}
            .header {{ background: white; padding: 15px; text-align: center; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .container {{ padding: 10px; max-width: 600px; margin: 0 auto; }}
            
            /* 產品卡片 */
            .menu-item {{ background: white; border-radius: 12px; padding: 10px; display: flex; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
            .menu-img {{ width: 90px; height: 90px; border-radius: 8px; object-fit: cover; background: #eee; flex-shrink: 0; }}
            .menu-info {{ flex-grow: 1; padding-left: 15px; display: flex; flex-direction: column; justify-content: space-between; }}
            .menu-name {{ font-weight: bold; font-size: 1.1em; }}
            .menu-price {{ color: #e91e63; font-weight: bold; }}
            
            /* 按鈕 */
            .add-btn {{ background: #28a745; color: white; border: none; padding: 8px 15px; border-radius: 20px; font-weight: bold; cursor: pointer; align-self: flex-end; }}
            .sold-out {{ background: #ccc; cursor: not-allowed; }}
            
            /* 彈跳視窗 (Modal) */
            .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: none; z-index: 999; justify-content: center; align-items: flex-end; }}
            .modal-content {{ background: white; width: 100%; max-width: 600px; border-radius: 20px 20px 0 0; padding: 20px; box-sizing: border-box; animation: slideUp 0.3s; }}
            @keyframes slideUp {{ from {{ transform: translateY(100%); }} to {{ transform: translateY(0); }} }}
            
            .option-tag {{ display: inline-block; border: 1px solid #ddd; padding: 8px 15px; border-radius: 20px; margin: 5px 5px 5px 0; color: #555; cursor: pointer; }}
            .option-tag.selected {{ background: #e3f2fd; border-color: #2196f3; color: #2196f3; font-weight: bold; }}
            
            /* 購物車底部 */
            .cart-bar {{ position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 15px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; box-sizing: border-box; }}
            .cart-info {{ font-weight: bold; font-size: 1.2em; }}
            .checkout-btn {{ background: #28a745; color: white; border: none; padding: 12px 30px; border-radius: 50px; font-size: 1.1em; font-weight: bold; }}
            
            /* 數量控制器 */
            .qty-control {{ display: flex; align-items: center; margin-top: 15px; }}
            .qty-btn {{ width: 35px; height: 35px; border-radius: 50%; border: 1px solid #ddd; background: white; font-size: 1.2em; display:flex; align-items:center; justify-content:center; cursor:pointer; }}
            .qty-val {{ margin: 0 15px; font-size: 1.2em; font-weight: bold; }}

        </style>
    </head>
    <body>
        <div class="header">
            <h3>🍴 歡迎點餐</h3>
            <div style="background:#f1f1f1; padding:10px; border-radius:8px;">桌號：{table_input}</div>
        </div>

        <div class="container" id="menu-container">
            </div>
        
        <div style="height: 80px;"></div>

        <form method="POST" id="order-form">
            <input type="hidden" name="cart_data" id="cart_data_input">
            <div class="cart-bar" id="cart-bar" style="display:none;">
                <div class="cart-info">
                    <span id="total-qty" style="background:#e91e63; color:white; padding:2px 8px; border-radius:10px; font-size:0.8em;">0</span>
                    合計: $<span id="total-price">0</span>
                </div>
                <button type="button" class="checkout-btn" onclick="submitOrder()">去結帳</button>
            </div>
        </form>

        <div class="modal-overlay" id="modal">
            <div class="modal-content">
                <h3 id="modal-title">菜名</h3>
                <div style="color:#e91e63; font-weight:bold; margin-bottom:10px;">$<span id="modal-price">0</span></div>
                
                <div id="modal-options-area"></div>
                
                <div class="qty-control">
                    <div class="qty-btn" onclick="changeQty(-1)">-</div>
                    <span class="qty-val" id="modal-qty">1</span>
                    <div class="qty-btn" onclick="changeQty(1)">+</div>
                </div>

                <button style="width:100%; background:#28a745; color:white; padding:15px; border:none; border-radius:10px; margin-top:20px; font-size:1.1em;" onclick="addToCartConfirm()">加入購物車</button>
                <button style="width:100%; background:white; color:#666; padding:10px; border:none; margin-top:5px;" onclick="closeModal()">取消</button>
            </div>
        </div>

        <script>
            // 後端傳來的菜單資料
            const products = {products_json};
            let cart = []; // 購物車陣列
            let currentItem = null; // 當前正在編輯的商品
            let currentQty = 1;
            let currentOptions = [];

            // 1. 渲染菜單
            const container = document.getElementById('menu-container');
            let currentCat = "";
            
            products.forEach(p => {{
                if(p.category !== currentCat) {{
                    const title = document.createElement('div');
                    title.innerHTML = `<b>${{p.category}}</b>`;
                    title.style.margin = "20px 5px 10px";
                    title.style.color = "#666";
                    container.appendChild(title);
                    currentCat = p.category;
                }}

                const el = document.createElement('div');
                el.className = 'menu-item';
                
                let btnHtml = '';
                if(p.is_available) {{
                    btnHtml = `<button class="add-btn" onclick="openModal(${{p.id}})">加入</button>`;
                }} else {{
                    btnHtml = `<button class="add-btn sold-out" disabled>已售完</button>`;
                }}

                el.innerHTML = `
                    <img src="${{p.image_url}}" class="menu-img">
                    <div class="menu-info">
                        <div>
                            <div class="menu-name">${{p.name}}</div>
                            <div class="menu-price">$${{p.price}}</div>
                        </div>
                        ${{btnHtml}}
                    </div>
                `;
                container.appendChild(el);
            }});

            // 2. 打開彈跳視窗
            function openModal(id) {{
                currentItem = products.find(p => p.id === id);
                currentQty = 1;
                currentOptions = [];
                
                document.getElementById('modal-title').innerText = currentItem.name;
                document.getElementById('modal-price').innerText = currentItem.price;
                document.getElementById('modal-qty').innerText = 1;
                
                // 渲染選項
                const optArea = document.getElementById('modal-options-area');
                optArea.innerHTML = '';
                
                if (currentItem.custom_options && currentItem.custom_options.length > 0) {{
                    optArea.innerHTML = '<p style="font-size:0.9em; color:#888;">客製化選項：</p>';
                    currentItem.custom_options.forEach(opt => {{
                        opt = opt.trim();
                        if(!opt) return;
                        const tag = document.createElement('div');
                        tag.className = 'option-tag';
                        tag.innerText = opt;
                        tag.onclick = function() {{
                            // 切換選取狀態
                            if(currentOptions.includes(opt)) {{
                                currentOptions = currentOptions.filter(o => o !== opt);
                                tag.classList.remove('selected');
                            }} else {{
                                currentOptions.push(opt);
                                tag.classList.add('selected');
                            }}
                        }};
                        optArea.appendChild(tag);
                    }});
                }}

                document.getElementById('modal').style.display = 'flex';
            }}

            function closeModal() {{
                document.getElementById('modal').style.display = 'none';
            }}

            function changeQty(n) {{
                if(currentQty + n >= 1) {{
                    currentQty += n;
                    document.getElementById('modal-qty').innerText = currentQty;
                }}
            }}

            // 3. 加入購物車
            function addToCartConfirm() {{
                cart.push({{
                    id: currentItem.id,
                    name: currentItem.name,
                    price: currentItem.price,
                    qty: currentQty,
                    options: [...currentOptions] // 複製陣列
                }});
                closeModal();
                updateCartBar();
            }}

            // 4. 更新底部購物車顯示
            function updateCartBar() {{
                const bar = document.getElementById('cart-bar');
                if(cart.length > 0) {{
                    bar.style.display = 'flex';
                    const totalP = cart.reduce((acc, item) => acc + (item.price * item.qty), 0);
                    const totalQ = cart.reduce((acc, item) => acc + item.qty, 0);
                    document.getElementById('total-price').innerText = totalP;
                    document.getElementById('total-qty').innerText = totalQ;
                }} else {{
                    bar.style.display = 'none';
                }}
            }}

            // 5. 送出訂單
            function submitOrder() {{
                const tableVal = document.getElementById('table_number').value;
                if(!tableVal) {{ alert('請輸入桌號'); return; }}
                if(cart.length === 0) return;
                
                if(!confirm(`確定送出訂單？\\n共 ${{cart.length}} 項餐點`)) return;

                // 將購物車轉成 JSON 字串填入 hidden input
                document.getElementById('cart_data_input').value = JSON.stringify(cart);
                document.getElementById('order-form').submit();
            }}
        </script>
    </body>
    </html>
    """

# --- 4. 下單成功頁面 ---
@app.route('/order_success')
def order_success():
    order_id = request.args.get('order_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    cur.close()
    conn.close()

    if not order: return "查無訂單"
    
    # 美化顯示
    items_html = order[2].replace(" + ", "<br><hr style='border:0; border-top:1px dashed #eee; margin:5px 0;'>")

    return f"""
    <!DOCTYPE html>
    <html>
    <head> <meta name="viewport" content="width=device-width, initial-scale=1"> </head>
    <body style="font-family: sans-serif; text-align: center; padding: 20px; background: #f4f7f6;">
        <div style="background: white; padding: 30px; border-radius: 15px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); max-width: 400px; margin: 0 auto;">
            <div style="font-size:50px; color:#28a745;">✅</div>
            <h2>下單成功！</h2>
            <h3 style="color:#ff9800;">桌號：{order[1]}</h3>
            <div style="text-align:left; background:#fafafa; padding:15px; border-radius:8px; margin:15px 0; font-size:1.1em;">
                {items_html}
            </div>
            <h3 style="text-align:right; color:#e91e63;">總計：${order[3]}</h3>
            <p>廚房備餐中，請稍後至櫃台結帳</p>
            <a href="/" style="display:inline-block; padding:10px 30px; background:#007bff; color:white; text-decoration:none; border-radius:20px;">繼續點餐</a>
        </div>
    </body>
    </html>
    """

# --- 5. 廚房看板 ---
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
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
        <style>
            body { font-family: sans-serif; background: #222; color: white; margin: 0; padding: 10px; }
            .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
            .order-card { background: #333; border-left: 8px solid #ff9800; margin-bottom: 15px; padding: 15px; border-radius: 5px; }
            .completed { border-left: 8px solid #28a745; opacity: 0.5; }
            .btn-done { background: #28a745; color: white; border: none; padding: 10px; border-radius: 5px; float: right; cursor: pointer; }
            .order-items { font-size: 1.2em; line-height: 1.6; margin-top: 10px; }
            a { color: white; background: #444; padding: 5px 10px; text-decoration: none; border-radius: 5px; }
        </style>
    </head>
    <body>
        <div class="header">
            <h3>👨‍🍳 訂單看板</h3>
            <div>
                <button onclick="enableAudio()" style="background:#e91e63; border:none; color:white; padding:5px;">🔊</button>
                <a href="/kitchen/menu">菜單管理</a>
                <a href="/daily_report" target="_blank">結帳單</a>
            </div>
        </div>
        
        <div id="container">
    """
    
    for order in orders:
        status_class = "completed" if order[4] == 'Completed' else ""
        btn = f"<button class='btn-done' onclick=\"completeOrder({order[0]})\">完成</button>" if order[4] != 'Completed' else ""
        items_display = order[2].replace(" + ", "<br>")
        
        html += f"""
        <div class="order-card {status_class}">
            {btn}
            <div style="font-size:1.4em; color:#ff9800; font-weight:bold;">桌號：{order[1]} <small style="color:#aaa; font-size:0.5em;">{order[5].strftime('%H:%M')}</small></div>
            <div class="order-items">{items_display}</div>
        </div>
        """

    html += f"""
        </div>
        <audio id="notification-sound" src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" preload="auto"></audio>
        <script>
            let currentOrderCount = {len(orders)};
            function enableAudio() {{ document.getElementById('notification-sound').play(); alert("音效已開啟"); }}
            function completeOrder(id) {{ if(confirm('完成？')) fetch('/complete/'+id).then(()=>location.reload()); }}
            
            // 每 10 秒檢查一次
            setInterval(() => location.reload(), 10000);
            
            let savedCount = localStorage.getItem('orderCount');
            if (savedCount && parseInt(savedCount) < currentOrderCount) {{
                document.getElementById('notification-sound').play().catch(e=>console.log("Audio block"));
            }}
            localStorage.setItem('orderCount', currentOrderCount);
        </script>
    </body>
    </html>
    """
    return html

# --- 6. 菜單管理 (同前，不變) ---
@app.route('/kitchen/menu', methods=['GET', 'POST'])
def kitchen_menu():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST' and 'add_item' in request.form:
        name = request.form['name']
        price = request.form['price']
        category = request.form['category']
        image_url = request.form['image_url']
        custom_options = request.form['custom_options']
        cur.execute("INSERT INTO products (name, price, category, image_url, is_available, custom_options) VALUES (%s, %s, %s, %s, TRUE, %s)", 
                    (name, price, category, image_url, custom_options))
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
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>body{font-family:sans-serif; padding:10px; background:#f4f4f9;}</style>
    </head>
    <body>
        <a href="/kitchen">⬅️ 回廚房</a>
        <h2>🛠️ 菜單管理</h2>
        <div style="background:white; padding:15px; border-radius:8px;">
            <h3>➕ 新增</h3>
            <form method="POST">
                <input type="hidden" name="add_item" value="1">
                <input type="text" name="name" placeholder="名稱" required style="width:100%; margin:5px 0; padding:8px;">
                <input type="number" name="price" placeholder="價格" required style="width:100%; margin:5px 0; padding:8px;">
                <input type="text" name="category" placeholder="分類 (主食/飲料)" required style="width:100%; margin:5px 0; padding:8px;">
                <input type="text" name="image_url" placeholder="圖片網址" style="width:100%; margin:5px 0; padding:8px;">
                <input type="text" name="custom_options" placeholder="選項 (如: 微糖,半糖)" style="width:100%; margin:5px 0; padding:8px;">
                <button style="width:100%; background:#007bff; color:white; padding:10px; border:none; margin-top:5px;">新增</button>
            </form>
        </div>
        <hr>
    """
    for p in products:
        status = "🟢" if p[5] else "🔴"
        html += f"<div style='background:white; padding:10px; margin-bottom:5px; border-left:5px solid #007bff;'>{status} <b>{p[1]}</b> (${p[2]})<br><small>{p[6]}</small><br><a href='/menu/toggle/{p[0]}'>上架/完售</a> | <a href='/menu/delete/{p[0]}'>刪除</a></div>"
    return html + "</body></html>"

# --- 輔助路由 ---
@app.route('/menu/toggle/<int:pid>')
def menu_toggle(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE products SET is_available = NOT is_available WHERE id = %s", (pid,))
    conn.commit()
    return redirect(url_for('kitchen_menu'))

@app.route('/menu/delete/<int:pid>')
def menu_delete(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit()
    return redirect(url_for('kitchen_menu'))

@app.route('/complete/<int:order_id>')
def complete_order(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status = 'Completed' WHERE id = %s", (order_id,))
    conn.commit()
    return "OK"

@app.route('/daily_report')
def daily_report():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date")
    orders = cur.fetchall()
    total = sum(o[3] for o in orders)
    
    html = f"<h2>日結單 {date.today()}</h2><table style='width:100%'>"
    for o in orders:
        html += f"<tr><td>#{o[0]} 桌{o[1]}</td><td align='right'>${o[3]}</td></tr>"
    html += f"</table><h3 align='right'>總計: ${total}</h3><button onclick='window.print()'>列印</button>"
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
