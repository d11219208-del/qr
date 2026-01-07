import os
import psycopg2
import json
import threading
import urllib.request
import time
import re
from flask import Flask, request, redirect, url_for, render_template_string
from datetime import datetime

app = Flask(__name__)

# --- 資料庫連線 ---
def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 翻譯設定 ---
def load_translations():
    # 預設翻譯，防止讀取檔案失敗
    fallback = {
        "zh": {
            "title": "線上點餐", "welcome": "歡迎點餐", "table_placeholder": "請輸入桌號", 
            "table_label": "桌號", "add": "加入", "sold_out": "已售完", "cart_detail": "查看明細", 
            "total": "合計", "checkout": "去結帳", "cart_title": "購物車明細", "empty_cart": "購物車是空的", 
            "close": "關閉", "confirm_delete": "確定刪除此項目？", "confirm_order": "確定送出訂單？", 
            "modal_unit_price": "單價", "modal_add_cart": "加入購物車", "modal_cancel": "取消", 
            "custom_options": "客製化選項", "order_success": "下單成功！", "kitchen_prep": "廚房備餐中", 
            "print_receipt_opt": "需要列印收據嗎？", "daily_seq_prefix": "單號"
        },
        "en": {
            "title": "Online Ordering", "welcome": "Welcome", "table_placeholder": "Enter Table No.",
            "table_label": "Table", "add": "Add", "sold_out": "Sold Out", "cart_detail": "View Cart",
            "total": "Total", "checkout": "Checkout", "cart_title": "Cart Details", "empty_cart": "Cart is empty",
            "close": "Close", "confirm_delete": "Remove item?", "confirm_order": "Submit Order?",
            "modal_unit_price": "Price", "modal_add_cart": "Add to Cart", "modal_cancel": "Cancel",
            "custom_options": "Options", "order_success": "Order Placed!", "kitchen_prep": "Preparing...",
            "print_receipt_opt": "Print Receipt?", "daily_seq_prefix": "No."
        },
        "jp": {
            "title": "オンライン注文", "welcome": "いらっしゃいませ", "table_placeholder": "卓番を入力",
            "table_label": "卓番", "add": "追加", "sold_out": "完売", "cart_detail": "カートを見る",
            "total": "合計", "checkout": "会計する", "cart_title": "カート詳細", "empty_cart": "カートは空です",
            "close": "閉じる", "confirm_delete": "削除しますか？", "confirm_order": "注文を確定しますか？",
            "modal_unit_price": "単価", "modal_add_cart": "カートに入れる", "modal_cancel": "キャンセル",
            "custom_options": "オプション", "order_success": "注文完了！", "kitchen_prep": "調理中...",
            "print_receipt_opt": "レシートを印刷しますか？", "daily_seq_prefix": "番号"
        }
    }
    try:
        base_path = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base_path, 'translations.json'), 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return fallback

# --- 1. 資料庫初始化 (完整修復版) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    msg = []
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
        msg.append("基本表格檢查完成")

        # 補齊所有需要的欄位
        alter_commands = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS daily_seq INTEGER DEFAULT 0;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS need_receipt BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_en VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_jp VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_en TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_jp TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 100;"
        ]
        
        for cmd in alter_commands:
            try:
                cur.execute(cmd)
            except:
                pass
        
        msg.append("所有欄位更新完成")
        return "<br>".join(msg) + "<br><br><a href='/'>👉 回首頁</a> | <a href='/admin'>⚙️ 進入菜單管理</a>"
    except Exception as e:
        return f"Init Failed: {e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 首頁 (語言選擇) ---
@app.route('/')
def index():
    return """
    <!DOCTYPE html>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <body style="font-family:sans-serif; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; background:#f4f7f6;">
        <h2>請選擇語言 Language</h2>
        <a href="/menu?lang=zh" style="padding:15px 50px; margin:10px; background:#e91e63; color:white; text-decoration:none; border-radius:30px; font-size:1.2em;">中文</a>
        <a href="/menu?lang=en" style="padding:15px 50px; margin:10px; background:#007bff; color:white; text-decoration:none; border-radius:30px; font-size:1.2em;">English</a>
        <a href="/menu?lang=jp" style="padding:15px 50px; margin:10px; background:#ff9800; color:white; text-decoration:none; border-radius:30px; font-size:1.2em;">日本語</a>
        <br><br>
        <a href="/kitchen" style="color:#666;">👨‍🍳 廚房接單</a> | <a href="/admin" style="color:#666;">⚙️ 菜單管理</a>
    </body>
    """

