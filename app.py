import os
import psycopg2
import json
import threading
import urllib.request
import time
from flask import Flask, request, redirect, url_for
from datetime import datetime, date

app = Flask(__name__)

# --- 資料庫連線 ---
def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 翻譯字典 ---
def load_translations():
    return {
        "zh": {
            "title": "線上點餐", "welcome": "歡迎點餐", "table_placeholder": "請輸入桌號", 
            "table_label": "桌號", "add": "加入", "sold_out": "已售完", "cart_detail": "查看明細", 
            "total": "合計", "checkout": "去結帳", "cart_title": "購物車明細", "empty_cart": "購物車是空的", 
            "close": "關閉", "confirm_delete": "確定刪除？", "confirm_order": "確定送出訂單？", 
            "modal_unit_price": "單價", "modal_add_cart": "加入購物車", "modal_cancel": "取消", 
            "custom_options": "客製化選項", "order_success": "下單成功！", "kitchen_prep": "廚房備餐中", 
            "pay_at_counter": "請至櫃檯結帳", "order_details": "訂單明細", 
            "print_receipt_opt": "列印收據", "daily_seq_prefix": "單號"
        },
        "en": {
            "title": "Order", "welcome": "Welcome", "table_placeholder": "Table No.",
            "table_label": "Table", "add": "Add", "sold_out": "Sold Out", "cart_detail": "Cart",
            "total": "Total", "checkout": "Checkout", "cart_title": "Cart", "empty_cart": "Empty",
            "close": "Close", "confirm_delete": "Remove?", "confirm_order": "Submit?",
            "modal_unit_price": "Price", "modal_add_cart": "Add to Cart", "modal_cancel": "Cancel",
            "custom_options": "Options", "order_success": "Success!", "kitchen_prep": "Preparing...",
            "pay_at_counter": "Please pay at counter", "order_details": "Order Details",
            "print_receipt_opt": "Print Receipt", "daily_seq_prefix": "No."
        },
        "jp": {
            "title": "注文", "welcome": "ようこそ", "table_placeholder": "卓番",
            "table_label": "卓番", "add": "追加", "sold_out": "完売", "cart_detail": "カート",
            "total": "合計", "checkout": "会計", "cart_title": "詳細", "empty_cart": "空です",
            "close": "閉じる", "confirm_delete": "削除？", "confirm_order": "送信？",
            "modal_unit_price": "単価", "modal_add_cart": "カートへ", "modal_cancel": "キャンセル",
            "custom_options": "オプション", "order_success": "送信完了", "kitchen_prep": "調理中...",
            "pay_at_counter": "レジでお会計ください", "order_details": "注文詳細",
            "print_receipt_opt": "レシート印刷", "daily_seq_prefix": "番号"
        }
    }

