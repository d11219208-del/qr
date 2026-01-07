import os
import psycopg2
import json
import threading
import urllib.request
import time
from flask import Flask, request, redirect, url_for, render_template_string
from datetime import datetime, date

app = Flask(__name__)

# --- 資料庫連線 ---
def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 翻譯載入 (防呆版) ---
def load_translations():
    fallback = {
        "zh": {
            "title": "線上點餐", "welcome": "歡迎點餐", "table_placeholder": "請輸入桌號", 
            "table_label": "桌號", "add": "加入", "sold_out": "已售完", "cart_detail": "查看明細", 
            "total": "合計", "checkout": "去結帳", "cart_title": "購物車明細", "empty_cart": "購物車是空的", 
            "close": "關閉", "confirm_delete": "確定刪除此項目？", "confirm_order": "確定送出訂單？", 
            "modal_unit_price": "單價", "modal_add_cart": "加入購物車", "modal_cancel": "取消", 
            "custom_options": "客製化選項", "order_success": "下單成功！", "kitchen_prep": "廚房備餐中", 
            "continue_order": "繼續點餐", "category_main": "主食", "category_side": "小菜", "category_drink": "飲料",
            "print_receipt_opt": "需要列印收據嗎？", "daily_seq_prefix": "單號"
        },
        "en": {
            "title": "Online Ordering", "welcome": "Welcome", "table_placeholder": "Enter Table No.",
            "table_label": "Table", "add": "Add", "sold_out": "Sold Out", "cart_detail": "View Cart",
            "total": "Total", "checkout": "Checkout", "cart_title": "Cart Details", "empty_cart": "Cart is empty",
            "close": "Close", "confirm_delete": "Remove item?", "confirm_order": "Submit Order?",
            "modal_unit_price": "Price", "modal_add_cart": "Add to Cart", "modal_cancel": "Cancel",
            "custom_options": "Options", "order_success": "Order Placed!", "kitchen_prep": "Preparing...",
            "continue_order": "Order More", "category_main": "Main Dish", "category_side": "Side Dish", "category_drink": "Drinks",
            "print_receipt_opt": "Print Receipt?", "daily_seq_prefix": "No."
        },
        "jp": {
            "title": "オンライン注文", "welcome": "いらっしゃいませ", "table_placeholder": "卓番を入力",
            "table_label": "卓番", "add": "追加", "sold_out": "完売", "cart_detail": "カートを見る",
            "total": "合計", "checkout": "会計する", "cart_title": "カート詳細", "empty_cart": "カートは空です",
            "close": "閉じる", "confirm_delete": "削除しますか？", "confirm_order": "注文を確定しますか？",
            "modal_unit_price": "単価", "modal_add_cart": "カートに入れる", "modal_cancel": "キャンセル",
            "custom_options": "オプション", "order_success": "注文完了！", "kitchen_prep": "調理中...",
            "continue_order": "続けて注文", "category_main": "メイン", "category_side": "サイド", "category_drink": "ドリンク",
            "print_receipt_opt": "レシートを印刷しますか？", "daily_seq_prefix": "番号"
        }
    }
    try:
        base_path = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_path, 'translations.json')
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return fallback

# --- 1. 資料庫初始化 (升級結構) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 建立基本表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price INTEGER NOT NULL,
                category VARCHAR(50),
                image_url TEXT,
                is_available BOOLEAN DEFAULT TRUE,
                custom_options TEXT,
                sort_order INTEGER DEFAULT 100,
                name_en VARCHAR(100), name_jp VARCHAR(100),
                custom_options_en TEXT, custom_options_jp TEXT
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

        # 升級結構：加入 daily_seq (流水號), content_json (原始數據), need_receipt (列印選項)
        alter_commands = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS daily_seq INTEGER DEFAULT 0;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS need_receipt BOOLEAN DEFAULT FALSE;",
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
                print(f"Update skipped: {e}")

        return "資料庫升級完成！請返回首頁。"
    except Exception as e:
        return f"Init failed: {e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 首頁 (語言選擇) ---
