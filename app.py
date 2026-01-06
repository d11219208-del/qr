import os
import psycopg2
import json
import time
import threading
import urllib.request
from flask import Flask, request, redirect, url_for
from datetime import datetime, date

app = Flask(__name__)

# --- 防止休眠設定 ---
# 請將這裡換成您 Render 的網址 (例如 https://my-app.onrender.com)
# 如果不改，它會嘗試 ping 自己內部，效果可能不如 ping 外部網址好
SITE_URL = "http://127.0.0.1:10000" 

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 防呆翻譯載入 ---
def load_translations():
    fallback = {
        "zh": {
            "title": "線上點餐", "welcome": "歡迎點餐", "table_placeholder": "請輸入桌號", 
            "table_label": "桌號", "add": "加入", "sold_out": "已售完", "cart_detail": "查看明細", 
            "total": "合計", "checkout": "去結帳", "cart_title": "購物車明細", "empty_cart": "購物車是空的", 
            "close": "關閉", "confirm_delete": "確定刪除此項目？", "confirm_order": "確定送出訂單？", 
            "modal_unit_price": "單價", "modal_add_cart": "加入購物車", "modal_cancel": "取消", 
            "custom_options": "客製化選項", "order_success": "下單成功！", "kitchen_prep": "廚房備餐中", 
            "continue_order": "繼續點餐", "category_main": "主食", "category_side": "小菜", "category_drink": "飲料"
        },
        "en": {
            "title": "Online Ordering", "welcome": "Welcome", "table_placeholder": "Enter Table No.",
            "table_label": "Table", "add": "Add", "sold_out": "Sold Out", "cart_detail": "View Cart",
            "total": "Total", "checkout": "Checkout", "cart_title": "Cart Details", "empty_cart": "Cart is empty",
            "close": "Close", "confirm_delete": "Remove item?", "confirm_order": "Submit Order?",
            "modal_unit_price": "Price", "modal_add_cart": "Add to Cart", "modal_cancel": "Cancel",
            "custom_options": "Options", "order_success": "Order Placed!", "kitchen_prep": "Preparing...",
            "continue_order": "Order More", "category_main": "Main Dish", "category_side": "Side Dish", "category_drink": "Drinks"
        },
        "jp": {
            "title": "オンライン注文", "welcome": "いらっしゃいませ", "table_placeholder": "卓番を入力",
            "table_label": "卓番", "add": "追加", "sold_out": "完売", "cart_detail": "カートを見る",
            "total": "合計", "checkout": "会計する", "cart_title": "カート詳細", "empty_cart": "カートは空です",
            "close": "閉じる", "confirm_delete": "削除しますか？", "confirm_order": "注文を確定しますか？",
            "modal_unit_price": "単価", "modal_add_cart": "カートに入れる", "modal_cancel": "キャンセル",
            "custom_options": "オプション", "order_success": "注文完了！", "kitchen_prep": "調理中...",
            "continue_order": "続けて注文", "category_main": "メイン", "category_side": "サイド", "category_drink": "ドリンク"
        }
    }
    try:
        base_path = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_path, 'translations.json')
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 讀取翻譯檔失敗，使用內建備份。原因: {e}")
        return fallback

