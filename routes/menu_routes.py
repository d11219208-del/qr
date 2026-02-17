# routes/menu_routes.py
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from database import get_db_connection
from translations import load_translations
from datetime import timedelta, datetime
import json
import traceback

menu_bp = Blueprint('menu', __name__)

# ==========================================
# 1. 共用函數：讀取產品與設定 (加入 store_id)
# ==========================================
def get_menu_data(store_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 讀取特定店鋪的所有設定
    cur.execute("SELECT key, value FROM settings WHERE store_id = %s", (store_id,))
    settings_rows = cur.fetchall()
    settings = {row[0]: row[1] for row in settings_rows}
    
    # 確保 delivery_min_price 存在
    if 'delivery_min_price' not in settings:
        settings['delivery_min_price'] = '0'
    
    # 讀取特定店鋪產品
    cur.execute("""
        SELECT id, name, price, category, image_url, is_available, custom_options, sort_order,
               name_en, name_jp, name_kr, 
               custom_options_en, custom_options_jp, custom_options_kr, 
               print_category, 
               category_en, category_jp, category_kr
        FROM products 
        WHERE store_id = %s
        ORDER BY sort_order ASC, id ASC
    """, (store_id,))
    products = cur.fetchall()
    cur.close()
    conn.close()

    p_list = []
    for p in products:
        def parse_opts(opt_str, fallback_str=None):
            if opt_str: return opt_str.split(',')
            if fallback_str: return fallback_str.split(',')
            return []

        p_list.append({
            'id': p[0], 
            'name_zh': p[1], 
            'name_en': p[8] or p[1], 
            'name_jp': p[9] or p[1], 
            'name_kr': p[10] or p[1],
            'price': p[2], 
            'category_zh': p[3], 
            'category_en': p[15] or p[3], 
            'category_jp': p[16] or p[3], 
            'category_kr': p[17] or p[3],
            'image_url': p[4] or '', 
            'is_available': p[5], 
            'custom_options_zh': parse_opts(p[6]),
            'custom_options_en': parse_opts(p[11], p[6]),
            'custom_options_jp': parse_opts(p[12], p[6]),
            'custom_options_kr': parse_opts(p[13], p[6]),
            'print_category': p[14] or 'Noodle'
        })
    return settings, p_list

# ==========================================
# 2. 共用函數：處理訂單提交 (加入 store_id)
# ==========================================
def process_order_submission(request, store_id, order_type_override=None):
    display_lang = request.form.get('lang_input', 'zh')
    
    conn = get_db_connection()
    conn.autocommit = False 
    cur = conn.cursor()

    try:
        # --- A. 檢查店鋪狀態 (限本店) ---
        cur.execute("SELECT key, value FROM settings WHERE store_id = %s AND key IN ('shop_open', 'delivery_enabled')", (store_id,))
        settings_rows = dict(cur.fetchall())
        shop_open = settings_rows.get('shop_open', '1') == '1'
        delivery_enabled = settings_rows.get('delivery_enabled', '1') == '1'

        if not shop_open:
            return "Shop is Closed / 本店休息中", 403

        # --- B. 接收表單資料 ---
        raw_table_number = request.form.get('table_number')
        cart_json = request.form.get('cart_data')
        need_receipt = request.form.get('need_receipt') == 'on'
        final_lang = request.form.get('lang_input', 'zh')
        old_order_id = request.form.get('old_order_id')
        
        order_type = order_type_override if order_type_override else request.form.get('order_type', 'dine_in')
        
        if order_type == 'delivery' and not delivery_enabled:
             return "Delivery Service disabled / 外送服務關閉中", 403

        # --- C. 處理外送資訊 ---
        sess_data = session.get('delivery_data', {})
        sess_info = session.get('delivery_info', {})

        customer_name = request.form.get('customer_name') or request.form.get('name') or sess_data.get('name') or ''
        customer_phone = request.form.get('customer_phone') or request.form.get('phone') or sess_data.get('phone') or ''
        customer_address = request.form.get('delivery_address') or request.form.get('address') or sess_data.get('address') or ''
        note = request.form.get('delivery_note') or request.form.get('note') or sess_data.get('note') or ''
        scheduled_for = request.form.get('scheduled_for') or sess_data.get('scheduled_for') or ''
        
        delivery_info_json_str = None
        delivery_fee = 0
        
        should_process_as_delivery = False
        if order_type == 'delivery':
            should_process_as_delivery = True
        elif (customer_address and len(customer_address) > 2) and (order_type_override != 'dine_in'):
            should_process_as_delivery = True

        if should_process_as_delivery:
            order_type = 'delivery'
            sess_fee = sess_info.get('shipping_fee')
            form_fee = request.form.get('delivery_fee')
            delivery_fee = int(float(sess_fee)) if sess_fee is not None else int(float(form_fee or 0))

            delivery_info_dict = {
                'name': customer_name, 'phone': customer_phone, 'address': customer_address, 
                'scheduled_for': scheduled_for, 'distance_km': sess_info.get('distance_km') or request.form.get('distance_km'),
                'note': note, 'shipping_fee': delivery_fee
            }
            delivery_info_json_str = json.dumps(delivery_info_dict, ensure_ascii=False)
            table_number = "外送"
        else:
            delivery_fee = 0
            if raw_table_number and raw_table_number.strip():
                table_number = raw_table_number
                order_type = 'dine_in'
            else:
                table_number = "外帶"
                order_type = 'takeout'

        if not cart_json or cart_json == '[]': 
            return "Empty Cart", 400

        # --- D. 計算總金額 ---
        cart_items = json.loads(cart_json)
        total_price = 0
        display_list = []

        if old_order_id:
            cur.execute("SELECT lang FROM orders WHERE id=%s AND store_id=%s", (old_order_id, store_id))
            orig_res = cur.fetchone()
            if orig_res: final_lang = orig_res[0] 

        for item in cart_items:
            price = int(float(item['unit_price']))
            qty = int(float(item['qty']))
            total_price += (price * qty)
            n_display = item.get(f"name_{final_lang}", item.get('name_zh'))
            opts = item.get(f"options_{final_lang}", item.get('options_zh', []))
            opt_str = f"({','.join(opts)})" if opts else ""
            display_list.append(f"{n_display} {opt_str} x{qty}")

        items_str = " + ".join(display_list)
        total_price += delivery_fee

        # --- E. 寫入資料庫 (依店鋪流水號) ---
        cur.execute("LOCK TABLE orders IN SHARE ROW EXCLUSIVE MODE")
        cur.execute("""
            INSERT INTO orders (
                store_id, table_number, items, total_price, lang, 
                daily_seq, 
                content_json, need_receipt, created_at,
                order_type, delivery_info, delivery_fee,
                customer_name, customer_phone, customer_address, scheduled_for
            )
            VALUES (
                %s, %s, %s, %s, %s, 
                (SELECT COALESCE(MAX(daily_seq), 0) + 1 FROM orders WHERE store_id = %s AND created_at >= CURRENT_DATE), 
                %s, %s, NOW(),
                %s, %s, %s,
                %s, %s, %s, %s
            )
            RETURNING id, daily_seq
        """, (
            store_id, table_number, items_str, total_price, final_lang, 
            store_id, cart_json, need_receipt, 
            order_type, delivery_info_json_str, delivery_fee,
            customer_name, customer_phone, customer_address, scheduled_for
        ))

        res = cur.fetchone()
        oid = res[0]
        
        if old_order_id:
            cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s AND store_id=%s", (old_order_id, store_id))
        
        conn.commit()
        if old_order_id: 
            return f"<script>localStorage.removeItem('cart_cache'); alert('訂單已更新'); if(window.opener) window.opener.location.reload(); window.close();</script>"
        
        return redirect(url_for('menu.order_success', order_id=oid, lang=final_lang))

    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        return f"Order Failed: {e}", 500
    finally:
        cur.close()
        conn.close()


# ==========================================
# 3. 路由定義
# ==========================================

# --- 首頁 (入口支援 store_id) ---
@menu_bp.route('/')
@menu_bp.route('/<int:store_id>')
def index(store_id=1):
    table_num = request.args.get('table', '')
    session['customer_store_id'] = store_id  # 將店鋪 ID 存入 Session
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings WHERE store_id = %s AND key IN ('shop_open', 'delivery_enabled')", (store_id,))
    settings = dict(cur.fetchall())
    conn.close()
    
    shop_open = settings.get('shop_open', '1') == '1'
    delivery_enabled = settings.get('delivery_enabled', '1') == '1'

    if 'delivery_data' in session: session.pop('delivery_data', None)
    if 'delivery_info' in session: session.pop('delivery_info', None)
    
    return render_template('index.html', 
                           store_id=store_id,
                           table_num=table_num, 
                           shop_open=shop_open, 
                           delivery_enabled=delivery_enabled)


# --- 內用/外帶 路由 ---
@menu_bp.route('/menu', methods=['GET', 'POST'])
def menu():
    store_id = session.get('customer_store_id', 1)
    if request.method == 'POST':
        return process_order_submission(request, store_id, order_type_override='dine_in')

    display_lang = request.args.get('lang', 'zh')
    t_all = load_translations()
    t = t_all.get(display_lang, t_all['zh'])

    url_table = request.args.get('table', '')
    edit_oid = request.args.get('edit_oid')
    preload_cart = "null" 
    order_lang = display_lang 

    if edit_oid:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT table_number, content_json, lang FROM orders WHERE id=%s AND store_id=%s", (edit_oid, store_id))
        old_data = cur.fetchone()
        cur.close(); conn.close()
        if old_data:
            if not url_table: url_table = old_data[0]
            preload_cart = old_data[1] 
            order_lang = old_data[2] or 'zh'

    settings, products = get_menu_data(store_id)
    
    return render_template('menu.html', 
                           products=products, texts=t, table_num=url_table, 
                           display_lang=display_lang, order_lang=order_lang, 
                           preload_cart=preload_cart, edit_oid=edit_oid, config=settings,
                           current_mode='dine_in', is_delivery_mode=False)


# --- 外送 專用路由 ---
@menu_bp.route('/delivery', methods=['GET', 'POST'])
def delivery_menu():
    store_id = session.get('customer_store_id', 1)
    if request.method == 'POST':
        return process_order_submission(request, store_id, order_type_override='delivery')
    
    settings, products = get_menu_data(store_id)
    if settings.get('delivery_enabled', '1') != '1':
        return redirect(url_for('menu.index', store_id=store_id))

    display_lang = request.args.get('lang', 'zh')
    t_all = load_translations()
    t = t_all.get(display_lang, t_all['zh'])
    session_delivery = session.get('delivery_data', {})
    
    return render_template('menu.html', 
                           products=products, texts=t, table_num="外送", 
                           display_lang=display_lang, order_lang=display_lang, 
                           preload_cart="null", edit_oid=None, config=settings,
                           current_mode='delivery', is_delivery_mode=True,
                           session_delivery=session_delivery)


# --- 下單成功頁面 (加入 store_id 安全檢查) ---
@menu_bp.route('/success')
def order_success():
    oid = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    store_id = session.get('customer_store_id', 1)
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT daily_seq, content_json, total_price, created_at, 
               order_type, delivery_info, delivery_fee,
               customer_name, customer_phone, customer_address, scheduled_for,
               table_number
        FROM orders WHERE id=%s AND store_id=%s
    """, (oid, store_id))
    row = cur.fetchone()
    cur.close(); conn.close()
    
    if not row: return "Order Not Found", 404
    
    seq, json_str, total, created_at, order_type, delivery_info_json, delivery_fee, c_name, c_phone, c_addr, c_time, table_num_db = row
    is_delivery = (str(order_type or '').strip().lower() == 'delivery') or (str(table_num_db or '').strip() == '外送')
    
    delivery_info_dict = json.loads(delivery_info_json) if delivery_info_json else {}
    d_name = c_name or delivery_info_dict.get('name', 'N/A')
    d_phone = c_phone or delivery_info_dict.get('phone', 'N/A')
    d_addr = c_addr or delivery_info_dict.get('address', 'N/A')
    d_note = delivery_info_dict.get('note', '')
    d_scheduled = str(c_time or delivery_info_dict.get('scheduled_for', ''))[:16]

    items = json.loads(json_str) if json_str else []
    items_html = ""
    for i in items:
        row_total = int(float(i['unit_price'])) * int(float(i['qty']))
        d_name_prod = i.get(f'name_{lang}', i.get('name_zh', 'Product'))
        ops = i.get(f'options_{lang}', i.get('options_zh', []))
        opt_str = f"<br><small style='color:#777; font-size:0.9em;'>└ {', '.join(ops)}</small>" if ops else ""
        items_html += f"""
        <div style='display:flex; justify-content:space-between; align-items: flex-start; border-bottom:1px solid #eee; padding:15px 0;'>
            <div style="text-align: left; padding-right: 10px;">
                <div style="font-size:1.1em; font-weight:bold; color:#333;">{d_name_prod} <span style="color:#888; font-weight:normal;">x{i['qty']}</span></div>
                {opt_str}
            </div>
            <div style="font-weight:bold; font-size:1.1em; white-space:nowrap;">${row_total}</div>
        </div>"""
    
    delivery_html = ""
    fee_row_html = ""
    if is_delivery:
        fee_label = "Delivery Fee" if lang == 'en' else "運費"
        fee_row_html = f"<div style='display:flex; justify-content:space-between; padding:15px 0; color:#007bff;'><div style='font-weight:bold;'>🛵 {fee_label}</div><div style='font-weight:bold;'>${delivery_fee}</div></div>"
        delivery_html = f"""<div style="background:#e3f2fd; padding:15px; border-radius:10px; margin-bottom:20px; text-align:left;">
            <h4 style="margin:0 0 10px 0; color:#1565c0;">🛵 外送資訊</h4>
            {f"<div><b>預約時間:</b> {d_scheduled}</div>" if d_scheduled else ""}
            <div><b>姓名:</b> {d_name}</div><div><b>電話:</b> {d_phone}</div><div><b>地址:</b> {d_addr}</div>
        </div>"""
        status_msg, wait_msg = "Order Received / 訂單已收到", "Please wait for confirmation call."
    else:
        status_msg, wait_msg = t.get('pay_at_counter', '請至櫃檯結帳'), t.get('kitchen_prep', 'Kitchen is preparing your meal.')

    tw_time = (created_at + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    back_link = url_for('menu.delivery_menu') if is_delivery else url_for('menu.index', store_id=store_id)
    back_text = "Back to Delivery" if is_delivery else "Back to Menu"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ margin: 0; padding: 0; background: #fdfdfd; font-family: sans-serif; }}
            .container {{ padding: 20px; }}
            .card {{ background: #fff; border-radius: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); padding: 30px 20px; text-align: center; }}
            .seq-box {{ background: #fff5f8; border-radius: 15px; padding: 20px; margin-bottom: 25px; }}
            .seq-number {{ font-size: 5em; font-weight: 900; color: #e91e63; }}
            .home-btn {{ display: block; padding: 18px; background: #007bff; color: white !important; text-decoration: none; border-radius: 12px; font-weight: bold; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1 style="color:#28a745;">{t.get('order_success', '下單成功')}</h1>
                <div class="seq-box">
                    <div style="color:#e91e63;">取餐單號</div>
                    <div class="seq-number">#{seq:03d}</div>
                </div>
                <div style="background:#fdf6e3; padding:15px; border-left:5px solid #ff9800; text-align:left; margin-bottom:20px;">
                    <b>{status_msg}</b><br>{wait_msg}
                </div>
                {delivery_html}
                <div style="text-align:left;">
                    <h3 style="border-bottom:1px solid #eee;">🧾 {t.get('order_details', '訂單明細')}</h3>
                    {items_html}
                    {fee_row_html}
                    <div style="font-size:1.8em; font-weight:bold; color:#d32f2f; text-align:right;">Total: ${total}</div>
                </div>
                <p style="color:#999; font-size:0.8em;">Time: {tw_time}</p>
                <a href="{back_link}" class="home-btn">{back_text}</a>
            </div>
        </div>
    </body>
    </html>
    """