@app.route('/')
def language_select():
    return """
    <!DOCTYPE html>
    <html><head><title>Select Language</title><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;background:#f4f7f6;}
    .lang-btn{width:200px;padding:15px;margin:10px;text-align:center;text-decoration:none;font-size:1.2em;border-radius:50px;color:white;box-shadow:0 4px 6px rgba(0,0,0,0.1);}
    .zh{background:#e91e63;} .en{background:#007bff;} .jp{background:#ff9800;}</style></head>
    <body><h2>請選擇語言 Language</h2>
    <a href="/menu?lang=zh" class="lang-btn zh">中文</a>
    <a href="/menu?lang=en" class="lang-btn en">English</a>
    <a href="/menu?lang=jp" class="lang-btn jp">日本語</a>
    </body></html>
    """

# --- 3. 點餐頁面 ---
@app.route('/menu', methods=['GET', 'POST'])
def menu():
    lang = request.args.get('lang', 'zh')
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        table_number = request.form.get('table_number')
        cart_json = request.form.get('cart_data')
        need_receipt = request.form.get('need_receipt') == 'on'
        
        if not cart_json or cart_json == '[]': return "Empty Cart"

        try:
            cart_items = json.loads(cart_json)
        except:
            return "Data Error"
        
        # 計算總價 & 生成顯示字串 (Legacy purpose)
        total_price = 0
        items_display_list = []
        for item in cart_items:
            # item 結構包含: name, unit_price, qty, options, category
            price = int(item['unit_price'])
            qty = int(item['qty'])
            opts = item.get('options', [])
            opts_str = f"({','.join(opts)})" if opts else ""
            items_display_list.append(f"{item['name']} {opts_str} x{qty}")
            total_price += (price * qty)

        items_str = " + ".join(items_display_list)

        # 生成每日流水號 (Daily Seq)
        # 邏輯：計算今天已經有幾筆訂單，然後 +1
        cur.execute("SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE")
        count_today = cur.fetchone()[0]
        new_seq = count_today + 1

        cur.execute(
            """INSERT INTO orders 
               (table_number, items, total_price, lang, daily_seq, content_json, need_receipt) 
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (table_number, items_str, total_price, lang, new_seq, cart_json, need_receipt)
        )
        new_order_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('order_success', order_id=new_order_id, lang=lang))

    # GET: 顯示菜單
    cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
    products = cur.fetchall()
    cur.close()
    conn.close()
    
    products_list = []
    for p in products:
        # p: 0:id, 1:name, 2:price, 3:cat, 4:img, 5:avail, 6:opts, 7:sort, 8:en, 9:jp, 10:opt_en, 11:opt_jp
        display_name = p[1]
        display_opts = p[6]
        has_multi = len(p) >= 12
        if lang == 'en' and has_multi:
            display_name = p[8] or p[1]
            display_opts = p[10] or p[6]
        elif lang == 'jp' and has_multi:
            display_name = p[9] or p[1]
            display_opts = p[11] or p[6]

        products_list.append({
            'id': p[0], 'name': display_name, 'price': p[2], 'category': p[3], 
            'image_url': p[4] or "", 'is_available': p[5], 
            'custom_options': display_opts.split(',') if display_opts else [],
            'raw_category': p[3] # 用來判斷廚房分區
        })

    return render_frontend(products_list, t, lang)

def render_frontend(products_data, t, lang):
    products_json = json.dumps(products_data)
    t_json = json.dumps(t)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{t['title']}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
        <style>
            body {{ font-family: 'Microsoft JhengHei', sans-serif; margin: 0; padding-bottom: 90px; background: #f4f7f6; }}
            .header {{ background: white; padding: 15px; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .menu-item {{ background: white; border-radius: 12px; padding: 10px; display: flex; margin: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
            .menu-img {{ width: 90px; height: 90px; border-radius: 8px; object-fit: cover; background: #eee; }}
            .menu-info {{ flex: 1; padding-left: 15px; display: flex; flex-direction: column; justify-content: space-between; }}
            .add-btn {{ background: #28a745; color: white; border: none; padding: 8px 15px; border-radius: 20px; align-self: flex-end; }}
            .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: none; z-index: 999; justify-content: center; align-items: flex-end; }}
            .modal-content {{ background: white; width: 100%; border-radius: 20px 20px 0 0; padding: 20px; max-height: 80vh; overflow-y: auto; }}
            .cart-bar {{ position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 15px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; z-index: 500; box-sizing: border-box; }}
            .option-tag {{ display: inline-block; border: 1px solid #ddd; padding: 8px 15px; border-radius: 20px; margin: 5px; cursor: pointer; }}
            .option-tag.selected {{ background: #e3f2fd; border-color: #2196f3; color: #2196f3; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h3>{t['welcome']}</h3>
            <input type="text" id="visible_table" placeholder="{t['table_placeholder']}" style="padding:10px; width:100%; box-sizing:border-box;">
        </div>
        <div id="container"></div>
        
        <form method="POST" id="order-form">
            <input type="hidden" name="cart_data" id="cart_data_input">
            <input type="hidden" name="table_number" id="hidden_table">
            
            <div class="cart-bar" id="cart-bar" style="display:none;">
                <div onclick="openCartModal()" style="flex-grow:1;">
                    <span id="total-qty" style="background:#e91e63; color:white; padding:2px 8px; border-radius:10px;">0</span> 
                    <b>{t['total']}: $<span id="total-price">0</span></b>
                </div>
                <div style="display:flex; align-items:center;">
                    <label style="margin-right:10px; font-size:0.8em;">
                        <input type="checkbox" name="need_receipt" checked> {t['print_receipt_opt']}
                    </label>
                    <button type="button" onclick="submitOrder()" style="background:#28a745; color:white; border:none; padding:10px 20px; border-radius:50px;">{t['checkout']}</button>
                </div>
            </div>
        </form>

        <div class="modal-overlay" id="option-modal"><div class="modal-content">
            <h3 id="m-title"></h3>
            <div id="m-opts"></div>
            <div style="margin-top:15px; display:flex; justify-content:center; align-items:center;">
                <button onclick="q(-1)" style="width:40px;height:40px;">-</button>
                <span id="m-qty" style="margin:0 20px; font-weight:bold;">1</span>
                <button onclick="q(1)" style="width:40px;height:40px;">+</button>
            </div>
            <button onclick="addToCartConf()" style="width:100%; background:#28a745; color:white; padding:15px; border:none; border-radius:10px; margin-top:20px;">{t['modal_add_cart']}</button>
            <button onclick="closeM()" style="width:100%; background:white; color:#666; padding:10px; border:none; margin-top:5px;">{t['modal_cancel']}</button>
        </div></div>

        <div class="modal-overlay" id="cart-modal"><div class="modal-content">
            <h3>{t['cart_title']}</h3>
            <div id="c-list"></div>
            <button onclick="document.getElementById('cart-modal').style.display='none'" style="width:100%; padding:15px; margin-top:10px;">{t['close']}</button>
        </div></div>

        <script>
            const prods = {products_json};
            const t = {t_json};
            let cart = [], curP = null, curQ = 1, curOpts = [], curAddP = 0;
            
            // 渲染菜單
            const c = document.getElementById('container');
            let cat = "";
            prods.forEach(p => {{
                if(p.category !== cat) {{
                    c.innerHTML += `<div style="padding:10px; color:#666; font-weight:bold;">${{p.category}}</div>`;
                    cat = p.category;
                }}
                c.innerHTML += `
                <div class="menu-item">
                    <img src="${{p.image_url}}" class="menu-img">
                    <div class="menu-info">
                        <div><b>${{p.name}}</b><br><span style="color:#e91e63">$${{p.price}}</span></div>
                        <button class="add-btn" onclick="openOpt(${{p.id}})">${{t.add}}</button>
                    </div>
                </div>`;
            }});

            function openOpt(id) {{
                curP = prods.find(p=>p.id===id); curQ=1; curOpts=[]; curAddP=0;
                document.getElementById('m-title').innerText = curP.name;
                const area = document.getElementById('m-opts'); area.innerHTML='';
                curP.custom_options.forEach(o => {{
                    if(!o) return;
                    let parts = o.split(':+'); 
                    let price = parts[1] ? parseInt(parts[1]) : 0;
                    let name = parts[0];
                    let el = document.createElement('div');
                    el.className = 'option-tag';
                    el.innerText = name + (price?` (+$${{price}})`:'');
                    el.onclick = () => {{
                        if(curOpts.includes(o)) {{ curOpts=curOpts.filter(x=>x!==o); curAddP-=price; el.classList.remove('selected'); }}
                        else {{ curOpts.push(o); curAddP+=price; el.classList.add('selected'); }}
                    }};
                    area.appendChild(el);
                }});
                document.getElementById('m-qty').innerText=1;
                document.getElementById('option-modal').style.display='flex';
            }}
            
            function q(n){{ if(curQ+n>=1) {{ curQ+=n; document.getElementById('m-qty').innerText=curQ; }} }}
            function closeM(){{ document.getElementById('option-modal').style.display='none'; }}
            
            function addToCartConf() {{
                cart.push({{
                    id: curP.id, name: curP.name, 
                    unit_price: curP.price + curAddP, 
                    qty: curQ, options: [...curOpts],
                    category: curP.raw_category // 用來做廚房分單
                }});
                closeM(); updateBar();
            }}

            function updateBar() {{
                if(cart.length>0) {{
                    document.getElementById('cart-bar').style.display='flex';
                    let tot = cart.reduce((a,b)=>a+b.unit_price*b.qty,0);
                    document.getElementById('total-price').innerText=tot;
                    document.getElementById('total-qty').innerText = cart.reduce((a,b)=>a+b.qty,0);
                }} else document.getElementById('cart-bar').style.display='none';
            }}

            function openCartModal() {{
                let h = '';
                cart.forEach((i, idx) => {{
                    h += `<div style="border-bottom:1px solid #eee; padding:10px; display:flex; justify-content:space-between;">
                        <div><b>${{i.name}}</b> x${{i.qty}}<br><small>${{i.options.join(',')}}</small></div>
                        <button onclick="cart.splice(${{idx}},1); openCartModal(); updateBar();" style="color:red; border:none; background:none;">🗑️</button>
                    </div>`;
                }});
                document.getElementById('c-list').innerHTML = h || t.empty_cart;
                document.getElementById('cart-modal').style.display='flex';
            }}

            function submitOrder() {{
                let tbl = document.getElementById('visible_table').value;
                if(!tbl) {{ alert(t.table_placeholder); return; }}
                document.getElementById('hidden_table').value = tbl;
                document.getElementById('cart_data_input').value = JSON.stringify(cart);
                if(confirm(t.confirm_order)) document.getElementById('order-form').submit();
            }}
        </script>
    </body>
    </html>
    """

# --- 4. 下單成功 ---
@app.route('/order_success')
def order_success():
    oid = request.args.get('order_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT daily_seq, table_number FROM orders WHERE id=%s", (oid,))
    res = cur.fetchone()
    conn.close()
    
    seq = f"{res[0]:03d}" if res else "---"
    return f"""
    <div style="text-align:center; padding:50px; font-family:sans-serif;">
        <h1 style="color:green; font-size:50px;">✅</h1>
        <h2>下單成功！</h2>
        <div style="font-size:3em; font-weight:bold; margin:20px;">{seq}</div>
        <p>您的單號 (No.{seq})</p>
        <p>廚房正在準備中...</p>
        <a href="/">回到首頁</a>
    </div>
    """

# --- 5. 廚房看板 (增強版) ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
    # 排除已取消 (Status='Cancelled') 的訂單
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date AND status != 'Cancelled' ORDER BY daily_seq DESC")
    orders = cur.fetchall()
    conn.close()

    html = """
    <!DOCTYPE html>
    <html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{background:#222;color:white;font-family:sans-serif;padding:10px;}
        .card{background:#333; margin-bottom:10px; padding:15px; border-radius:5px; border-left:5px solid #ff9800; position:relative;}
        .done{border-left-color:#28a745; opacity:0.6;}
        .seq{font-size:1.5em; font-weight:bold; color:#ff9800;}
        .btn{padding:5px 10px; margin-left:5px; cursor:pointer; border:none; border-radius:3px;}
        .btn-print{background:#17a2b8; color:white;}
        .btn-edit{background:#ffc107; color:black;}
        .btn-del{background:#dc3545; color:white;}
        .btn-done{background:#28a745; color:white; float:right; padding:10px;}
        a {text-decoration:none;}
    </style>
    </head><body>
    <h2>👨‍🍳 廚房接單</h2>
    """
    for o in orders:
        # o: 0:id, 1:tbl, 2:items_str, 3:price, 4:status, 5:time, 6:lang, 7:daily_seq, 8:json, 9:receipt
        oid = o[0]
        seq = f"{o[7]:03d}"
        status = o[4]
        cls = "done" if status == 'Completed' else ""
        
        # 顯示內容
        items_html = o[2].replace(" + ", "<br>")
        
        # 按鈕區
        btns = ""
        if status != 'Completed':
            btns += f"<button class='btn btn-done' onclick=\"location.href='/kitchen/complete/{oid}'\">完成</button>"
        
        # 功能按鈕
        actions = f"""
            <div style="margin-top:10px; border-top:1px solid #555; padding-top:10px;">
                <a href="/print_order/{oid}" target="_blank" class="btn btn-print">🖨️ 列印單據</a>
                <a href="/order/edit/{oid}" class="btn btn-edit">✏️ 編輯</a>
                <a href="/order/delete/{oid}" class="btn btn-del" onclick="return confirm('確定刪除此單 (將保留紀錄)?')">🗑️ 刪除</a>
            </div>
        """

        html += f"""
        <div class="card {cls}">
            {btns}
            <span class="seq">#{seq}</span> 桌號: {o[1]} 
            <small style="color:#aaa">({o[5].strftime('%H:%M')})</small>
            <div style="margin-top:10px; font-size:1.2em;">{items_html}</div>
            {actions}
        </div>
        """
    return html + "</body></html>"

# --- 6. 功能：完成、軟刪除、編輯 ---
@app.route('/kitchen/complete/<int:oid>')
def complete_order(oid):
    conn = get_db_connection()
    conn.cursor().execute("UPDATE orders SET status='Completed' WHERE id=%s", (oid,))
    conn.commit()
    conn.close()
    return redirect('/kitchen')

@app.route('/order/delete/<int:oid>')
def delete_order(oid):
    conn = get_db_connection()
    # 軟刪除：狀態改為 Cancelled，不從資料庫移除
    conn.cursor().execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (oid,))
    conn.commit()
    conn.close()
    return redirect('/kitchen')

# --- 7. 編輯訂單頁面 ---
@app.route('/order/edit/<int:oid>', methods=['GET', 'POST'])
def edit_order(oid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        # 接收修改後的數據 (這裡簡化為修改數量或刪除項目)
        # 為了簡便，我們重新讀取表單中的 qty
        new_items = []
        raw_indices = request.form.getlist('item_index') # 原本的索引
        
        # 讀取原始數據來比對
        cur.execute("SELECT content_json FROM orders WHERE id=%s", (oid,))
        original_json = cur.fetchone()[0]
        original_items = json.loads(original_json) if original_json else []
        
        total_price = 0
        display_list = []
        
        for idx in raw_indices:
            i = int(idx)
            new_qty = int(request.form.get(f'qty_{i}', 0))
            if new_qty > 0:
                item = original_items[i]
                item['qty'] = new_qty # 更新數量
                
                # 重算
                total_price += item['unit_price'] * new_qty
                opts_str = f"({','.join(item['options'])})" if item['options'] else ""
                display_list.append(f"{item['name']} {opts_str} x{new_qty}")
                new_items.append(item)
        
        # 更新資料庫
        new_json = json.dumps(new_items)
        new_str = " + ".join(display_list)
        
        cur.execute("UPDATE orders SET content_json=%s, items=%s, total_price=%s WHERE id=%s", 
                    (new_json, new_str, total_price, oid))
        conn.commit()
        conn.close()
        return redirect('/kitchen')

    # GET: 顯示編輯表單
    cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
    order = cur.fetchone()
    conn.close()
    
    if not order or not order[8]: # order[8] is content_json
        return "無法編輯 (舊資料或格式錯誤)"
        
    items = json.loads(order[8])
    
    html = f"""
    <!DOCTYPE html>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body{{font-family:sans-serif; padding:20px;}} .row{{border-bottom:1px solid #ddd; padding:10px; display:flex; justify-content:space-between; align-items:center;}} input{{width:50px; padding:5px;}}</style>
    <h2>✏️ 編輯訂單 #{order[7]:03d}</h2>
    <form method="POST">
    """
    
    for i, item in enumerate(items):
        opts = ",".join(item.get('options',[]))
        html += f"""
        <div class="row">
            <div>
                <b>{item['name']}</b> <small>{opts}</small><br>
                ${item['unit_price']}
            </div>
            <div>
                數量: <input type="number" name="qty_{i}" value="{item['qty']}" min="0">
                <input type="hidden" name="item_index" value="{i}">
                <br><small style="color:red">(設為0即刪除)</small>
            </div>
        </div>
        """
    
    html += """
        <br>
        <button type="submit" style="width:100%; background:#28a745; color:white; padding:15px; border:none; font-size:1.2em;">儲存變更</button>
        <br><br>
        <a href="/kitchen" style="display:block; text-align:center;">取消返回</a>
    </form>
    """
    return html

# --- 8. 列印專用頁面 (重點功能) ---
@app.route('/print_order/<int:oid>')
def print_order(oid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
    o = cur.fetchone()
    conn.close()
    
    if not o or not o[8]: return "無資料"
    
    # 解析資料
    seq = f"{o[7]:03d}"
    table = o[1]
    time_str = o[5].strftime('%Y-%m-%d %H:%M')
    items = json.loads(o[8]) # item結構: name, qty, options, category
    need_receipt = o[9] # 是否列印收據
    
    # 分類邏輯 (湯區 vs 麵區)
    # 邏輯：如果 category 包含 '主食' 或 'Main' -> 麵區
    #      其他 (小菜、湯、飲料) -> 湯區
    noodle_items = []
    soup_items = []
    
    for i in items:
        cat = i.get('category', '')
        # 簡單分類邏輯
        if '主食' in cat or 'Main' in cat or '麵' in cat:
            noodle_items.append(i)
        else:
            soup_items.append(i)

    def render_ticket(title, item_list, is_receipt=False):
        if not item_list and not is_receipt: return ""
        
        html = f"""
        <div class="ticket">
            <div class="header">
                <h2>{title}</h2>
                <h1>#{seq}</h1>
                <p>桌號: {table} | {time_str}</p>
            </div>
            <hr style="border-top: 1px dashed black;">
            <div class="items">
        """
        total = 0
        for item in item_list:
            opts = f"<br><span class='opt'>({','.join(item['options'])})</span>" if item['options'] else ""
            price_display = f"${item['unit_price']*item['qty']}" if is_receipt else ""
            html += f"""
                <div class="item-row">
                    <span class="qty">{item['qty']}</span>
                    <span class="name">{item['name']} {opts}</span>
                    <span class="price">{price_display}</span>
                </div>
            """
            total += item['unit_price'] * item['qty']
            
        html += "</div>"
        if is_receipt:
            html += f"""
            <hr style="border-top: 1px solid black;">
            <div style="text-align:right; font-size:1.2em; font-weight:bold;">合計: ${total}</div>
            <div style="text-align:center; margin-top:20px;">謝謝光臨</div>
            """
        html += "</div><div class='page-break'></div>"
        return html

    # 組合 HTML
    # 根據需求：收據(若客人要) + 麵區單 + 湯區單
    body_content = ""
    if need_receipt:
        body_content += render_ticket("結帳單 (Receipt)", items, is_receipt=True)
    
    if noodle_items:
        body_content += render_ticket("🍜 麵區工單", noodle_items)
    
    if soup_items:
        body_content += render_ticket("🍲 湯區/小菜工單", soup_items)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Print Order #{seq}</title>
        <style>
            body {{ font-family: 'Courier New', monospace; font-size: 14px; margin: 0; padding: 0; background: #eee; }}
            .ticket {{ width: 58mm; background: white; margin: 10px auto; padding: 10px; box-shadow: 0 0 5px rgba(0,0,0,0.2); }}
            .header {{ text-align: center; }}
            h1 {{ font-size: 2em; margin: 5px 0; }}
            h2 {{ font-size: 1.2em; margin: 5px 0; border: 1px solid black; display:inline-block; padding:2px 10px; }}
            .item-row {{ display: flex; margin-bottom: 8px; align-items: flex-start; }}
            .qty {{ font-weight: bold; font-size: 1.2em; width: 25px; }}
            .name {{ flex-grow: 1; }}
            .opt {{ font-size: 0.85em; color: #444; }}
            .price {{ text-align: right; min-width: 40px; }}
            
            @media print {{
                body {{ background: white; }}
                .ticket {{ width: 100%; box-shadow: none; margin: 0; padding: 0; }}
                .page-break {{ page-break-after: always; display: block; height: 1px; }}
                /* 隱藏瀏覽器預設頁首頁尾 */
                @page {{ margin: 0; }}
            }}
        </style>
    </head>
    <body onload="window.print()">
        {body_content}
    </body>
    </html>
    """

# --- 9. 防休眠 ---
def keep_alive():
    url = "http://127.0.0.1:10000/"
    while True:
        try: urllib.request.urlopen(url)
        except: pass
        time.sleep(800)
t = threading.Thread(target=keep_alive)
t.daemon = True
t.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
