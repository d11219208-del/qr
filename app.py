import os
import psycopg2
from flask import Flask, request, redirect, url_for
from datetime import datetime, date

app = Flask(__name__)

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 1. 資料庫初始化 (自動升級：加入客製化欄位) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 建立基礎表格
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

        # --- 自動升級舊資料庫 (確保欄位存在) ---
        # 1. 加入完售欄位
        try:
            cur.execute("ALTER TABLE products ADD COLUMN is_available BOOLEAN DEFAULT TRUE;")
        except psycopg2.errors.DuplicateColumn:
            conn.rollback()
        
        # 2. 加入客製化選項欄位 (New!)
        try:
            cur.execute("ALTER TABLE products ADD COLUMN custom_options TEXT;")
            conn.commit()
        except psycopg2.errors.DuplicateColumn:
            conn.rollback()

        # 如果是空的，插入預設資料
        cur.execute('SELECT count(*) FROM products;')
        if cur.fetchone()[0] == 0:
            default_menu = [
                ('招牌牛肉麵', 180, '主食', 'https://i.ibb.co/vz1k3j1/beef-noodle.jpg', True, '不要蔥,加辣,麵軟'),
                ('古早味排骨飯', 120, '主食', 'https://i.ibb.co/MCTvVqL/pork-rice.jpg', True, '半飯,多汁'),
                ('燙青菜', 40, '小菜', 'https://i.ibb.co/Xkz2zt3/vegetables.jpg', True, '不要蒜,醬油少'),
                ('珍珠奶茶', 60, '飲料', 'https://i.ibb.co/JtdjvX3/bubble-tea.jpg', True, '半糖,微糖,去冰,少冰')
            ]
            cur.executemany('INSERT INTO products (name, price, category, image_url, is_available, custom_options) VALUES (%s, %s, %s, %s, %s, %s)', default_menu)
            conn.commit()

        return "系統升級完成！已加入客製化選項功能。<br><a href='/'>前往首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 顧客端點餐首頁 (手機優化版) ---
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    table_from_url = request.args.get('table', '')

    if request.method == 'POST':
        table_number = request.form.get('table_number')
        # 獲取所有被勾選的商品 ID
        selected_item_ids = request.form.getlist('items')
        
        if not selected_item_ids:
            return "錯誤：未選擇餐點。<a href='/'>重試</a>"

        total_price = 0
        ordered_items_details = []
        
        for pid in selected_item_ids:
            cur.execute("SELECT name, price FROM products WHERE id = %s AND is_available = TRUE", (pid,))
            product = cur.fetchone()
            if product:
                # 取得該商品被勾選的客製化選項
                # HTML name 格式為: options_商品ID
                opts = request.form.getlist(f'options_{pid}')
                opts_str = f" ({', '.join(opts)})" if opts else ""
                
                ordered_items_details.append(f"{product[0]}{opts_str} (${product[1]})")
                total_price += product[1]
        
        if not ordered_items_details:
            return "錯誤：商品可能已完售。<a href='/'>重試</a>"

        items_str = " + ".join(ordered_items_details)

        cur.execute(
            "INSERT INTO orders (table_number, items, total_price) VALUES (%s, %s, %s) RETURNING id",
            (table_number, items_str, total_price)
        )
        new_order_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('order_success', order_id=new_order_id))

    try:
        cur.execute("SELECT * FROM products ORDER BY category, id")
        products = cur.fetchall()
    except:
        return "系統更新中，請先執行 <a href='/init_db'>/init_db</a>"
        
    cur.close()
    conn.close()

    table_input_html = f'<input type="text" name="table_number" value="{table_from_url}" readonly>' if table_from_url else '<input type="text" name="table_number" placeholder="請輸入桌號" required>'
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>線上點餐</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
        <style>
            body {{ font-family: 'PingFang TC', 'Microsoft JhengHei', sans-serif; background: #f0f2f5; margin: 0; padding-bottom: 80px; }}
            .header {{ background: white; padding: 15px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 100; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 10px; }}
            
            /* 菜單卡片優化 */
            .menu-card {{ background: white; margin-bottom: 15px; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
            .menu-main {{ display: flex; padding: 12px; align-items: center; }}
            .menu-img {{ width: 90px; height: 90px; object-fit: cover; border-radius: 8px; flex-shrink: 0; background: #eee; }}
            .menu-info {{ flex-grow: 1; padding-left: 15px; }}
            .menu-name {{ font-size: 1.1em; font-weight: bold; color: #333; }}
            .menu-price {{ color: #e91e63; font-weight: bold; font-size: 1.1em; margin-top: 5px; }}
            
            /* 勾選框優化 (大按鈕) */
            input[type="checkbox"].main-check {{ width: 25px; height: 25px; margin-left: 10px; accent-color: #28a745; }}
            
            /* 客製化選項區域 */
            .options-area {{ background: #fcfcfc; padding: 10px 15px; border-top: 1px solid #f0f0f0; display: none; }}
            .option-tag {{ display: inline-block; margin: 5px 10px 5px 0; font-size: 0.9em; color: #555; }}
            .option-tag input {{ margin-right: 5px; transform: scale(1.2); }}

            /* 顯示/隱藏選項的邏輯 */
            .menu-card:has(.main-check:checked) .options-area {{ display: block; }}

            /* 完售樣式 */
            .sold-out {{ opacity: 0.6; filter: grayscale(1); pointer-events: none; }}
            .badge {{ background: #999; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; }}
            
            /* 分類標題 */
            .cat-title {{ font-size: 1.2em; font-weight: bold; color: #555; margin: 20px 5px 10px; border-left: 5px solid #ff9800; padding-left: 10px; }}
            
            /* 底部懸浮按鈕 */
            .footer-bar {{ position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 15px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); text-align: center; box-sizing: border-box; }}
            .submit-btn {{ width: 100%; max-width: 580px; padding: 15px; background: #28a745; color: white; border: none; font-size: 1.2em; font-weight: bold; border-radius: 50px; cursor: pointer; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2 style="margin:0;">🍴 歡迎點餐</h2>
        </div>
        <div class="container">
            <form method="POST">
                <div style="background:white; padding:15px; border-radius:10px; margin-bottom:20px;">
                    <label style="font-weight:bold; color:#555;">您的位置：</label>
                    <div style="margin-top:5px;">{table_input_html}</div>
                </div>
    """
    
    current_category = ""
    for p in products:
        # p: id, name, price, category, image_url, is_available, custom_options
        p_id, p_name, p_price, p_cat, p_img, p_avail, p_opts = p[0], p[1], p[2], p[3], p[4], p[5], p[6]
        
        # 處理圖片與預設值
        p_img = p_img if p_img else "https://via.placeholder.com/150"
        p_avail = True if p_avail is None else p_avail # 容錯
        
        if p_cat != current_category:
            html += f"<div class='cat-title'>{p_cat}</div>"
            current_category = p_cat
            
        sold_out_class = "" if p_avail else "sold-out"
        sold_out_badge = "" if p_avail else "<span class='badge'>已售完</span>"
        disabled_attr = "" if p_avail else "disabled"
        
        # 解析客製化選項 (例如 "少油,不要蔥") -> 轉成 Checkbox HTML
        options_html = ""
        if p_opts and p_avail:
            opt_list = p_opts.split(',')
            options_html = "<div class='options-area'><div style='font-size:0.9em; margin-bottom:5px; color:#888;'>客製化 (選填)：</div>"
            for opt in opt_list:
                opt = opt.strip()
                if opt:
                    # 注意 name 是 options_{pid}，這樣後端才知道是哪個商品的選項
                    options_html += f"""
                    <label class="option-tag">
                        <input type="checkbox" name="options_{p_id}" value="{opt}"> {opt}
                    </label>
                    """
            options_html += "</div>"

        html += f"""
        <div class="menu-card {sold_out_class}">
            <label style="display:block; cursor:pointer;">
                <div class="menu-main">
                    <img src="{p_img}" class="menu-img">
                    <div class="menu-info">
                        <div class="menu-name">{p_name} {sold_out_badge}</div>
                        <div class="menu-price">${p_price}</div>
                    </div>
                    <input type="checkbox" name="items" value="{p_id}" class="main-check" {disabled_attr}>
                </div>
            </label>
            {options_html}
        </div>
        """

    html += """
                <div style="height: 60px;"></div> <div class="footer-bar">
                    <button type="submit" class="submit-btn" onclick="return confirm('確定送出訂單？')">送出訂單 ($)</button>
                </div>
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

    if not order: return "查無訂單"
    items_list = order[2].replace(" + ", "<br><span style='font-size:1.2em'>🔹</span> ")

    return f"""
    <!DOCTYPE html>
    <html>
    <head> <meta name="viewport" content="width=device-width, initial-scale=1"> </head>
    <body style="font-family: sans-serif; text-align: center; padding: 40px 20px; background: #f4f4f9;">
        <div style="background: white; padding: 30px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); max-width: 400px; margin: 0 auto;">
            <div style="font-size:60px; color:#28a745; margin-bottom:10px;">✅</div>
            <h2 style="color:#333; margin:0;">下單成功</h2>
            <h3 style="color:#ff9800; margin-top:5px;">桌號：{order[1]}</h3>
            <div style="text-align:left; background:#f8f9fa; padding:20px; margin:20px 0; border-radius:10px; line-height:1.6;">
                <span style="font-size:1.2em">🔹</span> {items_list}
                <hr style="border-top:1px dashed #ccc; margin:15px 0;">
                <div style="text-align:right; font-weight:bold; font-size:1.4em; color:#e91e63;">總計：${order[3]}</div>
            </div>
            <p style="color:#666;">廚房已收到您的訂單<br>請稍後至櫃台結帳</p>
            <a href="/" style="display:inline-block; margin-top:20px; text-decoration:none; background:#007bff; color:white; padding:10px 30px; border-radius:50px;">回到首頁</a>
        </div>
    </body>
    </html>
    """

# --- 4. 廚房看板 ---
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
            body { font-family: sans-serif; background: #222; color: white; padding: 10px; margin:0; }
            .order-card { background: #333; border-left: 8px solid #ff9800; margin-bottom: 15px; padding: 15px; border-radius: 8px; }
            .completed { border-left: 8px solid #28a745; opacity: 0.5; }
            .btn-done { background: #28a745; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; float: right; font-size:1em; }
            .nav-bar { display: flex; justify-content: space-between; padding: 10px; background: #111; margin: -10px -10px 15px -10px; align-items: center; }
            a { color: white; text-decoration: none; margin-left: 15px; background: #555; padding: 5px 10px; border-radius: 5px; font-size:0.9em; }
        </style>
    </head>
    <body>
        <div class="nav-bar">
            <h3 style="margin:0;">👨‍🍳 訂單看板</h3>
            <div>
                <button onclick="enableAudio()" style="background:#e91e63; border:none; color:white; padding:5px 10px; border-radius:5px;">🔊</button>
                <a href="/kitchen/menu">菜單管理</a>
                <a href="/daily_report" target="_blank">結帳單</a>
            </div>
        </div>
        
        <div id="order-container">
    """
    
    for order in orders:
        status_class = "completed" if order[4] == 'Completed' else ""
        btn = f"<button class='btn-done' onclick=\"completeOrder({order[0]})\">完成</button>" if order[4] != 'Completed' else ""
        # 顯示訂單內容 (如果有客製化，內容會比較長，這裡簡單處理)
        items_display = order[2].replace(" + ", "<br>")
        
        html += f"""
        <div class="order-card {status_class}">
            {btn}
            <div style="font-size:1.4em; color:#ff9800; font-weight:bold;">桌號：{order[1]} <span style="font-size:0.6em; color:#aaa; font-weight:normal;">({order[5].strftime('%H:%M')})</span></div>
            <div style="font-size:1.1em; margin-top:10px; line-height:1.5;">{items_display}</div>
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

# --- 5. 菜單管理後台 (新增客製化欄位) ---
@app.route('/kitchen/menu', methods=['GET', 'POST'])
def kitchen_menu():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST' and 'add_item' in request.form:
        name = request.form['name']
        price = request.form['price']
        category = request.form['category']
        image_url = request.form['image_url']
        # 獲取客製化選項字串
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
        <title>菜單管理</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: sans-serif; padding: 20px; background: #f4f4f9; }
            .form-box { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            input, select { padding: 10px; margin: 5px 0; width: 100%; box-sizing: border-box; border:1px solid #ddd; border-radius:4px; }
            .btn { display:inline-block; padding:8px 12px; color:white; border-radius:4px; text-decoration:none; margin-right:5px; font-size:0.9em; }
            .item-row { background:white; padding:10px; margin-bottom:10px; border-radius:5px; border-left:5px solid #007bff; display:flex; align-items:center; justify-content:space-between; }
        </style>
    </head>
    <body>
        <a href="/kitchen" style="text-decoration:none; font-size:1.2em;">⬅️ 回廚房看板</a>
        <h2>🛠️ 菜單管理</h2>

        <div class="form-box">
            <h3>➕ 新增菜色</h3>
            <form method="POST">
                <input type="hidden" name="add_item" value="1">
                <label>名稱：</label><input type="text" name="name" required>
                <label>價格：</label><input type="number" name="price" required>
                <label>分類：</label><input type="text" name="category" placeholder="主食 / 小菜 / 飲料" required>
                <label>圖片連結：</label><input type="text" name="image_url" placeholder="https://...">
                <label style="color:#e91e63; font-weight:bold;">客製化選項 (用逗號隔開)：</label>
                <input type="text" name="custom_options" placeholder="例如：不要蔥,加辣,去冰 (留空則無選項)">
                <button type="submit" style="background:#007bff; color:white; border:none; padding:12px; width:100%; margin-top:10px; border-radius:5px; font-size:1.1em;">新增</button>
            </form>
        </div>
        <hr>
        <h3>📋 現有菜單</h3>
    """
    
    for p in products:
        # p: id, name, price, category, image_url, is_avail, custom_options
        status = "🟢上架" if p[5] else "🔴完售"
        opts_display = f"<br><small style='color:#e91e63'>選項: {p[6]}</small>" if p[6] else ""
        
        html += f"""
        <div class="item-row">
            <div>
                <b>{p[1]}</b> (${p[2]}) - {p[3]}
                {opts_display}
                <br><small>{status}</small>
            </div>
            <div style="min-width:120px; text-align:right;">
                <a href="/menu/toggle/{p[0]}" class="btn" style="background:#6c757d;">上架/完售</a>
                <a href="/menu/edit/{p[0]}" class="btn" style="background:#ff9800;">編輯</a>
                <a href="/menu/delete/{p[0]}" class="btn" style="background:#dc3545;" onclick="return confirm('刪除？')">X</a>
            </div>
        </div>
        """

    html += "</body></html>"
    return html

# --- 6. 編輯功能 (含客製化) ---
@app.route('/menu/edit/<int:pid>', methods=['GET', 'POST'])
def menu_edit(pid):
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        name = request.form['name']
        price = request.form['price']
        category = request.form['category']
        image_url = request.form['image_url']
        custom_options = request.form['custom_options']
        
        cur.execute("UPDATE products SET name=%s, price=%s, category=%s, image_url=%s, custom_options=%s WHERE id=%s",
                    (name, price, category, image_url, custom_options, pid))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('kitchen_menu'))

    cur.execute("SELECT * FROM products WHERE id = %s", (pid,))
    p = cur.fetchone()
    cur.close()
    conn.close()
    
    # p[6] 是 custom_options
    opts_val = p[6] if p[6] else ""

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="font-family: sans-serif; padding: 20px; max-width: 600px; margin: 0 auto;">
        <h2>✏️ 編輯菜色</h2>
        <form method="POST">
            <p>名稱：<input type="text" name="name" value="{p[1]}" required style="width:100%; padding:10px;"></p>
            <p>價格：<input type="number" name="price" value="{p[2]}" required style="width:100%; padding:10px;"></p>
            <p>分類：<input type="text" name="category" value="{p[3]}" required style="width:100%; padding:10px;"></p>
            <p>圖片：<input type="text" name="image_url" value="{p[4]}" style="width:100%; padding:10px;"></p>
            <p style="color:#e91e63; font-weight:bold;">客製化選項 (逗號隔開)：</p>
            <input type="text" name="custom_options" value="{opts_val}" style="width:100%; padding:10px;">
            <br><br>
            <button type="submit" style="background:#28a745; color:white; border:none; padding:12px 30px; border-radius:5px; font-size:1.1em;">儲存</button>
            <a href="/kitchen/menu" style="margin-left:20px;">取消</a>
        </form>
    </body>
    </html>
    """

# --- 其他輔助 API 保持不變 ---
@app.route('/menu/toggle/<int:pid>')
def menu_toggle(pid):
    conn = get_db_connection()
    cur = conn.cursor()
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
            <tr><th style="border-bottom:1px dashed #000; text-align:left;">單號/內容</th><th style="border-bottom:1px dashed #000; text-align:right;">金額</th></tr>
    """
    for order in orders:
        items_clean = order[2].replace("<br>", " ").replace("🔹", "")
        html += f"<tr><td style='padding:5px 0;'>#{order[0]} 桌:{order[1]}<br><small>{items_clean}</small></td><td style='text-align:right; vertical-align:top;'>${order[3]}</td></tr>"
    html += f"</table><h3 style='text-align:right;'>總計：${total_revenue}</h3></body></html>"
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
