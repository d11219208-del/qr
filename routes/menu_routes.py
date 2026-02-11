from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from database import get_db_connection
from translations import load_translations
from datetime import timedelta, datetime
import json
import traceback

menu_bp = Blueprint('menu', __name__)

# --- 共用函數：讀取產品與設定 ---
def get_menu_data():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 讀取設定
    cur.execute("SELECT key, value FROM settings")
    settings = dict(cur.fetchall())
    
    # 讀取產品
    cur.execute("""
        SELECT id, name, price, category, image_url, is_available, custom_options, sort_order,
               name_en, name_jp, name_kr, custom_options_en, custom_options_jp, custom_options_kr, 
               print_category, category_en, category_jp, category_kr
        FROM products ORDER BY sort_order ASC, id ASC
    """)
    products = cur.fetchall()
    cur.close()
    conn.close()

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
    return settings, p_list

# --- 共用函數：處理訂單提交 (核心邏輯) ---
def process_order_submission(request, order_type_override=None):
    display_lang = request.form.get('lang_input', 'zh')
    
    conn = get_db_connection()
    conn.autocommit = False 
    cur = conn.cursor()

    try:
        # 1. 基本欄位
        raw_table_number = request.form.get('table_number')
        cart_json = request.form.get('cart_data')
        need_receipt = request.form.get('need_receipt') == 'on'
        final_lang = request.form.get('lang_input', 'zh')
        old_order_id = request.form.get('old_order_id')
        
        # 2. 判斷訂單類型 (優先使用傳入的 override 參數)
        order_type = order_type_override if order_type_override else request.form.get('order_type', 'dine_in')
        delivery_fee = int(float(request.form.get('delivery_fee', 0)))
        
        # 初始化欄位
        customer_name = None
        customer_phone = None
        customer_address = None
        scheduled_for = None
        delivery_info = None
        
        # --- 判斷邏輯 ---
        if order_type == 'delivery':
            # 外送邏輯
            customer_name = request.form.get('customer_name')
            customer_phone = request.form.get('customer_phone')
            
            # [關鍵修復] 確保能抓到地址，無論前端是用 delivery_address 還是 address
            customer_address = request.form.get('delivery_address') or request.form.get('address')
            
            scheduled_for = request.form.get('scheduled_for')
            
            # 建立完整的 delivery_info JSON
            delivery_info = json.dumps({
                'name': customer_name,
                'phone': customer_phone,
                'address': customer_address, 
                'scheduled_for': scheduled_for,
                'distance_km': request.form.get('distance_km'),
                'note': request.form.get('delivery_note')
            }, ensure_ascii=False)
            
            table_number = "外送"
        else:
            # 內用/外帶邏輯
            if raw_table_number and raw_table_number.strip():
                table_number = raw_table_number
                order_type = 'dine_in'
            else:
                table_number = "外帶"
                order_type = 'takeout'

        if not cart_json or cart_json == '[]': 
            return "Empty Cart", 400

        # 計算金額與項目字串
        cart_items = json.loads(cart_json)
        total_price = 0
        display_list = []

        if old_order_id:
            cur.execute("SELECT lang FROM orders WHERE id=%s", (old_order_id,))
            orig_res = cur.fetchone()
            if orig_res: final_lang = orig_res[0] 

        for item in cart_items:
            price = int(float(item['unit_price']))
            qty = int(float(item['qty']))
            total_price += (price * qty)
            
            name_key = f"name_{final_lang}"
            n_display = item.get(name_key, item.get('name_zh'))
            opt_key = f"options_{final_lang}"
            opts = item.get(opt_key, item.get('options_zh', []))
            opt_str = f"({','.join(opts)})" if opts else ""
            display_list.append(f"{n_display} {opt_str} x{qty}")

        items_str = " + ".join(display_list)
        total_price += delivery_fee

        # --- DB Transaction ---
        cur.execute("LOCK TABLE orders IN SHARE ROW EXCLUSIVE MODE")

        cur.execute("""
            INSERT INTO orders (
                table_number, items, total_price, lang, 
                daily_seq, 
                content_json, need_receipt, created_at,
                order_type, delivery_info, delivery_fee,
                customer_name, customer_phone, customer_address, scheduled_for
            )
            VALUES (
                %s, %s, %s, %s, 
                (SELECT COALESCE(MAX(daily_seq), 0) + 1 FROM orders WHERE created_at >= CURRENT_DATE), 
                %s, %s, NOW(),
                %s, %s, %s,
                %s, %s, %s, %s
            )
            RETURNING id, daily_seq
        """, (
            table_number, items_str, total_price, final_lang, 
            cart_json, need_receipt, 
            order_type, delivery_info, delivery_fee,
            customer_name, customer_phone, customer_address, scheduled_for
        ))

        res = cur.fetchone()
        oid = res[0]
        
        if old_order_id:
            cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (old_order_id,))
        
        conn.commit()
        
        if 'delivery_data' in session:
            session.pop('delivery_data', None)

        if old_order_id: 
            return f"<script>localStorage.removeItem('cart_cache'); alert('訂單已更新'); if(window.opener) window.opener.location.reload(); window.close();</script>"
        
        return redirect(url_for('menu.order_success', order_id=oid, lang=final_lang))

    except Exception as e:
        conn.rollback()
        print(f"Order Error: {e}")
        traceback.print_exc()
        return f"Order Failed: {e}", 500
    finally:
        cur.close()
        conn.close()

