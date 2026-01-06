import os
import psycopg2
import json
import re
from flask import Flask, request, redirect, url_for
from datetime import datetime, date

app = Flask(__name__)

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 1. 資料庫初始化 (新增排序欄位) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 建立表格結構
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price INTEGER NOT NULL,
                category VARCHAR(50),
                image_url TEXT,
                is_available BOOLEAN DEFAULT TRUE,
                custom_options TEXT,
                sort_order INTEGER DEFAULT 100
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
        conn.commit()

        # 嘗試新增 sort_order 欄位 (針對舊資料庫升級)
        try:
            cur.execute("ALTER TABLE products ADD COLUMN sort_order INTEGER DEFAULT 100;")
            conn.commit()
        except:
            conn.rollback()

        # 預設菜單 (如果完全沒資料才加)
        cur.execute('SELECT count(*) FROM products;')
        if cur.fetchone()[0] == 0:
            default_menu = [
                ('招牌牛肉麵', 180, '主食', 'https://i.ibb.co/vz1k3j1/beef-noodle.jpg', True, '不要蔥,加麵:+20,加湯', 1),
                ('古早味排骨飯', 120, '主食', 'https://i.ibb.co/MCTvVqL/pork-rice.jpg', True, '半飯,加滷蛋:+15', 2),
                ('燙青菜', 40, '小菜', 'https://i.ibb.co/Xkz2zt3/vegetables.jpg', True, '不要蒜,清燙', 3),
                ('珍珠奶茶', 60, '飲料', 'https://i.ibb.co/JtdjvX3/bubble-tea.jpg', True, '半糖,微糖,加椰果:+10,加珍珠:+10', 4)
            ]
            cur.executemany('INSERT INTO products (name, price, category, image_url, is_available, custom_options, sort_order) VALUES (%s, %s, %s, %s, %s, %s, %s)', default_menu)
            conn.commit()

        return "資料庫更新完成！(已加入排序與加價功能)<br><a href='/'>前往首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 顧客端首頁 ---
@app.route('/', methods=['GET', 'POST'])
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    table_from_url = request.args.get('table', '')

    if request.method == 'POST':
        table_number = request.form.get('table_number')
        cart_json = request.form.get('cart_data')
        
        if not cart_json or cart_json == '[]':
            return "錯誤：購物車是空的 <a href='/'>返回</a>"

        try:
            cart_items = json.loads(cart_json)
        except:
            return "資料格式錯誤"
        
        total_price = 0
        items_display_list = []

        for item in cart_items:
            # item 結構: {name, base_price, unit_price, qty, options:[]}
            p_name = item['name']
            p_unit_price = int(item['unit_price']) # 這是包含加價後的單價
            p_qty = int(item['qty'])
            p_opts = item.get('options', [])
            
            opts_str = f"({','.join(p_opts)})" if p_opts else ""
            display_str = f"{p_name} {opts_str} x{p_qty}"
            
            items_display_list.append(display_str)
            total_price += (p_unit_price * p_qty)

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

    # 依照 sort_order 排序 (ASC: 小的在前)
    try:
        cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
        products = cur.fetchall()
    except:
        return "請先執行 <a href='/init_db'>/init_db</a>"
    
    cur.close()
    conn.close()
    
    products_list = []
    for p in products:
        # p[7] 是 sort_order
        products_list.append({
            'id': p[0], 'name': p[1], 'price': p[2], 'category': p[3],
            'image_url': p[4] if p[4] else "https://via.placeholder.com/150",
            'is_available': p[5],
            'custom_options': p[6].split(',') if p[6] else []
        })

    return render_frontend(table_from_url, products_list)