# --- 3. 菜單管理後台 (NEW!) ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_menu():
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            cur.execute("""
                INSERT INTO products (name, price, category, custom_options, sort_order, is_available)
                VALUES (%s, %s, %s, %s, %s, TRUE)
            """, (
                request.form.get('name'),
                request.form.get('price'),
                request.form.get('category'),
                request.form.get('options'),
                request.form.get('sort', 100)
            ))
        elif action == 'delete':
            pid = request.form.get('id')
            cur.execute("DELETE FROM products WHERE id=%s", (pid,))
        elif action == 'toggle':
            pid = request.form.get('id')
            cur.execute("UPDATE products SET is_available = NOT is_available WHERE id=%s", (pid,))
            
        conn.commit()
        return redirect('/admin')

    cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
    products = cur.fetchall()
    conn.close()
    
    html = """
    <!DOCTYPE html>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{font-family:sans-serif; padding:20px; max-width:800px; margin:auto;}
        input, select {padding:8px; margin:5px 0; width:100%; box-sizing:border-box;}
        .item {border:1px solid #ddd; padding:10px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;}
        .btn {padding:5px 10px; cursor:pointer; border:none; color:white;}
    </style>
    <h2>⚙️ 菜單管理</h2>
    
    <div style="background:#f9f9f9; padding:15px; border-radius:10px; margin-bottom:20px;">
        <h3>新增菜色</h3>
        <form method="POST">
            <input type="hidden" name="action" value="add">
            <input type="text" name="name" placeholder="菜名 (例如: 牛肉麵)" required>
            <input type="number" name="price" placeholder="價格" required>
            <input type="text" name="category" placeholder="分類 (例如: 主食)" required>
            <input type="text" name="options" placeholder="選項 (例如: 加麵:+20, 不蔥)">
            <input type="number" name="sort" placeholder="排序權重 (越小越前面)" value="100">
            <button class="btn" style="background:#28a745; width:100%; padding:10px;">新增</button>
        </form>
    </div>

    <h3>現有菜單</h3>
    """
    for p in products:
        # p: 0:id, 1:name, 2:price, 3:cat, 5:avail, 6:opts
        status_color = "#28a745" if p[5] else "#ccc"
        status_text = "上架中" if p[5] else "已下架"
        
        html += f"""
        <div class="item">
            <div style="flex-grow:1;">
                <b>{p[1]}</b> (${p[2]}) <span style="font-size:0.8em; color:#666;">[{p[3]}]</span><br>
                <small>{p[6] or ''}</small>
            </div>
            <div style="display:flex; flex-direction:column; gap:5px;">
                <form method="POST" style="margin:0;">
                    <input type="hidden" name="action" value="toggle">
                    <input type="hidden" name="id" value="{p[0]}">
                    <button class="btn" style="background:{status_color}; width:80px;">{status_text}</button>
                </form>
                <form method="POST" style="margin:0;" onsubmit="return confirm('確定刪除?')">
                    <input type="hidden" name="action" value="delete">
                    <input type="hidden" name="id" value="{p[0]}">
                    <button class="btn" style="background:#dc3545; width:80px;">刪除</button>
                </form>
            </div>
        </div>
        """
    return html + "<br><a href='/'>回首頁</a>"