# --- 1. 資料庫初始化 ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                lang VARCHAR(10) DEFAULT 'zh'
            );
        ''')
        conn.commit()

        alter_commands = [
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_en VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_jp VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_en TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_jp TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 100;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';"
        ]
        
        for cmd in alter_commands:
            try:
                cur.execute(cmd)
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"Schema update skipped: {e}")

        return "資料庫初始化/升級完成！<br><a href='/'>前往首頁</a>"
    except Exception as e:
        return f"初始化失敗：{e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 語言選擇頁 ---
@app.route('/')
def language_select():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Select Language</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f4f7f6; }
            h2 { color: #333; margin-bottom: 30px; }
            .lang-btn {
                display: block; width: 200px; padding: 15px; margin: 10px;
                text-align: center; text-decoration: none; font-size: 1.2em;
                border-radius: 50px; color: white; transition: transform 0.1s;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
            .lang-btn:active { transform: scale(0.95); }
            .zh { background: #e91e63; }
            .en { background: #007bff; }
            .jp { background: #ff9800; }
        </style>
    </head>
    <body>
        <h2>請選擇語言 Language</h2>
        <a href="/menu?lang=zh" class="lang-btn zh">中文</a>
        <a href="/menu?lang=en" class="lang-btn en">English</a>
        <a href="/menu?lang=jp" class="lang-btn jp">日本語</a>
    </body>
    </html>
    """

# --- 3. 點餐頁面 ---
@app.route('/menu', methods=['GET', 'POST'])
def menu():
    lang = request.args.get('lang', 'zh')
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()
    table_from_url = request.args.get('table', '')

    if request.method == 'POST':
        table_number = request.form.get('table_number') 
        cart_json = request.form.get('cart_data')
        
        if not cart_json or cart_json == '[]':
            return f"Error <a href='/menu?lang={lang}'>Back</a>"

        try:
            cart_items = json.loads(cart_json)
        except:
            return "Data Error"
        
        total_price = 0
        items_display_list = []

        for item in cart_items:
            p_name = item['name'] 
            p_unit_price = int(item['unit_price'])
            p_qty = int(item['qty'])
            p_opts = item.get('options', [])
            
            opts_str = f"({','.join(p_opts)})" if p_opts else ""
            display_str = f"{p_name} {opts_str} x{p_qty}"
            
            items_display_list.append(display_str)
            total_price += (p_unit_price * p_qty)

        items_final_str = " + ".join(items_display_list)

        cur.execute(
            "INSERT INTO orders (table_number, items, total_price, lang) VALUES (%s, %s, %s, %s) RETURNING id",
            (table_number, items_final_str, total_price, lang)
        )
        new_order_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('order_success', order_id=new_order_id, lang=lang))

    try:
        cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
        products = cur.fetchall()
    except:
        return "資料庫讀取錯誤，請先執行 <a href='/init_db'>/init_db</a>"
    
    cur.close()
    conn.close()
    
    products_list = []
    for p in products:
        display_name = p[1]
        display_opts = p[6]
        has_multi_lang = len(p) >= 12
        
        if lang == 'en' and has_multi_lang:
            if p[8] and p[8].strip(): display_name = p[8]
            if p[10] and p[10].strip(): display_opts = p[10]
        elif lang == 'jp' and has_multi_lang:
            if p[9] and p[9].strip(): display_name = p[9]
            if p[11] and p[11].strip(): display_opts = p[11]

        display_cat = p[3]
        if p[3] == '主食': display_cat = t.get('category_main', 'Main')
        elif p[3] == '小菜': display_cat = t.get('category_side', 'Side')
        elif p[3] == '飲料': display_cat = t.get('category_drink', 'Drinks')

        products_list.append({
            'id': p[0], 
            'name': display_name, 
            'price': p[2], 
            'category': display_cat,
            'image_url': p[4] if p[4] else "https://via.placeholder.com/150",
            'is_available': p[5],
            'custom_options': display_opts.split(',') if display_opts else []
        })

    return render_frontend(table_from_url, products_list, t, lang)

def render_frontend(table_number, products_data, t, lang):
    products_json = json.dumps(products_data)
    t_json = json.dumps(t)
    table_input = f'<input type="text" id="visible_table_number" value="{table_number}" readonly>' if table_number else f'<input type="text" id="visible_table_number" placeholder="{t["table_placeholder"]}" required>'

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{t['title']}</title>
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
            .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: none; z-index: 999; justify-content: center; align-items: flex-end; }}
            .modal-content {{ background: white; width: 100%; max-width: 600px; border-radius: 20px 20px 0 0; padding: 20px; box-sizing: border-box; animation: slideUp 0.3s; max-height: 80vh; overflow-y: auto; }}
            @keyframes slideUp {{ from {{ transform: translateY(100%); }} to {{ transform: translateY(0); }} }}
            .cart-item-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding: 10px 0; }}
            .del-btn {{ color: white; background: #dc3545; border: none; padding: 5px 10px; border-radius: 5px; cursor: pointer; }}
            .option-tag {{ display: inline-block; border: 1px solid #ddd; padding: 8px 15px; border-radius: 20px; margin: 5px 5px 5px 0; color: #555; cursor: pointer; }}
            .option-tag.selected {{ background: #e3f2fd; border-color: #2196f3; color: #2196f3; font-weight: bold; }}
            .option-price {{ font-size: 0.8em; color: #e91e63; }}
            .cart-bar {{ position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 15px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; box-sizing: border-box; z-index: 500; }}
            .qty-btn {{ width: 35px; height: 35px; border-radius: 50%; border: 1px solid #ddd; background: white; font-size: 1.2em; display:flex; align-items:center; justify-content:center; cursor:pointer; }}
        </style>
    </head>
    <body>
        <div class="header">
            <a href="/" style="float:left; text-decoration:none; font-size:1.5em;">🌐</a>
            <h3>{t['welcome']}</h3>
            <div style="background:#f1f1f1; padding:10px; border-radius:8px;">{t['table_label']}：{table_input}</div>
        </div>
        <div class="container" id="menu-container"></div>
        
        <form method="POST" id="order-form">
            <input type="hidden" name="cart_data" id="cart_data_input">
            <input type="hidden" name="table_number" id="hidden_table_number">
            
            <div class="cart-bar" id="cart-bar" style="display:none;">
                <div style="flex-grow:1; cursor:pointer;" onclick="openCartModal()">
                    <span style="font-size:0.9em; color:#666;">▲ {t['cart_detail']}</span><br>
                    <span id="total-qty" style="background:#e91e63; color:white; padding:2px 8px; border-radius:10px; font-size:0.8em;">0</span> 
                    <b>{t['total']}: $<span id="total-price">0</span></b>
                </div>
                <button type="button" onclick="submitOrder()" style="background:#28a745; color:white; border:none; padding:12px 30px; border-radius:50px; font-size:1.1em; font-weight:bold;">{t['checkout']}</button>
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
                <div style="display:flex; align-items:center; margin-top:15px;">
                    <div class="qty-btn" onclick="changeQty(-1)">-</div>
                    <span style="margin:0 15px; font-size:1.2em; font-weight:bold;" id="modal-qty">1</span>
                    <div class="qty-btn" onclick="changeQty(1)">+</div>
                </div>
                <button style="width:100%; background:#28a745; color:white; padding:15px; border:none; border-radius:10px; margin-top:20px; font-size:1.1em;" onclick="addToCartConfirm()">{t['modal_add_cart']}</button>
                <button style="width:100%; background:white; color:#666; padding:10px; border:none; margin-top:5px;" onclick="closeOptionModal()">{t['modal_cancel']}</button>
            </div>
        </div>

        <div class="modal-overlay" id="cart-modal">
            <div class="modal-content">
                <h3>🛒 {t['cart_title']}</h3>
                <div id="cart-list-area" style="margin-bottom:20px;"></div>
                <button style="width:100%; background:#6c757d; color:white; padding:15px; border:none; border-radius:10px;" onclick="closeCartModal()">{t['close']}</button>
            </div>
        </div>

        <script>
            const products = {products_json};
            const t = {t_json};
            let cart = [];
            let currentItem = null;
            let currentQty = 1;
            let currentOptions = []; 
            let currentAddPrice = 0; 
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
                let btnHtml = p.is_available ? `<button class="add-btn" onclick="openOptionModal(${{p.id}})">${{t.add}}</button>` : `<button class="add-btn sold-out" disabled>${{t.sold_out}}</button>`;
                el.innerHTML = `<img src="${{p.image_url}}" class="menu-img"><div class="menu-info"><div><div class="menu-name">${{p.name}}</div><div class="menu-price">$${{p.price}}</div></div>${{btnHtml}}</div>`;
                container.appendChild(el);
            }});

            function parseOption(optStr) {{
                let cleanStr = optStr.replace('：', ':');
                if(cleanStr.includes(':+')) {{
                    const parts = cleanStr.split(':+');
                    const addP = parseInt(parts[1]) || 0;
                    return {{ name: parts[0], price: addP }};
                }}
                return {{ name: optStr, price: 0 }};
            }}

            function openOptionModal(id) {{
                currentItem = products.find(p => p.id === id);
                currentQty = 1;
                currentOptions = [];
                currentAddPrice = 0;
                document.getElementById('modal-title').innerText = currentItem.name;
                document.getElementById('modal-base-price-info').innerText = '(' + t.modal_unit_price + ' $' + currentItem.price + ')';
                updateModalTotal();
                const optArea = document.getElementById('modal-options-area');
                optArea.innerHTML = '';
                if (currentItem.custom_options && currentItem.custom_options.length > 0) {{
                    optArea.innerHTML = '<p style="font-size:0.9em; color:#888;">' + t.custom_options + '：</p>';
                    currentItem.custom_options.forEach(opt => {{
                        opt = opt.trim();
                        if(!opt) return;
                        const parsed = parseOption(opt);
                        const tag = document.createElement('div');
                        tag.className = 'option-tag';
                        let displayHTML = parsed.name;
                        if(parsed.price > 0) displayHTML += ` <span class="option-price">(+$${{parsed.price}})</span>`;
                        tag.innerHTML = displayHTML;
                        tag.onclick = function() {{
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
                const base = parseInt(currentItem.price);
                const add = parseInt(currentAddPrice);
                const total = (base + add) * currentQty;
                document.getElementById('modal-display-price').innerText = total;
                document.getElementById('modal-qty').innerText = currentQty;
            }}
            function closeOptionModal() {{ document.getElementById('option-modal').style.display = 'none'; }}
            function changeQty(n) {{ 
                if(currentQty + n >= 1) {{ currentQty += n; updateModalTotal(); }} 
            }}
            function addToCartConfirm() {{
                const base = parseInt(currentItem.price);
                const add = parseInt(currentAddPrice);
                cart.push({{ 
                    id: currentItem.id, name: currentItem.name, base_price: base, unit_price: base + add, qty: currentQty, options: [...currentOptions] 
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
                if(cart.length === 0) {{ listArea.innerHTML = '<p>' + t.empty_cart + '</p>'; }}
                cart.forEach((item, index) => {{
                    const row = document.createElement('div');
                    row.className = 'cart-item-row';
                    let displayOpts = [];
                    item.options.forEach(opt => {{
                        let parsed = parseOption(opt);
                        displayOpts.push(parsed.name + (parsed.price>0 ? `(+$${{parsed.price}})` : ''));
                    }});
                    const optsHtml = displayOpts.length ? `<br><small style='color:#888'>${{displayOpts.join(', ')}}</small>` : '';
                    row.innerHTML = `<div><b>${{item.name}}</b> x${{item.qty}}${{optsHtml}}<div style='color:#e91e63'>$${{item.unit_price * item.qty}}</div></div><button class="del-btn" onclick="removeFromCart(${{index}})">🗑️</button>`;
                    listArea.appendChild(row);
                }});
                document.getElementById('cart-modal').style.display = 'flex';
            }}
            function removeFromCart(index) {{
                if(confirm(t.confirm_delete)) {{ cart.splice(index, 1); updateCartBar(); if(cart.length === 0) closeCartModal(); else openCartModal(); }}
            }}
            function closeCartModal() {{ document.getElementById('cart-modal').style.display = 'none'; }}
            function submitOrder() {{
                const visibleTableInput = document.getElementById('visible_table_number');
                const tableVal = visibleTableInput.value.trim();
                if(!tableVal) {{ alert(t.table_placeholder); visibleTableInput.focus(); return; }}
                if(cart.length === 0) return;
                const totalP = cart.reduce((acc, item) => acc + (item.unit_price * item.qty), 0);
                if(!confirm(t.confirm_order + `\\n` + t.total + `: $${{totalP}}`)) return;
                document.getElementById('hidden_table_number').value = tableVal;
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
    lang = request.args.get('lang', 'zh')
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cur.fetchone()
    cur.close()
    conn.close()
    if not order: return "Error"
    items_html = order[2].replace(" + ", "<br><hr style='border:0; border-top:1px dashed #eee; margin:5px 0;'>")
    return f"""
    <!DOCTYPE html>
    <html>
    <head> <meta name="viewport" content="width=device-width, initial-scale=1"> </head>
    <body style="font-family: sans-serif; text-align: center; padding: 20px; background: #f4f7f6;">
        <div style="background: white; padding: 30px; border-radius: 15px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); max-width: 400px; margin: 0 auto;">
            <div style="font-size:50px; color:#28a745;">✅</div>
            <h2>{t['order_success']}</h2>
            <h3 style="color:#ff9800;">{t['table_label']}：{order[1]}</h3>
            <div style="text-align:left; background:#fafafa; padding:15px; border-radius:8px; margin:15px 0; font-size:1.1em;">{items_html}</div>
            <h3 style="text-align:right; color:#e91e63;">{t['total']}：${order[3]}</h3>
            <p>{t['kitchen_prep']}</p>
            <a href="/?lang={lang}" style="display:inline-block; padding:10px 30px; background:#007bff; color:white; text-decoration:none; border-radius:20px;">{t['continue_order']}</a>
        </div>
    </body>
    </html>
    """

# --- 5. 廚房端 ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date ORDER BY created_at DESC")
    orders = cur.fetchall()
    cur.close()
    conn.close()
    html = """
    <!DOCTYPE html><html><head><title>Kitchen</title><meta name="viewport" content="width=device-width, initial-scale=1"><style>
            body { font-family: sans-serif; background: #222; color: white; margin: 0; padding: 10px; }
            .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
            .order-card { background: #333; border-left: 8px solid #ff9800; margin-bottom: 15px; padding: 15px; border-radius: 5px; }
            .completed { border-left: 8px solid #28a745; opacity: 0.5; }
            .btn-done { background: #28a745; color: white; border: none; padding: 10px; border-radius: 5px; float: right; cursor: pointer; }
            .order-items { font-size: 1.2em; line-height: 1.6; margin-top: 10px; }
            a { color: white; background: #444; padding: 5px 10px; text-decoration: none; border-radius: 5px; }
        </style></head><body>
        <div class="header"><h3>👨‍🍳 訂單看板</h3><div><a href="/kitchen/menu">菜單管理</a> <a href="/daily_report" target="_blank">結帳單</a></div></div><div id="container">"""
    for order in orders:
        status_class = "completed" if order[4] == 'Completed' else ""
        btn = f"<button class='btn-done' onclick=\"completeOrder({order[0]})\">完成</button>" if order[4] != 'Completed' else ""
        items_display = order[2].replace(" + ", "<br>")
        lang_label = order[6].upper() if len(order) > 6 and order[6] else 'ZH'
        html += f"""<div class="order-card {status_class}">{btn}<div style="font-size:1.4em; color:#ff9800; font-weight:bold;">桌號：{order[1]} <span style='font-size:0.6em; color:#ccc; border:1px solid #555; padding:2px 5px; border-radius:4px;'>{lang_label}</span> <small style="color:#aaa; font-size:0.5em;">{order[5].strftime('%H:%M')}</small></div><div class="order-items">{items_display}</div></div>"""
    html += """</div><script>function completeOrder(id) { if(confirm('完成？')) fetch('/complete/'+id).then(()=>location.reload()); } setInterval(() => location.reload(), 10000);</script></body></html>"""
    return html

# --- 6. 菜單管理 ---
@app.route('/kitchen/menu', methods=['GET', 'POST'])
def kitchen_menu():
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST' and 'add_item' in request.form:
        cur.execute("""
            INSERT INTO products 
            (name, price, category, image_url, is_available, custom_options, sort_order, name_en, name_jp, custom_options_en, custom_options_jp) 
            VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s)
        """, (request.form['name'], request.form['price'], request.form['category'], request.form['image_url'], request.form['custom_options'], request.form.get('sort_order', 100), request.form.get('name_en', ''), request.form.get('name_jp', ''), request.form.get('custom_options_en', ''), request.form.get('custom_options_jp', '')))
        conn.commit()
        return redirect(url_for('kitchen_menu'))
    try:
        cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
        products = cur.fetchall()
    except: products = []
    cur.close()
    conn.close()
    html = """<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:sans-serif; padding:10px; background:#f4f4f9;} .btn{padding:5px 10px; text-decoration:none; color:white; border-radius:4px; font-size:0.9em; margin-left:5px; display:inline-block;} input{width:100%; margin:2px 0; padding:8px; box-sizing:border-box;} .lang-group{border:1px solid #ddd; padding:10px; margin-bottom:10px; background:#f9f9f9; border-radius:5px;}</style></head><body>
        <a href="/kitchen">⬅️ 回廚房</a><h2>🛠️ 菜單管理</h2><div style="background:white; padding:15px; border-radius:8px;"><h3>➕ 新增商品</h3><form method="POST"><input type="hidden" name="add_item" value="1">
                <div class="lang-group"><label>🇹🇼 中文 (必填)</label><input type="text" name="name" placeholder="商品名稱" required><input type="text" name="custom_options" placeholder="選項 (加麵:+20)"></div>
                <div class="lang-group"><label>🇺🇸 English</label><input type="text" name="name_en" placeholder="Product Name"><input type="text" name="custom_options_en" placeholder="Options"></div>
                <div class="lang-group"><label>🇯🇵 日本語</label><input type="text" name="name_jp" placeholder="商品名"><input type="text" name="custom_options_jp" placeholder="オプション"></div>
                <div class="lang-group"><label>⚙️ 設定</label><input type="number" name="price" placeholder="價格" required><input type="text" name="category" placeholder="分類" required><input type="text" name="image_url" placeholder="圖片URL"><input type="number" name="sort_order" placeholder="排序" value="100"></div>
                <button style="width:100%; background:#007bff; color:white; padding:10px; border:none; margin-top:5px;">新增</button></form></div><hr>"""
    for p in products:
        status = "🟢" if p[5] else "🔴"
        en_name = p[8] if len(p) > 8 else '-'
        html += f"""<div style='background:white; padding:10px; margin-bottom:5px; border-left:5px solid #007bff;'><div style="float:right; color:#888; font-size:0.8em;">排序: {p[7]}</div>{status} <b>{p[1]}</b> (${p[2]})<br><small style="color:#666">🇺🇸 {en_name or '-'}</small><br><div style="margin-top:5px;"><a href='/menu/toggle/{p[0]}' class='btn' style='background:#6c757d'>上架/完售</a> <a href='/menu/edit/{p[0]}' class='btn' style='background:#ffc107; color:black;'>編輯</a> <a href='/menu/delete/{p[0]}' class='btn' style='background:#dc3545' onclick="return confirm('確定刪除？')">刪除</a></div></div>"""
    return html + "</body></html>"

# --- 7. 編輯與其他功能 ---
@app.route('/menu/edit/<int:pid>', methods=['GET', 'POST'])
def menu_edit(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        cur.execute("""UPDATE products SET name=%s, price=%s, category=%s, image_url=%s, custom_options=%s, sort_order=%s, name_en=%s, name_jp=%s, custom_options_en=%s, custom_options_jp=%s WHERE id=%s""", (request.form['name'], request.form['price'], request.form['category'], request.form['image_url'], request.form['custom_options'], request.form['sort_order'], request.form['name_en'], request.form['name_jp'], request.form['custom_options_en'], request.form['custom_options_jp'], pid))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('kitchen_menu'))
    cur.execute("SELECT * FROM products WHERE id = %s", (pid,))
    p = cur.fetchone()
    cur.close()
    conn.close()
    if not p: return "Error"
    def v(val): return val if val is not None else ""
    name_en = v(p[8]) if len(p) > 8 else ""
    name_jp = v(p[9]) if len(p) > 9 else ""
    opt_en = v(p[10]) if len(p) > 10 else ""
    opt_jp = v(p[11]) if len(p) > 11 else ""
    return f"""<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>input{{width:100%; padding:8px; margin:5px 0; box-sizing:border-box;}} .grp{{background:#fff; padding:15px; margin-bottom:10px; border-radius:5px;}}</style></head><body style="font-family: sans-serif; padding: 20px; max-width: 600px; margin: 0 auto; background:#f4f4f9;"><h2>✏️ 編輯</h2><form method="POST"><div class="grp"><label>🇹🇼 中文</label><input type="text" name="name" value="{v(p[1])}" required><input type="text" name="custom_options" value="{v(p[6])}"></div><div class="grp"><label>🇺🇸 English</label><input type="text" name="name_en" value="{name_en}"><input type="text" name="custom_options_en" value="{opt_en}"></div><div class="grp"><label>🇯🇵 日本語</label><input type="text" name="name_jp" value="{name_jp}"><input type="text" name="custom_options_jp" value="{opt_jp}"></div><div class="grp"><label>設定</label>價格：<input type="number" name="price" value="{p[2]}" required>分類：<input type="text" name="category" value="{p[3]}" required>排序：<input type="number" name="sort_order" value="{p[7]}">圖片：<input type="text" name="image_url" value="{v(p[4])}"></div><button type="submit" style="background:#28a745; color:white; border:none; padding:10px 30px; border-radius:5px; width:100%;">儲存</button><br><br><a href="/kitchen/menu">取消</a></form></body></html>"""

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

# --- 啟動背景執行緒 (保持喚醒) ---
def keep_alive():
    """每14分鐘ping一次，防止Render休眠"""
    while True:
        try:
            # 等待 14 分鐘 (840秒)
            time.sleep(840)
            print(f"⏰ Keep-alive: Pinging {https://qr-mbdv.onrender.com} ...")
            # 發送請求給自己
            urllib.request.urlopen(https://qr-mbdv.onrender.com)
        except Exception as e:
            print(f"⚠️ Keep-alive failed: {e}")

# 建立並啟動背景執行緒
if os.environ.get("WERKZEUG_RUN_MAIN") != "true": # 防止在開發模式下重複啟動
    t = threading.Thread(target=keep_alive)
    t.daemon = True
    t.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
