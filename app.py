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

# --- 翻譯設定 ---
def load_translations():
    fallback = {
        "zh": {
            "title": "線上點餐", "welcome": "歡迎點餐", "table_placeholder": "請輸入桌號", 
            "table_label": "桌號", "add": "加入", "sold_out": "已售完", "cart_detail": "查看明細", 
            "total": "合計", "checkout": "去結帳", "cart_title": "購物車明細", "empty_cart": "購物車是空的", 
            "close": "關閉", "confirm_delete": "確定刪除？", "confirm_order": "確定送出？", 
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
    return fallback

# --- 1. 資料庫初始化 (包含所有後台欄位) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        # 產品表
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
        # 訂單表
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
        
        # 補欄位 (防止舊資料庫報錯)
        alters = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS daily_seq INTEGER DEFAULT 0;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS need_receipt BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_en VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_jp VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_en TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_jp TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 100;"
        ]
        for cmd in alters:
            try: cur.execute(cmd)
            except: pass

        return "資料庫初始化/更新完成！<a href='/admin'>前往後台管理</a> | <a href='/'>前往點餐首頁</a>"
    except Exception as e:
        return f"Error: {e}"
    finally:
        cur.close(); conn.close()

# --- 2. 首頁 (語言選擇) ---
@app.route('/')
def language_select():
    # 如果有帶 table 參數，傳遞下去
    tbl = request.args.get('table', '')
    q = f"?table={tbl}" if tbl else ""
    
    return f"""
    <!DOCTYPE html>
    <html><head><title>Language</title><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body{{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;background:#f4f7f6;}}
    .btn{{width:200px;padding:15px;margin:10px;text-align:center;text-decoration:none;font-size:1.2em;border-radius:50px;color:white;box-shadow:0 4px 6px rgba(0,0,0,0.1);}}
    .zh{{background:#e91e63;}} .en{{background:#007bff;}} .jp{{background:#ff9800;}}</style></head>
    <body><h2>Select Language</h2>
    <a href="/menu{q}&lang=zh" class="btn zh">中文</a>
    <a href="/menu{q}&lang=en" class="btn en">English</a>
    <a href="/menu{q}&lang=jp" class="btn jp">日本語</a>
    </body></html>
    """

# --- 3. 點餐頁面 ---
@app.route('/menu', methods=['GET', 'POST'])
def menu():
    lang = request.args.get('lang', 'zh')
    # 預設桌號邏輯
    url_table = request.args.get('table', '')
    
    t = load_translations().get(lang, load_translations()['zh'])
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            table_number = request.form.get('table_number')
            cart_json = request.form.get('cart_data')
            need_receipt = request.form.get('need_receipt') == 'on'
            
            if not cart_json or cart_json == '[]': return "Empty"
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
            
            cur.execute("""
                INSERT INTO orders (table_number, items, total_price, lang, daily_seq, content_json, need_receipt)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (table_number, items_str, total_price, lang, new_seq, cart_json, need_receipt))
            
            oid = cur.fetchone()[0]
            conn.commit()
            return redirect(url_for('order_success', order_id=oid, lang=lang))
        except Exception as e:
            conn.rollback()
            return f"Order Error: {e}"
        finally:
            cur.close(); conn.close()

    # GET Menu
    cur.execute("SELECT * FROM products WHERE is_available=TRUE ORDER BY sort_order ASC, id ASC")
    products = cur.fetchall()
    cur.close(); conn.close()
    
    p_list = []
    for p in products:
        # 0:id, 1:name, 2:price, 3:cat, 4:img, 5:avail, 6:opts, 7:sort, 8:en, 9:jp, 10:opt_en, 11:opt_jp
        d_name = p[1]
        d_opts = p[6]
        # 多語言切換
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

    return render_frontend(p_list, t, url_table)

def render_frontend(products, t, default_table):
    p_json = json.dumps(products)
    t_json = json.dumps(t)
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
        <h3>{t['welcome']}</h3>
        <input type="text" id="visible_table" value="{default_table}" placeholder="{t['table_placeholder']}" style="padding:10px;width:100%;box-sizing:border-box;border:1px solid #ddd;border-radius:5px;">
    </div>
    <div id="list"></div>
    <form id="order-form" method="POST">
        <input type="hidden" name="cart_data" id="cart_input">
        <input type="hidden" name="table_number" id="tbl_input">
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
    const P={p_json}, T={t_json};
    let C=[], cur=null, q=1, opts=[], addP=0;
    
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

# --- 4. 下單成功 (修復顯示明細 + 提示語) ---
@app.route('/order_success')
def order_success():
    oid = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    t = load_translations().get(lang, load_translations()['zh'])
    
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT daily_seq, content_json, total_price FROM orders WHERE id=%s", (oid,))
    row = cur.fetchone()
    conn.close()

    if not row: return "Error"
    seq, json_str, total = row
    items = json.loads(json_str) if json_str else []
    
    # 產生明細 HTML
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

# --- 5. 廚房後台 (顯示已刪除、日結報表) ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection(); cur = conn.cursor()
    # 修改查詢：顯示所有今日訂單，包含 Cancelled
    cur.execute("SELECT * FROM orders WHERE created_at >= CURRENT_DATE ORDER BY daily_seq DESC")
    orders = cur.fetchall()
    conn.close()

    html = """
    <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{background:#222;color:white;font-family:sans-serif;padding:10px;}
        .card{background:#333;margin-bottom:15px;padding:15px;border-radius:5px;border-left:5px solid #ff9800;position:relative;}
        .completed{border-left-color:#28a745;opacity:0.6;} 
        .cancelled{border-left-color:#dc3545;background:#442222; opacity:0.8;} /* 刪除樣式 */
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
        # o: 0:id, 1:tbl, 2:items, 3:price, 4:status, 5:time, 6:lang, 7:seq, 8:json, 9:receipt
        status = o[4]
        cls = status.lower()
        seq = f"{o[7]:03d}"
        
        # 狀態標籤
        tag = ""
        if status == 'Cancelled': tag = "<span style='background:red;color:white;'>已作廢</span>"
        elif status == 'Completed': tag = "<span style='background:green;color:white;'>已完成</span>"

        # 按鈕邏輯
        btns = ""
        if status == 'Pending':
            btns += f"<a href='/kitchen/complete/{o[0]}' class='btn' style='background:#28a745'>完成</a>"
        
        # 刪除按鈕 (未作廢才顯示)
        if status != 'Cancelled':
            btns += f"<a href='/order/cancel/{o[0]}' class='btn' style='background:#dc3545' onclick=\"return confirm('確定作廢此單？將不計入營收。')\">🗑️ 作廢</a>"
        
        # 編輯與列印 (隨時都可)
        btns += f"""
            <a href='/print_order/{o[0]}' target='_blank' class='btn' style='background:#17a2b8'>🖨️ 列印</a>
            <a href='/admin/edit_order/{o[0]}' class='btn' style='background:#ffc107;color:black;'>✏️ 編輯</a>
        """

        html += f"""
        <div class="card {cls}">
            <div class="tag">{tag}</div>
            <span style="font-size:1.5em;color:#ff9800;">#{seq}</span> 桌號: {o[1]} <small>({o[5].strftime('%H:%M')})</small>
            <div class="items" style="margin:10px 0;font-size:1.2em;">{o[2].replace(" + ", "<br>")}</div>
            <div style="border-top:1px solid #555;padding-top:10px;">{btns}</div>
        </div>
        """
    return html

# --- 6. 日結報表 ---
@app.route('/kitchen/report')
def daily_report():
    conn = get_db_connection(); cur = conn.cursor()
    # 只計算非作廢的訂單
    cur.execute("""
        SELECT COUNT(*), SUM(total_price) 
        FROM orders WHERE created_at >= CURRENT_DATE AND status != 'Cancelled'
    """)
    valid_count, valid_total = cur.fetchone()
    
    # 計算作廢單
    cur.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE created_at >= CURRENT_DATE AND status = 'Cancelled'")
    void_count, void_total = cur.fetchone()
    
    conn.close()
    
    return f"""
    <!DOCTYPE html>
    <body style="font-family:sans-serif;padding:20px;background:#f4f4f4;">
        <div style="background:white;padding:30px;max-width:500px;margin:0 auto;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.1);">
            <h2 style="text-align:center;">📅 本日結帳單 (Daily Report)</h2>
            <p style="text-align:center;">{date.today()}</p>
            <hr>
            <h3>✅ 有效營收</h3>
            <p>總單量: {valid_count or 0} 單</p>
            <p style="font-size:2em;color:#28a745;font-weight:bold;">總金額: ${valid_total or 0}</p>
            <hr>
            <h3 style="color:#dc3545;">❌ 作廢/刪除</h3>
            <p>作廢單量: {void_count or 0} 單</p>
            <p>作廢金額: ${void_total or 0}</p>
            <hr>
            <button onclick="window.print()" style="width:100%;padding:15px;background:#007bff;color:white;border:none;border-radius:5px;font-size:1.2em;">列印報表</button>
            <br><br>
            <a href="/kitchen" style="display:block;text-align:center;">回到廚房</a>
        </div>
    </body>
    """

# --- 7. 功能路由 (狀態變更) ---
@app.route('/kitchen/complete/<int:oid>')
def complete_order(oid):
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

@app.route('/order/cancel/<int:oid>')
def cancel_order(oid):
    # 軟刪除：標記為 Cancelled
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

# --- 8. 列印功能 (智慧判斷作廢單) ---
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
    
    # 如果是作廢單，標題改變
    title_prefix = "❌ 作廢單 (VOID)" if is_void else "結帳單 (Receipt)"
    watermark = "text-decoration: line-through; color:red;" if is_void else ""
    
    # 工單分類
    noodles = [i for i in items if '主食' in i.get('category','') or 'Main' in i.get('category','')]
    soups = [i for i in items if i not in noodles]
    
    def ticket(title, lst, show_price=False):
        if not lst and not show_price: return ""
        h = f"<div class='ticket' style='{watermark}'>"
        h += f"<div class='head'><h2>{title}</h2><h1>#{seq}</h1><p>Table: {o[1]}</p></div><hr>"
        tot = 0
        for i in lst:
            tot += i['unit_price']*i['qty']
            h += f"<div class='row'><span>{i['qty']} x {i['name']}</span><span>{' $'+str(i['unit_price']*i['qty']) if show_price else ''}</span></div>"
            if i['options']: h+=f"<div class='opt'>({','.join(i['options'])})</div>"
        if show_price:
            h += f"<hr><div style='text-align:right;font-size:1.2em;'>Total: ${tot}</div>"
        h += "</div><div class='break'></div>"
        return h

    body = ""
    # 1. 結帳單 (如果是作廢單，強制列印一張作廢收據)
    if o[9] or is_void: 
        body += ticket(title_prefix, items, show_price=True)
    
    # 2. 廚房工單 (作廢單通常不需要再印工單，除非您需要通知廚房停止)
    # 這裡邏輯：如果是作廢，只印上面的作廢收據給櫃檯留底。如果是正常單，才印工單。
    if not is_void:
        body += ticket("🍜 麵區", noodles)
        body += ticket("🍲 湯/菜區", soups)

    return f"""
    <html><head><style>
    body{{font-family:'Courier New';font-size:14px;background:#eee;margin:0;}}
    .ticket{{width:58mm;background:white;margin:10px auto;padding:10px;}}
    .head{{text-align:center;}} h2,h1{{margin:5px 0;}}
    .row{{display:flex;justify-content:space-between;font-weight:bold;margin-top:5px;}}
    .opt{{font-size:12px;color:#444;margin-left:20px;}}
    .break{{page-break-after:always;height:1px;}}
    @media print{{ .ticket{{box-shadow:none;width:100%;margin:0;}} body{{background:white;}} }}
    </style></head><body onload="window.print()">{body}</body></html>
    """

# --- 9. 全功能後台管理 (修復所有遺失功能) ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    conn = get_db_connection(); cur = conn.cursor()
    
    if request.method == 'POST':
        # 新增產品
        name = request.form['name']
        price = request.form['price']
        cat = request.form['category']
        img = request.form['image_url']
        opts = request.form['custom_options']
        # 多語言
        name_en = request.form.get('name_en','')
        name_jp = request.form.get('name_jp','')
        opts_en = request.form.get('custom_options_en','')
        opts_jp = request.form.get('custom_options_jp','')
        
        cur.execute("""
            INSERT INTO products (name, price, category, image_url, custom_options, name_en, name_jp, custom_options_en, custom_options_jp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (name, price, cat, img, opts, name_en, name_jp, opts_en, opts_jp))
        conn.commit()
        return redirect('/admin')
    
    cur.execute("SELECT * FROM products ORDER BY id DESC")
    prods = cur.fetchall()
    conn.close()
    
    rows = ""
    for p in prods:
        rows += f"""
        <tr>
            <td>{p[0]}</td>
            <td><img src="{p[4]}" style="height:50px;"></td>
            <td>{p[1]}<br><small style="color:blue">{p[8]}</small><br><small style="color:orange">{p[9]}</small></td>
            <td>{p[2]}</td>
            <td>{p[3]}</td>
            <td>
                <a href="/admin/edit_product/{p[0]}">編輯</a> | 
                <a href="/admin/delete_product/{p[0]}" onclick="return confirm('刪除?')">刪除</a>
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    </head>
    <body style="padding:20px;">
        <h1>🔧 後台管理</h1>
        <div style="background:#f4f4f4;padding:20px;border-radius:10px;">
            <h3>新增產品</h3>
            <form method="POST">
                <div class="row">
                    <div class="column">
                        <label>名稱 (中)</label><input type="text" name="name" required placeholder="牛肉麵">
                        <label>Name (EN)</label><input type="text" name="name_en" placeholder="Beef Noodle">
                        <label>名前 (JP)</label><input type="text" name="name_jp" placeholder="牛肉麺">
                    </div>
                    <div class="column">
                        <label>價格</label><input type="number" name="price" required>
                        <label>分類</label><input type="text" name="category" required placeholder="主食">
                        <label>圖片網址</label><input type="text" name="image_url">
                    </div>
                </div>
                <label>選項 (中) <small>格式: 加麵:+10,不蔥</small></label>
                <input type="text" name="custom_options">
                <label>Options (EN)</label><input type="text" name="custom_options_en">
                <label>Options (JP)</label><input type="text" name="custom_options_jp">
                <button type="submit">新增產品</button>
            </form>
        </div>
        <hr>
        <table>
            <thead><tr><th>ID</th><th>圖</th><th>名稱</th><th>價格</th><th>分類</th><th>操作</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </body>
    """

@app.route('/admin/delete_product/<int:pid>')
def delete_product(pid):
    c=get_db_connection(); c.cursor().execute("DELETE FROM products WHERE id=%s",(pid,)); c.commit(); c.close()
    return redirect('/admin')

@app.route('/admin/edit_product/<int:pid>', methods=['GET','POST'])
def edit_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    if request.method=='POST':
        cur.execute("""
            UPDATE products SET name=%s, price=%s, category=%s, image_url=%s, custom_options=%s,
            name_en=%s, name_jp=%s, custom_options_en=%s, custom_options_jp=%s
            WHERE id=%s
        """, (
            request.form['name'], request.form['price'], request.form['category'], request.form['image_url'], request.form['custom_options'],
            request.form['name_en'], request.form['name_jp'], request.form['custom_options_en'], request.form['custom_options_jp'],
            pid
        ))
        conn.commit(); conn.close()
        return redirect('/admin')
    
    cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
    p = cur.fetchone()
    conn.close()
    
    # 填入舊資料
    return f"""
    <!DOCTYPE html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    </head>
    <body style="padding:20px;">
        <h3>編輯產品 #{p[0]}</h3>
        <form method="POST">
            <label>名稱 (中)</label><input type="text" name="name" value="{p[1]}">
            <label>Name (EN)</label><input type="text" name="name_en" value="{p[8] or ''}">
            <label>名前 (JP)</label><input type="text" name="name_jp" value="{p[9] or ''}">
            
            <label>價格</label><input type="number" name="price" value="{p[2]}">
            <label>分類</label><input type="text" name="category" value="{p[3]}">
            <label>圖片網址</label><input type="text" name="image_url" value="{p[4] or ''}">
            
            <label>選項 (中)</label><input type="text" name="custom_options" value="{p[6] or ''}">
            <label>Options (EN)</label><input type="text" name="custom_options_en" value="{p[10] or ''}">
            <label>Options (JP)</label><input type="text" name="custom_options_jp" value="{p[11] or ''}">
            
            <button type="submit">儲存修改</button> <a href="/admin" class="button button-outline">取消</a>
        </form>
    </body>
    """

# --- 10. 編輯訂單 (廚房用) ---
@app.route('/admin/edit_order/<int:oid>', methods=['GET','POST'])
def edit_order_backend(oid):
    conn = get_db_connection(); cur = conn.cursor()
    if request.method == 'POST':
        # 簡易版：只允許刪除品項或修改數量
        # 實務上解析 form 比較複雜，這裡假設傳遞完整的 JSON 或用原來的邏輯
        # 為了快速修復，我們這裡保留原本的邏輯，但建議未來做更細緻的 UI
        return "暫時請使用刪除/作廢功能，若需細項編輯請告知開發者增加詳細介面"
    
    # 目前僅提供簡單的刪除引導
    return f"<h3>編輯訂單 #{oid}</h3><p>目前建議直接使用<a href='/kitchen'>廚房看板</a>的作廢功能。如需修改數量，請作廢後重開。</p><a href='/kitchen'>回廚房</a>"

# --- 防休眠 ---
def keep_alive():
    while True:
        try: urllib.request.urlopen("http://127.0.0.1:10000/")
        except: pass
        time.sleep(800)
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