# --- 4. 點餐前台 ---
@app.route('/menu', methods=['GET', 'POST'])
def menu():
    lang = request.args.get('lang', 'zh')
    t = load_translations().get(lang, load_translations()['zh'])
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            table_number = request.form.get('table_number')
            cart_json = request.form.get('cart_data')
            need_receipt = request.form.get('need_receipt') == 'on'
            
            if not cart_json or cart_json == '[]': return "Empty Cart"
            cart_items = json.loads(cart_json)
            
            total_price = 0
            display_list = []
            for item in cart_items:
                u_price = int(float(item['unit_price']))
                qty = int(float(item['qty']))
                opts = item.get('options', [])
                opts_str = f"({','.join(opts)})" if opts else ""
                display_list.append(f"{item['name']} {opts_str} x{qty}")
                total_price += (u_price * qty)
            
            items_str = " + ".join(display_list)
            
            # 產生流水號
            cur.execute("SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE")
            count_today = cur.fetchone()[0]
            new_seq = count_today + 1

            cur.execute(
                """INSERT INTO orders 
                   (table_number, items, total_price, lang, daily_seq, content_json, need_receipt) 
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (table_number, items_str, total_price, lang, new_seq, cart_json, need_receipt)
            )
            oid = cur.fetchone()[0]
            conn.commit()
            return redirect(url_for('order_success', order_id=oid))
        except Exception as e:
            conn.rollback()
            return f"Order Failed: {e}"
        finally:
            conn.close()

    # GET Menu
    cur.execute("SELECT * FROM products WHERE is_available=TRUE ORDER BY sort_order ASC, id ASC")
    products = cur.fetchall()
    conn.close()
    
    prod_list = []
    for p in products:
        # p: 0:id, 1:name, 2:price, 3:cat, 4:img, 6:opts
        display_name = p[1]
        display_opts = p[6]
        # 簡易多語言處理 (略，保持核心功能穩定)
        
        prod_list.append({
            'id': p[0], 'name': display_name, 'price': p[2], 'category': p[3], 
            'image_url': p[4] or "", 'custom_options': display_opts.split(',') if display_opts else [],
            'raw_category': p[3]
        })

    return render_frontend(prod_list, t)

def render_frontend(products_data, t):
    products_json = json.dumps(products_data)
    t_json = json.dumps(t)
    return f"""
    <!DOCTYPE html>
    <html><head><title>{t['title']}</title><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
    <style>
        body{{font-family:'Microsoft JhengHei',sans-serif;margin:0;padding-bottom:90px;background:#f4f7f6;}}
        .header{{background:white;padding:15px;position:sticky;top:0;z-index:100;box-shadow:0 2px 5px rgba(0,0,0,0.1);}}
        .menu-item{{background:white;border-radius:12px;padding:10px;display:flex;margin:10px;box-shadow:0 2px 5px rgba(0,0,0,0.05);}}
        .menu-info{{flex:1;padding-left:15px;display:flex;flex-direction:column;justify-content:space-between;}}
        .add-btn{{background:#28a745;color:white;border:none;padding:8px 15px;border-radius:20px;align-self:flex-end;}}
        .cart-bar{{position:fixed;bottom:0;width:100%;background:white;padding:15px;box-shadow:0 -2px 10px rgba(0,0,0,0.1);display:flex;justify-content:space-between;align-items:center;z-index:500;box-sizing:border-box;}}
        .modal-overlay{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:none;z-index:999;justify-content:center;align-items:flex-end;}}
        .modal-content{{background:white;width:100%;border-radius:20px 20px 0 0;padding:20px;max-height:80vh;overflow-y:auto;box-sizing:border-box;}}
        .option-tag{{display:inline-block;border:1px solid #ddd;padding:8px 15px;border-radius:20px;margin:5px;cursor:pointer;}}
        .option-tag.selected{{background:#e3f2fd;border-color:#2196f3;color:#2196f3;font-weight:bold;}}
    </style></head><body>
    <div class="header">
        <h3>{t['welcome']}</h3>
        <input type="text" id="visible_table" placeholder="{t['table_placeholder']}" style="padding:10px;width:100%;box-sizing:border-box;">
    </div>
    <div id="container"></div>
    <form method="POST" id="order-form">
        <input type="hidden" name="cart_data" id="cart_data_input">
        <input type="hidden" name="table_number" id="hidden_table">
        <div class="cart-bar" id="cart-bar" style="display:none;">
            <div onclick="openCartModal()" style="flex-grow:1;">
                <span id="total-qty" style="background:#e91e63;color:white;padding:2px 8px;border-radius:10px;">0</span> 
                <b>{t['total']}: $<span id="total-price">0</span></b>
            </div>
            <div>
                <label><input type="checkbox" name="need_receipt" checked> {t['print_receipt_opt']}</label>
                <button type="button" onclick="submitOrder()" style="background:#28a745;color:white;border:none;padding:10px 20px;border-radius:50px;">{t['checkout']}</button>
            </div>
        </div>
    </form>

    <div class="modal-overlay" id="option-modal"><div class="modal-content">
        <h3 id="m-title"></h3>
        <div style="color:#e91e63;font-weight:bold;margin-bottom:10px;">$<span id="m-price-display">0</span></div>
        <div id="m-opts"></div>
        <div style="margin-top:15px;display:flex;justify-content:center;align-items:center;">
            <button onclick="changeQ(-1)" style="width:40px;height:40px;border-radius:50%;border:1px solid #ccc;background:white;">-</button>
            <span id="m-qty" style="margin:0 20px;font-weight:bold;font-size:1.2em;">1</span>
            <button onclick="changeQ(1)" style="width:40px;height:40px;border-radius:50%;border:1px solid #ccc;background:white;">+</button>
        </div>
        <button onclick="addToCartConf()" style="width:100%;background:#28a745;color:white;padding:15px;border:none;border-radius:10px;margin-top:20px;">{t['modal_add_cart']}</button>
        <button onclick="closeM()" style="width:100%;background:white;color:#666;padding:10px;border:none;margin-top:5px;">{t['modal_cancel']}</button>
    </div></div>

    <div class="modal-overlay" id="cart-modal"><div class="modal-content">
        <h3>{t['cart_title']}</h3>
        <div id="c-list"></div>
        <button onclick="document.getElementById('cart-modal').style.display='none'" style="width:100%;padding:15px;margin-top:10px;">{t['close']}</button>
    </div></div>

    <script>
        const prods={products_json}, t={t_json};
        let cart=[], curP=null, curQ=1, curOpts=[], curAddP=0;
        
        // 解析價格正則
        function parseOption(str) {{
            const m = str.match(/[:：+\s]+(\d+)$/);
            if(m) return {{name: str.replace(m[0],'').trim(), price: parseInt(m[1]), origin: str}};
            return {{name: str, price: 0, origin: str}};
        }}

        const c=document.getElementById('container');
        let cat="";
        prods.forEach(p=>{{
            if(p.category!==cat) {{c.innerHTML+=`<div style="padding:15px 10px 5px;color:#666;font-weight:bold;">${{p.category}}</div>`; cat=p.category;}}
            c.innerHTML+=`<div class="menu-item"><div class="menu-info"><div><b>${{p.name}}</b><br><span style="color:#e91e63">$${{p.price}}</span></div><button class="add-btn" onclick="openOpt(${{p.id}})">${{t.add}}</button></div></div>`;
        }});

        function openOpt(id){{
            curP=prods.find(p=>p.id===id); curQ=1; curOpts=[]; curAddP=0;
            document.getElementById('m-title').innerText=curP.name;
            const area=document.getElementById('m-opts'); area.innerHTML='';
            updateModalTotal();
            curP.custom_options.forEach(o=>{{
                if(!o.trim()) return;
                const p = parseOption(o);
                let el=document.createElement('div'); el.className='option-tag';
                el.innerText = p.name + (p.price>0?` (+$${{p.price}})`:'');
                el.onclick=()=>{{
                    if(curOpts.includes(o)) {{ curOpts=curOpts.filter(x=>x!==o); curAddP-=p.price; el.classList.remove('selected'); }}
                    else {{ curOpts.push(o); curAddP+=p.price; el.classList.add('selected'); }}
                    updateModalTotal();
                }};
                area.appendChild(el);
            }});
            document.getElementById('m-qty').innerText=1;
            document.getElementById('option-modal').style.display='flex';
        }}
        function updateModalTotal() {{ document.getElementById('m-price-display').innerText = (curP.price+curAddP)*curQ; }}
        function changeQ(n) {{ if(curQ+n>=1) {{ curQ+=n; document.getElementById('m-qty').innerText=curQ; updateModalTotal(); }} }}
        function closeM() {{ document.getElementById('option-modal').style.display='none'; }}
        function addToCartConf() {{
            cart.push({{id:curP.id, name:curP.name, unit_price:curP.price+curAddP, qty:curQ, options:[...curOpts], category:curP.raw_category}});
            closeM(); updateBar();
        }}
        function updateBar() {{
            if(cart.length>0) {{
                document.getElementById('cart-bar').style.display='flex';
                let tot=cart.reduce((a,b)=>a+b.unit_price*b.qty,0);
                document.getElementById('total-price').innerText=tot;
                document.getElementById('total-qty').innerText=cart.reduce((a,b)=>a+b.qty,0);
            }} else document.getElementById('cart-bar').style.display='none';
        }}
        function openCartModal() {{
            let h='';
            cart.forEach((i,idx)=>{{
                let optD = i.options.map(o=>parseOption(o).name).join(', ');
                h+=`<div style="border-bottom:1px solid #eee;padding:10px;"><b>${{i.name}}</b> x${{i.qty}}<br><small>${{optD}}</small><button onclick="cart.splice(${{idx}},1);openCartModal();updateBar()" style="float:right;color:red;border:none;background:none;">🗑️</button></div>`;
            }});
            document.getElementById('c-list').innerHTML=h||t.empty_cart;
            document.getElementById('cart-modal').style.display='flex';
        }}
        function submitOrder() {{
            let tbl=document.getElementById('visible_table').value;
            if(!tbl) {{alert(t.table_placeholder);return;}}
            document.getElementById('hidden_table').value=tbl;
            document.getElementById('cart_data_input').value=JSON.stringify(cart);
            let tot=cart.reduce((a,b)=>a+b.unit_price*b.qty,0);
            if(confirm(t.confirm_order+`\\n${{t.total}}: $${{tot}}`)) document.getElementById('order-form').submit();
        }}
    </script></body></html>
    """

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
    <div style="text-align:center;padding:50px;font-family:sans-serif;">
        <h1 style="color:green;font-size:50px;">✅</h1>
        <h2>下單成功！</h2>
        <div style="font-size:3em;font-weight:bold;color:#e91e63;margin:20px;">{seq}</div>
        <p>單號 #{seq}</p>
        <a href="/" style="background:#007bff;color:white;padding:10px 30px;border-radius:20px;text-decoration:none;">回到首頁</a>
    </div>
    """

# --- 5. 廚房看板 ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date AND status != 'Cancelled' ORDER BY daily_seq DESC")
    orders = cur.fetchall()
    conn.close()
    
    html = """<!DOCTYPE html><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body{background:#222;color:white;font-family:sans-serif;padding:10px;} .card{background:#333;margin-bottom:15px;padding:15px;border-left:5px solid #ff9800;border-radius:5px;} .done{border-color:#28a745;opacity:0.6;} .btn{padding:5px 10px;margin-right:5px;text-decoration:none;color:white;border-radius:3px;font-size:0.9em;display:inline-block;}</style>
    <h2>👨‍🍳 廚房接單</h2>"""
    
    for o in orders:
        seq = f"{o[7]:03d}" if o[7] else "---"
        st = "done" if o[4]=='Completed' else ""
        items = o[2].replace(" + ", "<br>")
        btns = ""
        if o[4]!='Completed': btns += f"<a href='/kitchen/do/{o[0]}' class='btn' style='background:#28a745'>完成</a>"
        btns += f"""
            <a href='/print_order/{o[0]}' target='_blank' class='btn' style='background:#17a2b8'>🖨️</a>
            <a href='/order/edit/{o[0]}' class='btn' style='background:#ffc107;color:black;'>✏️</a>
            <a href='/order/del/{o[0]}' class='btn' style='background:#dc3545' onclick="return confirm('Del?')">🗑️</a>
        """
        html += f"<div class='card {st}'><span style='font-size:1.5em;color:#ff9800;'>#{seq}</span> 桌: {o[1]}<br><div style='margin:10px 0;font-size:1.2em;'>{items}</div><div style='border-top:1px solid #555;padding-top:10px;'>{btns}</div></div>"
    return html

@app.route('/kitchen/do/<int:oid>')
def kitchen_do(oid):
    c=get_db_connection();c.cursor().execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,));c.commit();c.close()
    return redirect('/kitchen')
@app.route('/order/del/<int:oid>')
def order_del(oid):
    c=get_db_connection();c.cursor().execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,));c.commit();c.close()
    return redirect('/kitchen')

# --- 6. 編輯訂單 (恢復功能) ---
@app.route('/order/edit/<int:oid>', methods=['GET','POST'])
def edit_order(oid):
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        # 重算邏輯
        try:
            cur.execute("SELECT content_json FROM orders WHERE id=%s", (oid,))
            orig = json.loads(cur.fetchone()[0])
            new_items, total, display = [], 0, []
            
            raw_indices = request.form.getlist('idx')
            for i in raw_indices:
                idx = int(i)
                qty = int(request.form.get(f'qty_{idx}', 0))
                if qty > 0:
                    item = orig[idx]
                    item['qty'] = qty
                    total += int(item['unit_price']) * qty
                    opts = f"({','.join(item['options'])})" if item['options'] else ""
                    display.append(f"{item['name']} {opts} x{qty}")
                    new_items.append(item)
            
            cur.execute("UPDATE orders SET content_json=%s, items=%s, total_price=%s WHERE id=%s", 
                        (json.dumps(new_items), " + ".join(display), total, oid))
            conn.commit()
            return redirect('/kitchen')
        except Exception as e:
            return f"Edit Error: {e}"
        finally:
            conn.close()

    cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
    o = cur.fetchone()
    conn.close()
    if not o[8]: return "無法編輯舊資料"
    items = json.loads(o[8])
    
    html = f"<h2>編輯訂單 #{o[7]:03d}</h2><form method='POST'>"
    for i, item in enumerate(items):
        html += f"<div><b>{item['name']}</b> ${item['unit_price']} <input type='number' name='qty_{i}' value='{item['qty']}' style='width:50px;'><input type='hidden' name='idx' value='{i}'></div><hr>"
    return html + "<button style='width:100%;padding:10px;background:#28a745;color:white;'>儲存</button></form>"

# --- 7. 列印功能 (恢復分類功能) ---
@app.route('/print_order/<int:oid>')
def print_order(oid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
    o = cur.fetchone()
    conn.close()
    
    if not o or not o[8]: return "No Data"
    items = json.loads(o[8])
    seq, table, time_str = f"{o[7]:03d}", o[1], o[5].strftime('%Y-%m-%d %H:%M')
    
    # 分類
    noodles, soups = [], []
    for i in items:
        cat = i.get('category', '')
        if '主食' in cat or 'Main' in cat or '麵' in cat: noodles.append(i)
        else: soups.append(i)

    def ticket(title, lst, price=False):
        if not lst and not price: return ""
        h = f"<div class='t'><center><h2>{title}</h2><h1>#{seq}</h1>Table: {table}<br>{time_str}</center><hr>"
        tot = 0
        for x in lst:
            opt = f"<br><small>({','.join(x['options'])})</small>" if x['options'] else ""
            pr = f"${x['unit_price']*x['qty']}" if price else ""
            h += f"<div style='display:flex;margin-bottom:5px;'><b style='width:25px;'>{x['qty']}</b><span style='flex:1;'>{x['name']}{opt}</span><span>{pr}</span></div>"
            tot += x['unit_price']*x['qty']
        if price: h += f"<hr><h3 style='text-align:right'>Total: ${tot}</h3>"
        return h + "</div><div class='pb'></div>"

    body = ""
    if o[9]: body += ticket("Receipt", items, True) # 結帳單
    if noodles: body += ticket("🍜 麵區", noodles)
    if soups: body += ticket("🍲 湯/小菜", soups)

    return f"""
    <html><head><style>
    body{{font-family:'Courier New';background:#eee;}} .t{{width:58mm;background:white;padding:10px;margin:10px auto;}} 
    @media print{{ body{{background:white;}} .t{{box-shadow:none;margin:0;width:100%;}} .pb{{page-break-after:always;}} }}
    </style></head><body onload="window.print()">{body}</body></html>
    """

# --- 8. 防休眠 ---
def keep_alive():
    while True:
        try: urllib.request.urlopen("http://127.0.0.1:10000/")
        except: pass
        time.sleep(800)
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