# --- 1. 首頁 ---
@menu_bp.route('/')
def index():
    table_num = request.args.get('table', '')
    if 'delivery_data' in session: session.pop('delivery_data', None)
    if 'delivery_info' in session: session.pop('delivery_info', None)
    return render_template('index.html', table_num=table_num)

# --- 2. 內用/外帶 專用路由 ---
@menu_bp.route('/menu', methods=['GET', 'POST'])
def menu():
    if request.method == 'POST':
        return process_order_submission(request, order_type_override='dine_in')

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
        cur.execute("SELECT table_number, content_json, lang FROM orders WHERE id=%s", (edit_oid,))
        old_data = cur.fetchone()
        cur.close(); conn.close()
        if old_data:
            if not url_table: url_table = old_data[0]
            preload_cart = old_data[1] 
            order_lang = old_data[2] if old_data[2] else 'zh'

    settings, products = get_menu_data()
    
    return render_template('menu.html', 
                           products=products, texts=t, table_num=url_table, 
                           display_lang=display_lang, order_lang=order_lang, 
                           preload_cart=preload_cart, edit_oid=edit_oid, config=settings,
                           current_mode='dine_in')

# --- 3. 外送 專用路由 ---
@menu_bp.route('/delivery', methods=['GET', 'POST'])
def delivery_menu():
    if request.method == 'POST':
        return process_order_submission(request, order_type_override='delivery')

    display_lang = request.args.get('lang', 'zh')
    t_all = load_translations()
    t = t_all.get(display_lang, t_all['zh'])
    
    settings, products = get_menu_data()

    return render_template('menu.html', 
                           products=products, texts=t, table_num="外送", 
                           display_lang=display_lang, order_lang=display_lang, 
                           preload_cart="null", edit_oid=None, config=settings,
                           current_mode='delivery')

