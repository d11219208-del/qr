from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from database import get_db_connection
from translations import load_translations
import json

menu_bp = Blueprint('menu', __name__)

# --- 語言選擇首頁 (維持現狀) ---
@menu_bp.route('/')
def index():
    table_num = request.args.get('table', '')
    return render_template('index.html', table_num=table_num)

# --- 點餐頁面 (bfcache & 編輯功能強化版) ---
@menu_bp.route('/menu', methods=['GET', 'POST'])
def menu():
    display_lang = request.args.get('lang', 'zh')
    t_all = load_translations()
    t = t_all.get(display_lang, t_all['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()

    # --- 處理 POST 提交訂單 ---
    if request.method == 'POST':
        try:
            table_number = request.form.get('table_number')
            cart_json = request.form.get('cart_data')
            need_receipt = request.form.get('need_receipt') == 'on'
            final_lang = request.form.get('lang_input', 'zh')
            old_order_id = request.form.get('old_order_id')

            if not cart_json or cart_json == '[]': 
                return "Empty Cart", 400

            cart_items = json.loads(cart_json)
            total_price = 0
            display_list = []

            # 如果是編輯訂單，鎖定原始語言
            if old_order_id:
                cur.execute("SELECT lang FROM orders WHERE id=%s", (old_order_id,))
                orig_res = cur.fetchone()
                if orig_res: final_lang = orig_res[0] 

            # 解析購物車內容並計算總價
            for item in cart_items:
                price = int(float(item['unit_price']))
                qty = int(float(item['qty']))
                total_price += (price * qty)
                
                # 根據語系產生顯示字串
                name_key = f"name_{final_lang}"
                n_display = item.get(name_key, item.get('name_zh'))
                opt_key = f"options_{final_lang}"
                opts = item.get(opt_key, item.get('options_zh', []))
                opt_str = f"({','.join(opts)})" if opts else ""
                display_list.append(f"{n_display} {opt_str} x{qty}")

            items_str = " + ".join(display_list)

            # 插入資料庫 (自動計算今日序號)
            cur.execute("""
                INSERT INTO orders (table_number, items, total_price, lang, daily_seq, content_json, need_receipt)
                VALUES (%s, %s, %s, %s, (SELECT COALESCE(MAX(daily_seq), 0) + 1 FROM orders WHERE created_at >= CURRENT_DATE), %s, %s) 
                RETURNING id
            """, (table_number, items_str, total_price, final_lang, cart_json, need_receipt))

            oid = cur.fetchone()[0]

            # 如果是編輯舊訂單，將舊單作廢
            if old_order_id:
                cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (old_order_id,))
            
            conn.commit()
            
            if old_order_id: 
                # 編輯模式成功後的 JS 回饋
                return f"<script>localStorage.removeItem('cart_cache'); alert('Order #{old_order_id} Updated'); if(window.opener) window.opener.location.reload(); window.close();</script>"
            
            # 一般點餐成功，跳轉至成功頁面
            return redirect(url_for('menu.order_success', order_id=oid, lang=final_lang))
        except Exception as e:
            conn.rollback()
            return f"Order Failed: {e}", 500
        finally:
            cur.close(); conn.close()

    # --- 處理 GET 載入頁面 ---
    url_table = request.args.get('table', '')
    edit_oid = request.args.get('edit_oid')
    preload_cart = "null" 
    order_lang = display_lang 

    # 如果是編輯模式，先抓取舊訂單資料
    if edit_oid:
        cur.execute("SELECT table_number, content_json, lang FROM orders WHERE id=%s", (edit_oid,))
        old_data = cur.fetchone()
        if old_data:
            if not url_table: url_table = old_data[0]
            preload_cart = old_data[1] 
            order_lang = old_data[2] if old_data[2] else 'zh'

    # 抓取產品清單
    cur.execute("""
        SELECT id, name, price, category, image_url, is_available, custom_options, sort_order,
               name_en, name_jp, name_kr, custom_options_en, custom_options_jp, custom_options_kr, 
               print_category, category_en, category_jp, category_kr
        FROM products ORDER BY sort_order ASC, id ASC
    """)
    products = cur.fetchall()
    cur.close(); conn.close()

    # 轉換產品格式
    p_list = []
    for p in products:
        p_list.append({
            'id': p[0], 'name_zh': p[1], 'name_en': p[8] or p[1], 'name_jp': p[9] or p[1], 'name_kr': p[10] or p[1],
            'price': p[2], 'category_zh': p[3], 'category_en': p[15] or p[3], 'category_jp': p[16] or p[3], 'category_kr': p[17] or p[3],
            'image_url': p[4] or '', 'is_available': p[5], 
            'custom_options_zh': p[6].split(',') if p[6] else [],
            'custom_options_en': p[11].split(',') if p[11] else (p[6].split(',') if p[6] else []),
            'custom_options_jp': p[12].split(',') if p[12] else (p[6].split(',') if p[6] else []),
            'custom_options_kr': p[13].split(',') if p[13] else (p[6].split(',') if p[6] else []),
            'print_category': p[14] or 'Noodle'
        })
    
    # 回傳給前端渲染
    return render_template('menu.html', 
                           products=p_list, 
                           texts=t, 
                           table_num=url_table, 
                           display_lang=display_lang, 
                           order_lang=order_lang, 
                           preload_cart=preload_cart, 
                           edit_oid=edit_oid)

# --- 訂單成功頁面 ---
@menu_bp.route('/success')
def order_success():
    order_id = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    # ... 渲染成功頁面的邏輯 ...
    return f"Order #{order_id} Success! (Language: {lang})"