def render_frontend(table_number, products_data):
    products_json = json.dumps(products_data)
    table_input = f'<input type="text" id="table_number" name="table_number" value="{table_number}" readonly>' if table_number else '<input type="text" id="table_number" name="table_number" placeholder="請輸入桌號" required>'

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>線上點餐</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
        <style>
            body {{ font-family: 'Microsoft JhengHei', sans-serif; margin: 0; padding-bottom: 90px; background: #f4f7f6; }}
            .header {{ background: white; padding: 15px; text-align: center; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .container {{ padding: 10px; max-width: 600px; margin: 0 auto; }}
            .menu-item {{ background: white; border-radius: 12px; padding: 10px; display: flex; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
            .menu-img {{ width: 90px; height: 90px; border-radius: 8px; object-fit: cover; background: #eee; flex-shrink: 0; }}
            .menu-info {{ flex-grow: 1; padding-left: 15px; display: flex; flex-direction: column; justify-content: space-between; }}
            .menu-name {{ font-weight: bold; font-size: 1.1em; }}
            .menu-price {{ color: #e91e63; font-weight: bold; }}
            .add-btn {{ background: #28a745; color: white; border: none; padding: 8px 15px; border-radius: 20px; font-weight: bold; cursor: pointer; align-self: flex-end; }}
            .sold-out {{ background: #ccc; cursor: not-allowed; }}
            
            /* Modal */
            .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: none; z-index: 999; justify-content: center; align-items: flex-end; }}
            .modal-content {{ background: white; width: 100%; max-width: 600px; border-radius: 20px 20px 0 0; padding: 20px; box-sizing: border-box; animation: slideUp 0.3s; max-height: 80vh; overflow-y: auto; }}
            @keyframes slideUp {{ from {{ transform: translateY(100%); }} to {{ transform: translateY(0); }} }}

            /* 購物車列表 */
            .cart-item-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding: 10px 0; }}
            .del-btn {{ color: white; background: #dc3545; border: none; padding: 5px 10px; border-radius: 5px; cursor: pointer; }}
            
            .option-tag {{ display: inline-block; border: 1px solid #ddd; padding: 8px 15px; border-radius: 20px; margin: 5px 5px 5px 0; color: #555; cursor: pointer; }}
            .option-tag.selected {{ background: #e3f2fd; border-color: #2196f3; color: #2196f3; font-weight: bold; }}
            .option-price {{ font-size: 0.8em; color: #e91e63; }}

            .cart-bar {{ position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 15px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; box-sizing: border-box; z-index: 500; }}
            .cart-info-box {{ cursor: pointer; flex-grow: 1; }}
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
        <div class="container" id="menu-container"></div>
        
        <form method="POST" id="order-form">
            <input type="hidden" name="cart_data" id="cart_data_input">
            <div class="cart-bar" id="cart-bar" style="display:none;">
                <div class="cart-info-box" onclick="openCartModal()">
                    <span style="font-size:0.9em; color:#666;">▲ 查看明細</span><br>
                    <span id="total-qty" style="background:#e91e63; color:white; padding:2px 8px; border-radius:10px; font-size:0.8em;">0</span> 
                    <b>合計: $<span id="total-price">0</span></b>
                </div>
                <button type="button" onclick="submitOrder()" style="background:#28a745; color:white; border:none; padding:12px 30px; border-radius:50px; font-size:1.1em; font-weight:bold;">去結帳</button>
            </div>
        </form>

        <div class="modal-overlay" id="option-modal">
            <div class="modal-content">
                <h3 id="modal-title"></h3>
                <div style="color:#e91e63; font-weight:bold; margin-bottom:10px;">
                    $<span id="modal-display-price">0</span> 
                    <small style="color:#888; font-weight:normal;" id="modal-base-price-info"></small>
                </div>
                <div id="modal-options-area"></div>
                <div class="qty-control">
                    <div class="qty-btn" onclick="changeQty(-1)">-</div>
                    <span class="qty-val" id="modal-qty">1</span>
                    <div class="qty-btn" onclick="changeQty(1)">+</div>
                </div>
                <button style="width:100%; background:#28a745; color:white; padding:15px; border:none; border-radius:10px; margin-top:20px; font-size:1.1em;" onclick="addToCartConfirm()">加入購物車</button>
                <button style="width:100%; background:white; color:#666; padding:10px; border:none; margin-top:5px;" onclick="closeOptionModal()">取消</button>
            </div>
        </div>

        <div class="modal-overlay" id="cart-modal">
            <div class="modal-content">
                <h3>🛒 購物車明細</h3>
                <div id="cart-list-area" style="margin-bottom:20px;"></div>
                <button style="width:100%; background:#6c757d; color:white; padding:15px; border:none; border-radius:10px;" onclick="closeCartModal()">關閉</button>
            </div>
        </div>

        <script>
            const products = {products_json};
            let cart = [];
            let currentItem = null;
            let currentQty = 1;
            let currentOptions = []; // 存字串 array
            let currentAddPrice = 0; // 當前選項加總金額
            
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
                let btnHtml = p.is_available ? `<button class="add-btn" onclick="openOptionModal(${{p.id}})">加入</button>` : `<button class="add-btn sold-out" disabled>已售完</button>`;
                el.innerHTML = `<img src="${{p.image_url}}" class="menu-img"><div class="menu-info"><div><div class="menu-name">${{p.name}}</div><div class="menu-price">$${{p.price}}</div></div>${{btnHtml}}</div>`;
                container.appendChild(el);
            }});

            // 解析選項與價格 (格式: "加麵:+20" 或 "去冰")
            function parseOption(optStr) {{
                if(optStr.includes(':+')) {{
                    const parts = optStr.split(':+');
                    return {{ name: parts[0], price: parseInt(parts[1]) || 0, full: optStr }};
                }}
                return {{ name: optStr, price: 0, full: optStr }};
            }}

            function openOptionModal(id) {{
                currentItem = products.find(p => p.id === id);
                currentQty = 1;
                currentOptions = [];
                currentAddPrice = 0;
                
                document.getElementById('modal-title').innerText = currentItem.name;
                document.getElementById('modal-base-price-info').innerText = '(單價 $' + currentItem.price + ')';
                updateModalTotal();

                const optArea = document.getElementById('modal-options-area');
                optArea.innerHTML = '';
                
                if (currentItem.custom_options && currentItem.custom_options.length > 0) {{
                    optArea.innerHTML = '<p style="font-size:0.9em; color:#888;">客製化選項：</p>';
                    currentItem.custom_options.forEach(opt => {{
                        opt = opt.trim();
                        if(!opt) return;
                        const parsed = parseOption(opt);
                        const tag = document.createElement('div');
                        tag.className = 'option-tag';
                        
                        // 顯示文字
                        let displayHTML = parsed.name;
                        if(parsed.price > 0) displayHTML += ` <span class="option-price">(+$${{parsed.price}})</span>`;
                        tag.innerHTML = displayHTML;
                        
                        tag.onclick = function() {{
                            // 切換選取
                            if(currentOptions.includes(opt)) {{
                                currentOptions = currentOptions.filter(o => o !== opt);
                                currentAddPrice -= parsed.price;
                                tag.classList.remove('selected');
                            }} else {{
                                currentOptions.push(opt);
                                currentAddPrice += parsed.price;
                                tag.classList.add('selected');
                            }}
                            updateModalTotal();
                        }};
                        optArea.appendChild(tag);
                    }});
                }}
                document.getElementById('option-modal').style.display = 'flex';
            }}

            function updateModalTotal() {{
                const unitPrice = currentItem.price + currentAddPrice;
                const total = unitPrice * currentQty;
                document.getElementById('modal-display-price').innerText = total;
                document.getElementById('modal-qty').innerText = currentQty;
            }}

            function closeOptionModal() {{ document.getElementById('option-modal').style.display = 'none'; }}
            function changeQty(n) {{ 
                if(currentQty + n >= 1) {{ 
                    currentQty += n; 
                    updateModalTotal();
                }} 
            }}
            
            function addToCartConfirm() {{
                const finalUnitPrice = currentItem.price + currentAddPrice;
                cart.push({{ 
                    id: currentItem.id, 
                    name: currentItem.name, 
                    base_price: currentItem.price,
                    unit_price: finalUnitPrice, // 包含加價
                    qty: currentQty, 
                    options: [...currentOptions] 
                }});
                closeOptionModal();
                updateCartBar();
            }}

            function updateCartBar() {{
                const bar = document.getElementById('cart-bar');
                if(cart.length > 0) {{
                    bar.style.display = 'flex';
                    const totalP = cart.reduce((acc, item) => acc + (item.unit_price * item.qty), 0);
                    const totalQ = cart.reduce((acc, item) => acc + item.qty, 0);
                    document.getElementById('total-price').innerText = totalP;
                    document.getElementById('total-qty').innerText = totalQ;
                }} else {{ bar.style.display = 'none'; }}
            }}

            function openCartModal() {{
                const listArea = document.getElementById('cart-list-area');
                listArea.innerHTML = '';
                if(cart.length === 0) {{ listArea.innerHTML = '<p>購物車是空的</p>'; }}
                
                cart.forEach((item, index) => {{
                    const row = document.createElement('div');
                    row.className = 'cart-item-row';
                    
                    // 美化選項顯示 (移除 :+20 這種後端格式，只顯示 UI 友善的)
                    let displayOpts = [];
                    item.options.forEach(opt => {{
                        let parsed = parseOption(opt);
                        displayOpts.push(parsed.name + (parsed.price>0 ? `(+$${{parsed.price}})` : ''));
                    }});

                    const optsHtml = displayOpts.length ? `<br><small style='color:#888'>${{displayOpts.join(', ')}}</small>` : '';
                    
                    row.innerHTML = `
                        <div>
                            <b>${{item.name}}</b> x${{item.qty}}
                            ${{optsHtml}}
                            <div style='color:#e91e63'>$${{item.unit_price * item.qty}}</div>
                        </div>
                        <button class="del-btn" onclick="removeFromCart(${{index}})">🗑️</button>
                    `;
                    listArea.appendChild(row);
                }});
                document.getElementById('cart-modal').style.display = 'flex';
            }}
            
            function removeFromCart(index) {{
                if(confirm('確定刪除此項目？')) {{
                    cart.splice(index, 1);
                    updateCartBar();
                    if(cart.length === 0) closeCartModal();
                    else openCartModal();
                }}
            }}
            
            function closeCartModal() {{ document.getElementById('cart-modal').style.display = 'none'; }}

            function submitOrder() {{
                const tableVal = document.getElementById('table_number').value;
                if(!tableVal) {{ alert('請輸入桌號'); return; }}
                if(cart.length === 0) return;
                
                const totalP = cart.reduce((acc, item) => acc + (item.unit_price * item.qty), 0);
                if(!confirm(`確定送出訂單？\\n總金額: $${{totalP}}`)) return;
                
                document.getElementById('cart_data_input').value = JSON.stringify(cart);
                document.getElementById('order-form').submit();
            }}
        </script>
    </body>
    </html>
    """

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
            <p>廚房備餐中</p>
            <a href="/" style="display:inline-block; padding:10px 30px; background:#007bff; color:white; text-decoration:none; border-radius:20px;">繼續點餐</a>
        </div>
    </body>
    </html>
    """

# --- 4. 廚房端 ---
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
    html += """</div><script>
        function completeOrder(id) { if(confirm('完成？')) fetch('/complete/'+id).then(()=>location.reload()); }
        setInterval(() => location.reload(), 10000);
    </script></body></html>"""
    return html

# --- 5. 菜單管理 (新增排序輸入框) ---
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
        sort_order = request.form.get('sort_order', 100)
        
        cur.execute("INSERT INTO products (name, price, category, image_url, is_available, custom_options, sort_order) VALUES (%s, %s, %s, %s, TRUE, %s, %s)", 
                    (name, price, category, image_url, custom_options, sort_order))
        conn.commit()
        return redirect(url_for('kitchen_menu'))
    
    # 依照排序顯示
    cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
    products = cur.fetchall()
    cur.close()
    conn.close()
    
    html = """
    <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:sans-serif; padding:10px; background:#f4f4f9;} .btn{padding:5px 10px; text-decoration:none; color:white; border-radius:4px; font-size:0.9em; margin-left:5px; display:inline-block;}</style></head><body>
        <a href="/kitchen">⬅️ 回廚房</a><h2>🛠️ 菜單管理</h2>
        <div style="background:white; padding:15px; border-radius:8px;">
            <h3>➕ 新增商品</h3>
            <form method="POST">
                <input type="hidden" name="add_item" value="1">
                <input type="text" name="name" placeholder="名稱" required style="width:100%; margin:5px 0; padding:8px;">
                <input type="number" name="price" placeholder="價格" required style="width:100%; margin:5px 0; padding:8px;">
                <input type="text" name="category" placeholder="分類 (主食/飲料)" required style="width:100%; margin:5px 0; padding:8px;">
                <input type="text" name="image_url" placeholder="圖片網址" style="width:100%; margin:5px 0; padding:8px;">
                <input type="text" name="custom_options" placeholder="選項 (例: 加麵:+20,微糖)" style="width:100%; margin:5px 0; padding:8px;">
                <input type="number" name="sort_order" placeholder="排序權重 (越小越前面)" value="100" style="width:100%; margin:5px 0; padding:8px;">
                <button style="width:100%; background:#007bff; color:white; padding:10px; border:none; margin-top:5px;">新增</button>
            </form>
            <p style="font-size:0.8em; color:gray;">💡 小提示：選項若要加錢，請用 :+數字，例如 <b>加麵:+20</b></p>
        </div>
        <hr>
    """
    for p in products:
        status = "🟢" if p[5] else "🔴"
        # p[7] 是 sort_order
        html += f"""
        <div style='background:white; padding:10px; margin-bottom:5px; border-left:5px solid #007bff;'>
            <div style="float:right; color:#888; font-size:0.8em;">排序: {p[7]}</div>
            {status} <b>{p[1]}</b> (${p[2]})<br><small>{p[6]}</small><br>
            <div style="margin-top:5px;">
                <a href='/menu/toggle/{p[0]}' class='btn' style='background:#6c757d'>上架/完售</a>
                <a href='/menu/edit/{p[0]}' class='btn' style='background:#ffc107; color:black;'>編輯</a>
                <a href='/menu/delete/{p[0]}' class='btn' style='background:#dc3545' onclick="return confirm('確定刪除？')">刪除</a>
            </div>
        </div>"""
    return html + "</body></html>"

# --- 6. 編輯菜單頁面 (修復 Bad Request 與新增排序) ---
@app.route('/menu/edit/<int:pid>', methods=['GET', 'POST'])
def menu_edit(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        name = request.form['name']
        price = request.form['price'] # 這裡之前有 HTML 錯誤，現在已修正
        category = request.form['category']
        image_url = request.form['image_url']
        custom_options = request.form['custom_options']
        sort_order = request.form['sort_order']
        
        cur.execute("""
            UPDATE products 
            SET name=%s, price=%s, category=%s, image_url=%s, custom_options=%s, sort_order=%s
            WHERE id=%s
        """, (name, price, category, image_url, custom_options, sort_order, pid))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('kitchen_menu'))

    cur.execute("SELECT * FROM products WHERE id = %s", (pid,))
    product = cur.fetchone()
    cur.close()
    conn.close()
    
    if not product: return "查無此商品"
    
    p_opts = product[6] if product[6] else ""
    # product[7] 是 sort_order
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="font-family: sans-serif; padding: 20px; max-width: 600px; margin: 0 auto; background:#f4f4f9;">
        <h2>✏️ 編輯商品</h2>
        <form method="POST" style="background:white; padding:20px; border-radius:10px;">
            <p>名稱：<input type="text" name="name" value="{product[1]}" required style="width:100%; padding:8px;"></p>
            <p>價格：<input type="number" name="price" value="{product[2]}" required style="width:100%; padding:8px;"></p>
            <p>分類：<input type="text" name="category" value="{product[3]}" required style="width:100%; padding:8px;"></p>
            <p>圖片：<input type="text" name="image_url" value="{product[4]}" style="width:100%; padding:8px;"></p>
            <p>選項 (加錢請用 :+20)：<input type="text" name="custom_options" value="{p_opts}" style="width:100%; padding:8px;"></p>
            <p>排序 (越小越前面)：<input type="number" name="sort_order" value="{product[7]}" style="width:100%; padding:8px;"></p>
            <br>
            <button type="submit" style="background:#28a745; color:white; border:none; padding:10px 30px; border-radius:5px;">儲存修改</button>
            <a href="/kitchen/menu" style="margin-left:20px;">取消</a>
        </form>
    </body>
    </html>
    """

# --- 輔助功能 ---
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
    for o in orders: html += f"<tr><td>#{o[0]} 桌{o[1]}</td><td align='right'>${o[3]}</td></tr>"
    html += f"</table><h3 align='right'>總計: ${total}</h3><button onclick='window.print()'>列印</button>"
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
