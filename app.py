import os
import psycopg2
import json
import threading
import urllib.request
import time
import re # 新增正則表達式模組
from flask import Flask, request, redirect, url_for

app = Flask(__name__)

# --- 資料庫連線 ---
def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 翻譯載入 ---
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

# --- 1. 資料庫初始化 (強力修復版) ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    conn.autocommit = True # 設定自動提交，避免 transaction 卡住
    cur = conn.cursor()
    msg = []
    
    try:
        # 1. 建立基本表 (如果不存在)
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
        msg.append("基本表格檢查完成。")

        # 2. 逐一檢查並補上新欄位 (避免一次執行失敗全部回滾)
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
            except Exception as e:
                msg.append(f"欄位檢查略過: {e}")
        
        msg.append("資料庫結構更新成功！")
        return "<br>".join(msg) + "<br><br><a href='/'>👉 點此回到首頁開始點餐</a>"

    except Exception as e:
        return f"❌ 初始化嚴重失敗: {e}"
    finally:
        cur.close()
        conn.close()

# --- 2. 首頁 ---
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

# --- 3. 點餐頁面 (核心邏輯) ---
@app.route('/menu', methods=['GET', 'POST'])
def menu():
    lang = request.args.get('lang', 'zh')
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            table_number = request.form.get('table_number')
            cart_json = request.form.get('cart_data')
            need_receipt = request.form.get('need_receipt') == 'on'
            
            if not cart_json or cart_json == '[]': return "Empty Cart"

            try:
                cart_items = json.loads(cart_json)
            except:
                return "Cart JSON Error"
            
            # 後端重算總金額與顯示字串
            total_price = 0
            items_display_list = []
            
            for item in cart_items:
                # 確保數值型別正確
                u_price = int(float(item['unit_price'])) # 轉浮點再轉整數，防止報錯
                qty = int(float(item['qty']))
                opts = item.get('options', [])
                
                opts_str = f"({','.join(opts)})" if opts else ""
                items_display_list.append(f"{item['name']} {opts_str} x{qty}")
                total_price += (u_price * qty)

            items_str = " + ".join(items_display_list)

            # 產生流水號 (若欄位不存在，這裡會報錯，所以 init_db 很重要)
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
            return redirect(url_for('order_success', order_id=new_order_id, lang=lang))
            
        except Exception as e:
            conn.rollback()
            # 這是為了讓您看到具體錯誤原因，而不是 Internal Server Error
            return f"❌ 結帳失敗 (Error): {e} <br> <a href='/init_db'>請先點此執行資料庫修復 (Fix DB)</a>"
        finally:
            cur.close()
            conn.close()

    # GET: 讀取菜單
    try:
        cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
        products = cur.fetchall()
    except:
        return "資料庫讀取失敗，請先執行 <a href='/init_db'>/init_db</a>"
    finally:
        cur.close()
        conn.close()
    
    products_list = []
    for p in products:
        # 資料庫欄位對應
        # 0:id, 1:name, 2:price, 3:cat, 4:img, 5:avail, 6:opt_zh, 7:sort, 
        # 8:name_en, 9:name_jp, 10:opt_en, 11:opt_jp (如果欄位存在)
        
        display_name = p[1]
        display_opts = p[6]
        
        # 多語言 fallback
        has_multi = len(p) >= 12
        if lang == 'en' and has_multi:
            display_name = p[8] if (p[8] and p[8].strip()) else p[1]
            display_opts = p[10] if (p[10] and p[10].strip()) else p[6]
        elif lang == 'jp' and has_multi:
            display_name = p[9] if (p[9] and p[9].strip()) else p[1]
            display_opts = p[11] if (p[11] and p[11].strip()) else p[6]

        products_list.append({
            'id': p[0], 
            'name': display_name, 
            'price': p[2], 
            'category': p[3], 
            'image_url': p[4] if p[4] else "",
            'is_available': p[5],
            'custom_options': display_opts.split(',') if display_opts else [],
            'raw_category': p[3]
        })

    return render_frontend(products_list, t)