# --- 1. 資料庫初始化 (結構更新) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    conn.autocommit = True
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
                daily_seq INTEGER DEFAULT 0,
                content_json TEXT,
                need_receipt BOOLEAN DEFAULT FALSE,
                lang VARCHAR(10) DEFAULT 'zh'
            );
        ''')
        # 確保所有欄位存在
        alters = [
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_available BOOLEAN DEFAULT TRUE;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_en VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_jp VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_en TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_jp TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS daily_seq INTEGER DEFAULT 0;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS need_receipt BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';"
        ]
        for cmd in alters:
            try: cur.execute(cmd)
            except: pass
            
        return "資料庫結構檢查完成 (不會刪除資料)。<a href='/'>回首頁</a> | <a href='/admin'>回後台</a>"
    except Exception as e:
        return f"DB Error: {e}"
    finally:
        cur.close(); conn.close()

# --- 2. 首頁與語言選擇 (修復 404) ---
@app.route('/')
def language_select():
    tbl = request.args.get('table', '')
    # 修復：正確的 Query String 拼接
    base_qs = f"&table={tbl}" if tbl else ""
    
    return f"""
    <!DOCTYPE html>
    <html><head><title>Language</title><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body{{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;background:#f4f7f6;}}
    .btn{{width:200px;padding:15px;margin:10px;text-align:center;text-decoration:none;font-size:1.2em;border-radius:50px;color:white;box-shadow:0 4px 6px rgba(0,0,0,0.1);}}
    .zh{{background:#e91e63;}} .en{{background:#007bff;}} .jp{{background:#ff9800;}}</style></head>
    <body><h2>Select Language</h2>
    <a href="/menu?lang=zh{base_qs}" class="btn zh">中文</a>
    <a href="/menu?lang=en{base_qs}" class="btn en">English</a>
    <a href="/menu?lang=jp{base_qs}" class="btn jp">日本語</a>
    </body></html>
    """

# --- 3. 點餐頁面 (支援編輯帶入) ---
@app.route('/menu', methods=['GET', 'POST'])
def menu():
    lang = request.args.get('lang', 'zh')
    t = load_translations().get(lang, load_translations()['zh'])
    conn = get_db_connection()
    cur = conn.cursor()

    # --- 提交訂單 (POST) ---
    if request.method == 'POST':
        try:
            table_number = request.form.get('table_number')
            cart_json = request.form.get('cart_data')
            need_receipt = request.form.get('need_receipt') == 'on'
            lang_post = request.form.get('lang_input', 'zh')
            old_order_id = request.form.get('old_order_id') # 取得舊單 ID
            
            if not cart_json or cart_json == '[]': return "Empty Cart"
            
            cart_items = json.loads(cart_json)
            total_price = 0
            display_list = []
            
            for item in cart_items:
                price = int(float(item['unit_price']))
                qty = int(float(item['qty']))
                total_price += (price * qty)
                opts = item.get('options', [])
                opt_str = f"({','.join(opts)})" if opts else ""
                display_list.append(f"{item['name']} {opt_str} x{qty}")

            items_str = " + ".join(display_list)
            
            # 每日流水號
            cur.execute("SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE")
            new_seq = cur.fetchone()[0] + 1
            
            # 1. 建立新單
            cur.execute("""
                INSERT INTO orders (table_number, items, total_price, lang, daily_seq, content_json, need_receipt)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (table_number, items_str, total_price, lang_post, new_seq, cart_json, need_receipt))
            oid = cur.fetchone()[0]
            
            # 2. 如果是編輯模式 (有 old_order_id)，作廢舊單
            if old_order_id:
                cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (old_order_id,))
            
            conn.commit()
            return redirect(url_for('order_success', order_id=oid, lang=lang_post))
            
        except Exception as e:
            conn.rollback()
            return f"Order Failed: {e}"
        finally:
            cur.close(); conn.close()

    # --- 顯示菜單 (GET) ---
    url_table = request.args.get('table', '')
    edit_oid = request.args.get('edit_oid') # 檢查是否為編輯模式
    preload_cart = "[]"
    
    # 如果是編輯模式，撈取舊資料
    if edit_oid:
        cur.execute("SELECT table_number, content_json FROM orders WHERE id=%s", (edit_oid,))
        old_data = cur.fetchone()
        if old_data:
            if not url_table: url_table = old_data[0] # 帶入舊桌號
            preload_cart = old_data[1] # 帶入購物車 JSON

    # 只撈取上架商品 (is_available=TRUE)
    cur.execute("SELECT * FROM products WHERE is_available=TRUE ORDER BY sort_order ASC, id ASC")
    products = cur.fetchall()
    cur.close(); conn.close()
    
    p_list = []
    for p in products:
        d_name = p[1]
        d_opts = p[6]
        if lang == 'en':
            if p[8]: d_name = p[8]
            if len(p)>10 and p[10]: d_opts = p[10]
        elif lang == 'jp':
            if p[9]: d_name = p[9]
            if len(p)>11 and p[11]: d_opts = p[11]

        p_list.append({
            'id': p[0], 'name': d_name, 'price': p[2], 'category': p[3],
            'image_url': p[4] if p[4] else '', 
            'custom_options': d_opts.split(',') if d_opts else [],
            'raw_category': p[3]
        })

    return render_frontend(p_list, t, url_table, lang, preload_cart, edit_oid)

def render_frontend(products, t, default_table, lang, preload_cart, edit_oid):
    p_json = json.dumps(products)
    t_json = json.dumps(t)
    old_oid_input = f'<input type="hidden" name="old_order_id" value="{edit_oid}">' if edit_oid else ''
    edit_notice = f'<div style="background:#fff3cd;padding:10px;color:#856404;text-align:center;">⚠️ 正在編輯 #{edit_oid} 號訂單 (送出後舊單將作廢)</div>' if edit_oid else ''
    
    return f"""
    <!DOCTYPE html>
    <html><head><title>{t['title']}</title><meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=0">
    <style>
        body{{font-family:'Microsoft JhengHei',sans-serif;margin:0;padding-bottom:100px;background:#f8f9fa;}}
        .header{{background:white;padding:15px;position:sticky;top:0;z-index:99;box-shadow:0 2px 5px rgba(0,0,0,0.1);}}
        .menu-item{{background:white;margin:10px;padding:10px;border-radius:10px;display:flex;box-shadow:0 2px 4px rgba(0,0,0,0.05);}}
        .menu-img{{width:80px;height:80px;border-radius:8px;object-fit:cover;background:#eee;}}
        .menu-info{{flex:1;padding-left:15px;display:flex;flex-direction:column;justify-content:space-between;}}
        .add-btn{{background:#28a745;color:white;border:none;padding:5px 15px;border-radius:15px;align-self:flex-end;}}
        .cart-bar{{position:fixed;bottom:0;width:100%;background:white;padding:15px;box-shadow:0 -2px 10px rgba(0,0,0,0.1);display:none;justify-content:space-between;align-items:center;box-sizing:border-box;z-index:100;}}
        .modal{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:none;z-index:200;justify-content:center;align-items:flex-end;}}
        .modal-c{{background:white;width:100%;padding:20px;border-radius:20px 20px 0 0;max-height:80vh;overflow-y:auto;}}
        .opt-tag{{border:1px solid #ddd;padding:5px 10px;border-radius:15px;margin:3px;display:inline-block;cursor:pointer;}}
        .opt-tag.sel{{background:#e3f2fd;border-color:#2196f3;color:#2196f3;}}
    </style></head><body>
    <div class="header">
        {edit_notice}
        <h3>{t['welcome']}</h3>
        <input type="text" id="visible_table" value="{default_table}" placeholder="{t['table_placeholder']}" style="padding:10px;width:100%;box-sizing:border-box;border:1px solid #ddd;border-radius:5px;">
    </div>
    <div id="list"></div>
    
    <form id="order-form" method="POST" action="/menu">
        <input type="hidden" name="cart_data" id="cart_input">
        <input type="hidden" name="table_number" id="tbl_input">
        <input type="hidden" name="lang_input" value="{lang}">
        {old_oid_input}
        
        <div class="cart-bar" id="bar">
            <div onclick="showCart()" style="flex-grow:1;">Total: $<span id="tot">0</span> (<span id="cnt">0</span>)</div>
            <label style="margin-right:10px;"><input type="checkbox" name="need_receipt" checked> {t['print_receipt_opt']}</label>
            <button type="button" onclick="sub()" style="background:#28a745;color:white;border:none;padding:10px 20px;border-radius:20px;">{t['checkout']}</button>
        </div>
    </form>
    
    <div class="modal" id="opt-m"><div class="modal-c">
        <h3 id="m-name"></h3>
        <div id="m-opts"></div>
        <div style="margin-top:20px;text-align:center;">
            <button onclick="cq(-1)">-</button> <span id="m-q" style="margin:0 15px;font-weight:bold;">1</span> <button onclick="cq(1)">+</button>
        </div>
        <button onclick="addC()" style="width:100%;background:#28a745;color:white;padding:12px;border:none;border-radius:10px;margin-top:20px;">{t['modal_add_cart']}</button>
        <button onclick="document.getElementById('opt-m').style.display='none'" style="width:100%;background:white;padding:10px;border:none;margin-top:10px;">{t['modal_cancel']}</button>
    </div></div>

    <div class="modal" id="cart-m"><div class="modal-c">
        <h3>{t['cart_title']}</h3><div id="c-list"></div>
        <button onclick="document.getElementById('cart-m').style.display='none'" style="width:100%;padding:10px;margin-top:10px;">{t['close']}</button>
    </div></div>

    <script>
    const P={p_json}, T={t_json}, PRELOAD={preload_cart};
    let C=[], cur=null, q=1, opts=[], addP=0;

    // Load Preload Data (如果有的話)
    if(PRELOAD && PRELOAD.length > 0){{
        C = PRELOAD;
        setTimeout(upd, 100);
    }}
    
    // Render
    let h="", cat="";
    P.forEach(p=>{{
        if(p.category!=cat) {{ h+=`<div style='padding:10px;font-weight:bold;color:#666'>${{p.category}}</div>`; cat=p.category; }}
        let img = p.image_url ? `<img src="${{p.image_url}}" class="menu-img">` : '';
        h+=`<div class="menu-item">
            ${{img}}
            <div class="menu-info">
                <div><b>${{p.name}}</b><div style="color:#e91e63">$${{p.price}}</div></div>
                <button class="add-btn" onclick="openOpt(${{p.id}})">${{T.add}}</button>
            </div>
        </div>`;
    }});
    document.getElementById('list').innerHTML=h;

    function parseOpt(s){{
        let m = s.match(/[:：+\s]+(\d+)$/);
        return m ? {{n:s.replace(m[0],'').trim(), p:parseInt(m[1])}} : {{n:s, p:0}};
    }}

    function openOpt(id){{
        cur=P.find(x=>x.id==id); q=1; opts=[]; addP=0;
        document.getElementById('m-name').innerText=cur.name;
        let area=document.getElementById('m-opts'); area.innerHTML="";
        cur.custom_options.forEach(o=>{{
            let parsed = parseOpt(o);
            let d = document.createElement('div'); d.className='opt-tag';
            d.innerText = parsed.n + (parsed.p?` (+$${{parsed.p}})`:'');
            d.onclick=()=>{{
                if(opts.includes(o)){{ opts=opts.filter(x=>x!=o); addP-=parsed.p; d.classList.remove('sel'); }}
                else{{ opts.push(o); addP+=parsed.p; d.classList.add('sel'); }}
            }};
            area.appendChild(d);
        }});
        document.getElementById('m-q').innerText=1;
        document.getElementById('opt-m').style.display='flex';
    }}
    function cq(n){{ if(q+n>0) {{q+=n; document.getElementById('m-q').innerText=q;}} }}
    function addC(){{
        C.push({{id:cur.id, name:cur.name, unit_price:cur.price+addP, qty:q, options:[...opts], category:cur.raw_category}});
        document.getElementById('opt-m').style.display='none'; upd();
    }}
    function upd(){{
        if(C.length){{
            document.getElementById('bar').style.display='flex';
            document.getElementById('tot').innerText = C.reduce((a,b)=>a+b.unit_price*b.qty,0);
            document.getElementById('cnt').innerText = C.reduce((a,b)=>a+b.qty,0);
        }} else document.getElementById('bar').style.display='none';
    }}
    function showCart(){{
        let h="";
        C.forEach((i,x)=>{{
            let op = i.options.map(o=>parseOpt(o).n).join(',');
            h+=`<div style="border-bottom:1px solid #eee;padding:10px;display:flex;justify-content:space-between;">
                <div><b>${{i.name}}</b> x${{i.qty}}<br><small>${{op}}</small></div>
                <button onclick="C.splice(${{x}},1);upd();showCart()" style="color:red;border:none;background:none;">🗑️</button>
            </div>`;
        }});
        document.getElementById('c-list').innerHTML=h;
        document.getElementById('cart-m').style.display='flex';
    }}
    function sub(){{
        let t = document.getElementById('visible_table').value;
        if(!t) return alert(T.table_placeholder);
        document.getElementById('tbl_input').value=t;
        document.getElementById('cart_input').value=JSON.stringify(C);
        if(confirm(T.confirm_order)) document.getElementById('order-form').submit();
    }}
    </script></body></html>
    """

# --- 4. 下單成功 ---
@app.route('/order_success')
def order_success():
    oid = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    t = load_translations().get(lang, load_translations()['zh'])
    
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT daily_seq, content_json, total_price FROM orders WHERE id=%s", (oid,))
    row = cur.fetchone()
    conn.close()

    if not row: return "Order Not Found"
    seq, json_str, total = row
    items = json.loads(json_str) if json_str else []
    
    items_html = ""
    for i in items:
        opt = f" <small>({','.join(i['options'])})</small>" if i['options'] else ""
        items_html += f"<div style='display:flex;justify-content:space-between;border-bottom:1px dashed #ddd;padding:5px;'><span>{i['name']} x{i['qty']}{opt}</span><span>${i['unit_price']*i['qty']}</span></div>"

    return f"""
    <div style="max-width:400px;margin:20px auto;text-align:center;font-family:sans-serif;padding:20px;border:1px solid #ddd;border-radius:10px;">
        <h1 style="color:#28a745;">✅ {t['order_success']}</h1>
        <div style="font-size:3em;font-weight:bold;color:#e91e63;margin:10px;">#{seq:03d}</div>
        <p>{t['kitchen_prep']}</p>
        <h2 style="background:#eee;padding:10px;">{t['pay_at_counter']}</h2>
        
        <div style="text-align:left;margin-top:20px;">
            <h3>🧾 {t['order_details']}</h3>
            {items_html}
            <div style="text-align:right;font-weight:bold;font-size:1.2em;margin-top:10px;">{t['total']}: ${total}</div>
        </div>
        <br>
        <a href="/" style="display:block;padding:10px;background:#007bff;color:white;text-decoration:none;border-radius:5px;">Back to Home</a>
    </div>
    """

# --- 5. 廚房看板 ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE created_at >= CURRENT_DATE ORDER BY daily_seq DESC")
    orders = cur.fetchall()
    conn.close()

    html = """
    <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{background:#222;color:white;font-family:sans-serif;padding:10px;}
        .card{background:#333;margin-bottom:15px;padding:15px;border-radius:5px;border-left:5px solid #ff9800;position:relative;}
        .completed{border-left-color:#28a745;opacity:0.6;} 
        .cancelled{border-left-color:#dc3545;background:#442222; opacity:0.8;}
        .cancelled .items{text-decoration:line-through;color:#aaa;}
        .tag{position:absolute;top:10px;right:10px;padding:5px;border-radius:3px;font-weight:bold;}
        .btn{padding:5px 10px;margin:5px 2px;text-decoration:none;color:white;border-radius:3px;display:inline-block;cursor:pointer;border:none;font-size:0.9em;}
    </style></head><body>
    <div style="display:flex;justify-content:space-between;align-items:center;">
        <h2>👨‍🍳 廚房接單</h2>
        <a href="/kitchen/report" class="btn" style="background:#6f42c1;font-size:1.1em;">📊 查看日結</a>
    </div>
    """
    
    for o in orders:
        status = o[4]
        cls = status.lower()
        seq = f"{o[7]:03d}"
        
        tag = ""
        if status == 'Cancelled': tag = "<span style='background:red;color:white;'>已作廢</span>"
        elif status == 'Completed': tag = "<span style='background:green;color:white;'>已完成</span>"

        btns = ""
        if status == 'Pending':
            btns += f"<a href='/kitchen/complete/{o[0]}' class='btn' style='background:#28a745'>完成</a>"
        
        if status != 'Cancelled':
            # 編輯邏輯改變：連結到 /menu 並帶入 edit_oid
            btns += f"""
            <a href='/menu?edit_oid={o[0]}' class='btn' style='background:#ffc107;color:black;'>✏️ 編輯重開</a>
            <a href='/order/cancel/{o[0]}' class='btn' style='background:#dc3545' onclick=\"return confirm('確定作廢？(不計營收)')\">🗑️ 作廢</a>
            """
        
        btns += f"<a href='/print_order/{o[0]}' target='_blank' class='btn' style='background:#17a2b8'>🖨️ 列印</a>"

        html += f"""
        <div class="card {cls}">
            <div class="tag">{tag}</div>
            <span style="font-size:1.5em;color:#ff9800;">#{seq}</span> 桌號: {o[1]} <small>({o[5].strftime('%H:%M')})</small>
            <div class="items" style="margin:10px 0;font-size:1.2em;">{o[2].replace(" + ", "<br>")}</div>
            <div style="border-top:1px solid #555;padding-top:10px;">{btns}</div>
        </div>
        """
    return html

@app.route('/kitchen/report')
def daily_report():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE created_at >= CURRENT_DATE AND status != 'Cancelled'")
    valid_count, valid_total = cur.fetchone()
    cur.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE created_at >= CURRENT_DATE AND status = 'Cancelled'")
    void_count, void_total = cur.fetchone()
    conn.close()
    
    return f"""
    <!DOCTYPE html><body style="font-family:sans-serif;padding:20px;background:#f4f4f4;">
        <div style="background:white;padding:30px;max-width:500px;margin:0 auto;border-radius:10px;">
            <h2 style="text-align:center;">📅 本日結帳單</h2><hr>
            <h3>✅ 有效營收</h3>
            <p>單量: {valid_count or 0} | 金額: <span style="font-size:1.5em;color:green;font-weight:bold">${valid_total or 0}</span></p>
            <hr><h3 style="color:red;">❌ 作廢/刪除</h3>
            <p>單量: {void_count or 0} | 金額: ${void_total or 0}</p>
            <hr><button onclick="window.print()" style="width:100%;padding:10px;">列印</button>
            <br><br><a href="/kitchen" style="display:block;text-align:center;">回廚房</a>
        </div>
    </body>
    """

# --- 6. 狀態變更邏輯 ---
@app.route('/kitchen/complete/<int:oid>')
def complete_order(oid):
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

@app.route('/order/cancel/<int:oid>')
def cancel_order(oid):
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

# --- 7. 列印 ---
@app.route('/print_order/<int:oid>')
def print_order(oid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
    o = cur.fetchone()
    conn.close()
    if not o: return "No Data"
    
    seq = f"{o[7]:03d}"
    items = json.loads(o[8]) if o[8] else []
    status = o[4]
    is_void = (status == 'Cancelled')
    
    title = "❌ 作廢單 (VOID)" if is_void else "結帳單 (Receipt)"
    style = "text-decoration: line-through; color:red;" if is_void else ""
    
    def mk_ticket(t_name, item_list, show_total=False):
        if not item_list and not show_total: return ""
        h = f"<div class='ticket' style='{style}'><div class='head'><h2>{t_name}</h2><h1>#{seq}</h1><p>Table: {o[1]}</p></div><hr>"
        t_price = 0
        for i in item_list:
            t_price += i['unit_price']*i['qty']
            h += f"<div class='row'><span>{i['qty']} x {i['name']}</span><span>${i['unit_price']*i['qty']}</span></div>"
            if i['options']: h+=f"<div class='opt'>({','.join(i['options'])})</div>"
        if show_total: h += f"<hr><div style='text-align:right;font-size:1.2em;'>Total: ${t_price}</div>"
        h += "</div><div class='break'></div>"
        return h

    body = ""
    body += mk_ticket(title, items, show_total=True)
    if not is_void:
        noodles = [i for i in items if '主食' in i.get('category','') or 'Main' in i.get('category','')]
        others = [i for i in items if i not in noodles]
        body += mk_ticket("🍜 麵區工單", noodles)
        body += mk_ticket("🍲 綜合工單", others)

    return f"<html><head><style>body{{font-family:'Courier New';font-size:14px;background:#eee;}} .ticket{{width:58mm;background:white;margin:10px auto;padding:10px;}} .head{{text-align:center;}} .row{{display:flex;justify-content:space-between;margin-top:5px;font-weight:bold;}} .opt{{font-size:12px;color:#555;margin-left:10px;}} .break{{page-break-after:always;}} @media print{{.ticket{{width:100%;box-shadow:none;}}}}</style></head><body onload='window.print()'>{body}</body></html>"

# --- 8. 後台管理 (支援上下架、清空資料) ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    conn = get_db_connection(); cur = conn.cursor()
    if request.method == 'POST':
        cur.execute("""
            INSERT INTO products (name, price, category, image_url, custom_options, name_en, name_jp, custom_options_en, custom_options_jp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            request.form['name'], request.form['price'], request.form['category'], request.form['image_url'], request.form['custom_options'],
            request.form.get('name_en'), request.form.get('name_jp'), request.form.get('custom_options_en'), request.form.get('custom_options_jp')
        ))
        conn.commit()
        return redirect('/admin')
    
    cur.execute("SELECT * FROM products ORDER BY id DESC")
    prods = cur.fetchall()
    conn.close()
    
    rows = ""
    for p in prods:
        # p[5] is is_available
        status_text = "<span style='color:green'>上架中</span>" if p[5] else "<span style='color:red'>已下架</span>"
        toggle_btn = f"<a href='/admin/toggle_product/{p[0]}' class='button button-outline' style='padding:0 10px;'>切換狀態</a>"
        rows += f"<tr><td>{p[0]}</td><td><img src='{p[4]}' height='50'></td><td>{p[1]}</td><td>{p[2]}</td><td>{status_text}<br>{toggle_btn}</td><td><a href='/admin/edit_product/{p[0]}'>編輯</a> | <a href='/admin/delete_product/{p[0]}' onclick='return confirm(\"確定永久刪除？\")'>刪除</a></td></tr>"

    return f"""
    <!DOCTYPE html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css"></head>
    <body style="padding:20px;">
    <div style="display:flex;justify-content:space-between;">
        <h1>🔧 後台管理</h1>
        <a href="/admin/reset_orders" onclick="return confirm('⚠️ 確定清空所有訂單記錄？此動作無法復原！')" class="button" style="background:red;border-color:red;">⚠️ 清空所有訂單</a>
    </div>
    <div style="background:#f4f4f4;padding:20px;">
        <form method="POST">
            <div class="row"><div class="column"><label>名稱</label><input type="text" name="name" required><label>Name(EN)</label><input type="text" name="name_en"><label>名前(JP)</label><input type="text" name="name_jp"></div>
            <div class="column"><label>價格</label><input type="number" name="price" required><label>分類</label><input type="text" name="category" required><label>圖片URL</label><input type="text" name="image_url"></div></div>
            <label>選項 (例: 加麵:+10)</label><input type="text" name="custom_options">
            <button type="submit">新增</button>
        </form>
    </div><hr><table><thead><tr><th>ID</th><th>圖</th><th>品名</th><th>價</th><th>狀態</th><th>操作</th></tr></thead><tbody>{rows}</tbody></table></body>
    """

# 切換上架/下架
@app.route('/admin/toggle_product/<int:pid>')
def toggle_product(pid):
    c=get_db_connection(); c.cursor().execute("UPDATE products SET is_available = NOT is_available WHERE id=%s", (pid,)); c.commit(); c.close()
    return redirect('/admin')

# 永久刪除
@app.route('/admin/delete_product/<int:pid>')
def delete_product(pid):
    c=get_db_connection(); c.cursor().execute("DELETE FROM products WHERE id=%s",(pid,)); c.commit(); c.close()
    return redirect('/admin')

# 清空訂單
@app.route('/admin/reset_orders')
def reset_orders():
    c=get_db_connection(); c.cursor().execute("DELETE FROM orders"); c.commit(); c.close()
    return "已清空所有訂單。<a href='/admin'>回後台</a>"

@app.route('/admin/edit_product/<int:pid>', methods=['GET','POST'])
def edit_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    if request.method=='POST':
        cur.execute("""
            UPDATE products SET name=%s, price=%s, category=%s, image_url=%s, custom_options=%s,
            name_en=%s, name_jp=%s, custom_options_en=%s, custom_options_jp=%s WHERE id=%s
        """, (
            request.form['name'], request.form['price'], request.form['category'], request.form['image_url'], request.form['custom_options'],
            request.form['name_en'], request.form['name_jp'], request.form['custom_options_en'], request.form['custom_options_jp'], pid
        ))
        conn.commit(); conn.close()
        return redirect('/admin')
    
    cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
    p = cur.fetchone()
    conn.close()
    return f"""
    <!DOCTYPE html><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css"></head>
    <body style="padding:20px;"><h3>編輯 #{p[0]}</h3>
    <form method="POST">
        <label>名稱</label><input type="text" name="name" value="{p[1]}">
        <label>價格</label><input type="number" name="price" value="{p[2]}">
        <label>分類</label><input type="text" name="category" value="{p[3]}">
        <label>圖片URL</label><input type="text" name="image_url" value="{p[4] or ''}">
        <label>選項</label><input type="text" name="custom_options" value="{p[6] or ''}">
        <label>Name(EN)</label><input type="text" name="name_en" value="{p[8] or ''}">
        <label>名前(JP)</label><input type="text" name="name_jp" value="{p[9] or ''}">
        <button type="submit">儲存</button> <a href="/admin" class="button button-outline">取消</a>
    </form></body>
    """

# --- 防休眠 ---
def keep_alive():
    while True:
        try: urllib.request.urlopen("http://127.0.0.1:10000/")
        except: pass
        time.sleep(800)
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