# --- 4. 下單成功頁面 ---
@menu_bp.route('/success')
def order_success():
    oid = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    
    conn = get_db_connection(); cur = conn.cursor()
    # [修正 1] 新增 table_number 到查詢中，作為判斷的雙重保險
    cur.execute("""
        SELECT daily_seq, content_json, total_price, created_at, 
               order_type, delivery_info, delivery_fee,
               customer_name, customer_phone, customer_address, scheduled_for,
               table_number
        FROM orders WHERE id=%s
    """, (oid,))
    row = cur.fetchone()
    cur.close(); conn.close()
    
    if not row: return "Order Not Found", 404
    
    # 解構回傳資料
    seq, json_str, total, created_at, order_type, delivery_info_json, delivery_fee, c_name, c_phone, c_addr, c_time, table_num_db = row
    
    # [修正 2] 強力判斷是否為外送：
    # 條件A: order_type 是 "delivery" (忽略大小寫與空白)
    # 條件B: table_number 是 "外送" (這是最穩的，因為外送單一定會寫入這個桌號)
    type_is_delivery = (str(order_type or '').strip().lower() == 'delivery')
    table_is_delivery = (str(table_num_db or '').strip() == '外送')
    
    is_delivery = type_is_delivery or table_is_delivery
    
    tw_time = created_at + timedelta(hours=8)
    time_str = tw_time.strftime('%Y-%m-%d %H:%M:%S')
    items = json.loads(json_str) if json_str else []
    
    # 讀取 JSON 作為備用資料源
    delivery_info = json.loads(delivery_info_json) if delivery_info_json else {}
    
    # [修正 3] 優先讀取 DB 欄位，若無則讀取 JSON，最後給空字串 (避免 None 錯誤)
    d_name = c_name if c_name else delivery_info.get('name', '')
    d_phone = c_phone if c_phone else delivery_info.get('phone', '')
    raw_addr = c_addr if c_addr else delivery_info.get('address')
    d_addr = raw_addr if raw_addr else '' 

    d_scheduled = c_time if c_time else delivery_info.get('scheduled_for', '')
    d_note = delivery_info.get('note', '')
    
    items_html = ""
    subtotal = 0
    
    for i in items:
        price = i['unit_price'] * i['qty']
        subtotal += price
        d_name_prod = i.get(f'name_{lang}', i.get('name_zh', 'Product'))
        ops = i.get(f'options_{lang}', i.get('options_zh', []))
        opt_str = f"<br><small style='color:#777; font-size:0.9em;'>└ {', '.join(ops)}</small>" if ops else ""
        items_html += f"""
        <div style='display:flex; justify-content:space-between; align-items: flex-start; border-bottom:1px solid #eee; padding:15px 0;'>
            <div style="text-align: left; padding-right: 10px;">
                <div style="font-size:1.1em; font-weight:bold; color:#333;">{d_name_prod} <span style="color:#888; font-weight:normal;">x{i['qty']}</span></div>
                {opt_str}
            </div>
            <div style="font-weight:bold; font-size:1.1em; white-space:nowrap;">${price}</div>
        </div>
        """
    
    delivery_html = ""
    fee_row_html = ""
    
    # [修正 4] 只要是外送模式，就一定要生成 delivery_html，即使資料有缺也顯示空欄位
    if is_delivery:
        fee_label = "Delivery Fee" if lang == 'en' else "運費"
        fee_row_html = f"""
        <div style='display:flex; justify-content:space-between; align-items: center; border-bottom:2px solid #333; padding:15px 0; color:#007bff;'>
            <div style="font-weight:bold;">🛵 {fee_label}</div>
            <div style="font-weight:bold; font-size:1.1em;">${delivery_fee}</div>
        </div>
        """
        
        # 處理 None 值顯示為空字串，防止 Python 報錯
        disp_name = d_name or ''
        disp_phone = d_phone or ''
        disp_addr = d_addr or ''
        disp_note = d_note or ''
        
        time_display = f"<div style='margin-bottom:5px; color:#d32f2f;'><b>Scheduled:</b> {d_scheduled}</div>" if d_scheduled else ""

        delivery_html = f"""
        <div style="background:#e3f2fd; padding:15px; border-radius:10px; margin-bottom:20px; text-align:left; border:1px solid #90caf9;">
            <h4 style="margin:0 0 10px 0; color:#1565c0;">🛵 Delivery Info / 外送資訊</h4>
            {time_display}
            <div style="margin-bottom:5px;"><b>Name:</b> {disp_name}</div>
            <div style="margin-bottom:5px;"><b>Phone:</b> <a href="tel:{disp_phone}">{disp_phone}</a></div>
            <div style="margin-bottom:5px;"><b>Address:</b> {disp_addr}</div>
            <div style="font-size:0.9em; color:#555;"><b>Note:</b> {disp_note}</div>
        </div>
        """
        status_msg = "Order Received / 訂單已收到"
        wait_msg = "Please wait for confirmation call.<br>請留意電話，我們可能與您確認。"
    else:
        status_msg = t.get('pay_at_counter', '請至櫃檯結帳')
        wait_msg = t.get('kitchen_prep', 'Kitchen is preparing your meal.')

    back_link = url_for('menu.delivery_menu', lang=lang) if is_delivery else url_for('menu.index', lang=lang)
    back_text = "Back to Delivery" if is_delivery else "Back to Menu"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Order Success</title>
        <style>
            body {{ margin: 0; padding: 0; background: #fdfdfd; font-family: 'Microsoft JhengHei', -apple-system, sans-serif; }}
            .container {{ min-height: 100vh; display: flex; flex-direction: column; padding: 20px; box-sizing: border-box; }}
            .card {{ background: #fff; flex-grow: 1; border-radius: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); padding: 30px 20px; text-align: center; display: flex; flex-direction: column; }}
            .success-icon {{ font-size: 60px; margin-bottom: 10px; }}
            .status-title {{ color: #28a745; margin: 0 0 20px 0; font-size: 1.8em; }}
            .seq-box {{ background: #fff5f8; border-radius: 15px; padding: 20px; margin-bottom: 25px; border: 2px solid #ffeef2; }}
            .seq-label {{ font-size: 1em; color: #e91e63; font-weight: bold; margin-bottom: 8px; letter-spacing: 1px; }}
            .seq-number {{ font-size: 5em; font-weight: 900; color: #e91e63; line-height: 1; }}
            .notice-box {{ background: #fdf6e3; padding: 18px; border-left: 6px solid #ff9800; border-radius: 8px; margin-bottom: 30px; text-align: left; }}
            .details-area {{ text-align: left; margin-bottom: 30px; }}
            .total-row {{ text-align: right; font-weight: 900; font-size: 1.8em; margin-top: 20px; color: #d32f2f; border-top: 2px solid #ddd; padding-top: 15px; }}
            .home-btn {{ display: block; padding: 18px; background: #007bff; color: white !important; text-decoration: none; border-radius: 12px; font-weight: bold; font-size: 1.2em; margin-top: auto; box-shadow: 0 4px 10px rgba(0,123,255,0.3); }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <div class="success-icon">✅</div>
                <h1 class="status-title">{t['order_success']}</h1>
                
                <div class="seq-box">
                    <div class="seq-label">取餐單號 / ORDER NO.</div>
                    <div class="seq-number">#{seq:03d}</div>
                </div>

                <div class="notice-box">
                    <div style="font-weight:bold; color:#856404; font-size:1.3em; margin-bottom:5px;">⚠️ {status_msg}</div>
                    <div style="color:#856404; font-size:1em; line-height:1.4;">{wait_msg}</div>
                </div>

                {delivery_html}

                <div class="details-area">
                    <h3 style="border-bottom:2px solid #eee; padding-bottom:10px; margin-bottom:10px; color:#444;">🧾 {t.get('order_details', '訂單明細')}</h3>
                    {items_html}
                    {fee_row_html}
                    <div class="total-row">{t['total']}: ${total}</div>
                </div>
                
                <p style="color:#999; font-size:0.85em; margin: 20px 0;">下單時間: {time_str}</p>
                <a href="{back_link}" class="home-btn">{back_text}</a>
            </div>
        </div>
    </body>
    </html>
    """