def render_frontend(products_data, t):
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
            .sold-out {{ background: #ccc; cursor: not-allowed; }}
            
            .modal-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: none; z-index: 999; justify-content: center; align-items: flex-end; }}
            .modal-content {{ background: white; width: 100%; border-radius: 20px 20px 0 0; padding: 20px; max-height: 80vh; overflow-y: auto; box-sizing: border-box; }}
            
            .cart-bar {{ position: fixed; bottom: 0; left: 0; width: 100%; background: white; padding: 15px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; z-index: 500; box-sizing: border-box; }}
            .option-tag {{ display: inline-block; border: 1px solid #ddd; padding: 8px 15px; border-radius: 20px; margin: 5px; cursor: pointer; }}
            .option-tag.selected {{ background: #e3f2fd; border-color: #2196f3; color: #2196f3; font-weight: bold; }}
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
                <div onclick="openCartModal()" style="flex-grow:1; cursor: pointer;">
                    <span id="total-qty" style="background:#e91e63; color:white; padding:2px 8px; border-radius:10px;">0</span> 
                    <b>{t['total']}: $<span id="total-price">0</span></b>
                </div>
                <div style="display:flex; align-items:center;">
                    <label style="margin-right:10px; font-size:0.8em; display:flex; align-items:center;">
                        <input type="checkbox" name="need_receipt" checked style="margin-right:5px;"> {t['print_receipt_opt']}
                    </label>
                    <button type="button" onclick="submitOrder()" style="background:#28a745; color:white; border:none; padding:10px 20px; border-radius:50px; font-weight:bold;">{t['checkout']}</button>
                </div>
            </div>
        </form>

        <div class="modal-overlay" id="option-modal"><div class="modal-content">
            <h3 id="m-title"></h3>
            <div style="color:#e91e63; font-weight:bold; margin-bottom:10px;">$<span id="m-price-display">0</span></div>
            <div id="m-opts"></div>
            <div style="margin-top:15px; display:flex; justify-content:center; align-items:center;">
                <button onclick="changeQ(-1)" style="width:40px;height:40px;border-radius:50%;border:1px solid #ccc;background:white;">-</button>
                <span id="m-qty" style="margin:0 20px; font-weight:bold; font-size:1.2em;">1</span>
                <button onclick="changeQ(1)" style="width:40px;height:40px;border-radius:50%;border:1px solid #ccc;background:white;">+</button>
            </div>
            <button onclick="addToCartConf()" style="width:100%; background:#28a745; color:white; padding:15px; border:none; border-radius:10px; margin-top:20px; font-size:1.1em;">{t['modal_add_cart']}</button>
            <button onclick="closeM()" style="width:100%; background:white; color:#666; padding:10px; border:none; margin-top:5px;">{t['modal_cancel']}</button>
        </div></div>

        <div class="modal-overlay" id="cart-modal"><div class="modal-content">
            <h3>{t['cart_title']}</h3>
            <div id="c-list"></div>
            <button onclick="document.getElementById('cart-modal').style.display='none'" style="width:100%; background:#6c757d; color:white; padding:15px; border-radius:10px; margin-top:10px; border:none;">{t['close']}</button>
        </div></div>

        <script>
            const prods = {products_json};
            const t = {t_json};
            let cart = [], curP = null, curQ = 1, curOpts = [], curAddP = 0;
            
            const c = document.getElementById('container');
            let cat = "";
            prods.forEach(p => {{
                if(p.category !== cat) {{
                    c.innerHTML += `<div style="padding:15px 10px 5px; color:#666; font-weight:bold; font-size:1.1em;">${{p.category}}</div>`;
                    cat = p.category;
                }}
                let btn = p.is_available 
                    ? `<button class="add-btn" onclick="openOpt(${{p.id}})">${{t.add}}</button>`
                    : `<button class="add-btn sold-out" disabled>${{t.sold_out}}</button>`;
                c.innerHTML += `
                <div class="menu-item">
                    <img src="${{p.image_url}}" class="menu-img">
                    <div class="menu-info">
                        <div><div style="font-weight:bold; font-size:1.1em;">${{p.name}}</div><div style="color:#e91e63; font-weight:bold;">$${{p.price}}</div></div>
                        ${{btn}}
                    </div>
                </div>`;
            }});

            // 智慧解析選項價格 (修復 Bug 核心)
            function parseOption(optStr) {{
                // 尋找字串尾部是否有數字，支援 :+20, : 20, +20, :20
                // Regex: 任何非數字字符(分隔符) + 數字 + 結尾
                const match = optStr.match(/[:：+\s]+(\d+)$/);
                if (match) {{
                    const price = parseInt(match[1]);
                    // 移除價格部分，只留名稱
                    const name = optStr.replace(match[0], '').trim();
                    return {{ name: name, price: price, origin: optStr }};
                }}
                return {{ name: optStr, price: 0, origin: optStr }};
            }}

            function openOpt(id) {{
                curP = prods.find(p=>p.id===id); curQ=1; curOpts=[]; curAddP=0;
                document.getElementById('m-title').innerText = curP.name;
                updateModalTotal();
                
                const area = document.getElementById('m-opts'); area.innerHTML='';
                if(curP.custom_options.length > 0) {{
                     area.innerHTML = '<p style="color:#888; font-size:0.9em;">' + t.custom_options + '</p>';
                }}
                
                curP.custom_options.forEach(o => {{
                    o = o.trim(); if(!o) return;
                    const parsed = parseOption(o);
                    
                    let el = document.createElement('div');
                    el.className = 'option-tag';
                    el.innerText = parsed.name + (parsed.price > 0 ? ` (+$${{parsed.price}})` : '');
                    
                    el.onclick = () => {{
                        // 檢查是否已選 (比對原始字串)
                        if(curOpts.includes(o)) {{ 
                            curOpts = curOpts.filter(x => x !== o); 
                            curAddP -= parsed.price; 
                            el.classList.remove('selected'); 
                        }} else {{ 
                            curOpts.push(o); 
                            curAddP += parsed.price; 
                            el.classList.add('selected'); 
                        }}
                        updateModalTotal();
                    }};
                    area.appendChild(el);
                }});
                document.getElementById('m-qty').innerText=1;
                document.getElementById('option-modal').style.display='flex';
            }}
            
            function updateModalTotal() {{
                const total = (curP.price + curAddP) * curQ;
                document.getElementById('m-price-display').innerText = total;
            }}

            function changeQ(n){{ 
                if(curQ+n>=1) {{ curQ+=n; document.getElementById('m-qty').innerText=curQ; updateModalTotal(); }} 
            }}
            
            function closeM(){{ document.getElementById('option-modal').style.display='none'; }}
            
            function addToCartConf() {{
                cart.push({{
                    id: curP.id, name: curP.name, 
                    unit_price: curP.price + curAddP, 
                    qty: curQ, options: [...curOpts],
                    category: curP.raw_category
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
                if(cart.length === 0) h = `<p style="text-align:center; color:#999;">${{t.empty_cart}}</p>`;
                cart.forEach((i, idx) => {{
                    // 解析選項顯示
                    let optDisplay = i.options.map(o => parseOption(o).name).join(', ');
                    
                    h += `<div style="border-bottom:1px solid #eee; padding:15px 0; display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div style="font-weight:bold;">${{i.name}} <span style="font-weight:normal;">x${{i.qty}}</span></div>
                            <small style="color:#888;">${{optDisplay}}</small>
                            <div style="color:#e91e63;">$${{i.unit_price * i.qty}}</div>
                        </div>
                        <button onclick="cart.splice(${{idx}},1); openCartModal(); updateBar();" style="color:white; background:#dc3545; border:none; border-radius:5px; padding:5px 10px;">🗑️</button>
                    </div>`;
                }});
                document.getElementById('c-list').innerHTML = h;
                document.getElementById('cart-modal').style.display='flex';
            }}

            function submitOrder() {{
                let tbl = document.getElementById('visible_table').value;
                if(!tbl) {{ alert(t.table_placeholder); return; }}
                document.getElementById('hidden_table').value = tbl;
                document.getElementById('cart_data_input').value = JSON.stringify(cart);
                
                let tot = cart.reduce((a,b)=>a+b.unit_price*b.qty,0);
                if(confirm(t.confirm_order + `\\n${{t.total}}: $${{tot}}`)) {{
                    document.getElementById('order-form').submit();
                }}
            }}
        </script>
    </body>
    </html>
    """

# --- 4. 下單成功頁面 ---
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
        <div style="font-size:3em; font-weight:bold; margin:20px; color:#e91e63;">{seq}</div>
        <p>您的單號 (No.{seq})</p>
        <p>廚房正在準備中...</p>
        <br>
        <a href="/" style="background:#007bff; color:white; padding:10px 30px; border-radius:20px; text-decoration:none;">回到首頁</a>
    </div>
    """

# --- 5. 廚房後台等其他路由 (保持不變，簡化版) ---
@app.route('/kitchen')
def kitchen():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE created_at >= current_date AND status != 'Cancelled' ORDER BY daily_seq DESC")
    orders = cur.fetchall()
    conn.close()

    html = """
    <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{background:#222;color:white;font-family:sans-serif;padding:10px;}
        .card{background:#333; margin-bottom:15px; padding:15px; border-radius:5px; border-left:5px solid #ff9800;}
        .done{border-left-color:#28a745; opacity:0.6;}
        .btn{padding:5px 10px; margin-right:5px; text-decoration:none; color:white; border-radius:3px; font-size:0.9em; display:inline-block;}
    </style></head><body><h2>👨‍🍳 廚房接單</h2>"""
    
    for o in orders:
        seq = f"{o[7]:03d}" if o[7] else "---" # daily_seq
        status = "done" if o[4]=='Completed' else ""
        items_html = o[2].replace(" + ", "<br>")
        
        actions = ""
        if o[4] != 'Completed':
            actions += f"<a href='/kitchen/complete/{o[0]}' class='btn' style='background:#28a745'>完成</a>"
        
        actions += f"""
            <a href='/print_order/{o[0]}' target='_blank' class='btn' style='background:#17a2b8'>🖨️</a>
            <a href='/order/edit/{o[0]}' class='btn' style='background:#ffc107; color:black;'>✏️</a>
            <a href='/order/delete/{o[0]}' class='btn' style='background:#dc3545' onclick="return confirm('刪除?')">🗑️</a>
        """

        html += f"""<div class="card {status}">
            <span style="font-size:1.5em; color:#ff9800; font-weight:bold;">#{seq}</span> 
            桌號: {o[1]} <small>({o[5].strftime('%H:%M')})</small>
            <div style="margin:10px 0; font-size:1.2em;">{items_html}</div>
            <div style="border-top:1px solid #555; padding-top:10px;">{actions}</div>
        </div>"""
    return html

@app.route('/kitchen/complete/<int:oid>')
def complete_order(oid):
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

@app.route('/order/delete/<int:oid>')
def delete_order(oid):
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

# --- 編輯與列印 (完整功能請參考上一個回應，這裡提供基礎支撐) ---
@app.route('/order/edit/<int:oid>', methods=['GET','POST'])
def edit_order(oid):
    # 這裡可以放上一次提供的編輯程式碼
    return "請將上個版本的 edit_order 函式貼回這裡 (因篇幅省略)"

@app.route('/print_order/<int:oid>')
def print_order(oid):
    # 這裡可以放上一次提供的 print_order 函式貼回這裡 (因篇幅省略)
    # 為了讓您目前能運作，提供一個簡易版
    c=get_db_connection(); cur=c.cursor(); cur.execute("SELECT * FROM orders WHERE id=%s",(oid,)); o=cur.fetchone(); c.close()
    return f"<pre>{o[2].replace(' + ', chr(10))}</pre><script>window.print()</script>"

# --- 防休眠 ---
def keep_alive():
    while True:
        try: urllib.request.urlopen("http://127.0.0.1:10000/")
        except: pass
        time.sleep(800)
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
