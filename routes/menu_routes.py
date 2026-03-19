from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from database import get_db_connection
from translations import load_translations
from datetime import timedelta, datetime
import json
import traceback

menu_bp = Blueprint('menu', __name__)

# ==========================================
# 1. 共用函數：讀取產品與設定
# ==========================================
def get_menu_data():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 讀取所有設定 (包含 shop_open, delivery_enabled, delivery_min_price 等)
    cur.execute("SELECT key, value FROM settings")
    settings_rows = cur.fetchall()
    settings = {row[0]: row[1] for row in settings_rows}
    
    # 確保 delivery_min_price 存在於設定中
    if 'delivery_min_price' not in settings:
        settings['delivery_min_price'] = '0'  # 若資料庫未設定，預設為 0
    
    # 讀取產品 (包含多語系欄位)
    cur.execute("""
        SELECT id, name, price, category, image_url, is_available, custom_options, sort_order,
                name_en, name_jp, name_kr, 
                custom_options_en, custom_options_jp, custom_options_kr, 
                print_category, 
                category_en, category_jp, category_kr
        FROM products 
        ORDER BY sort_order ASC, id ASC
    """)
    products = cur.fetchall()
    cur.close()
    conn.close()

    p_list = []
    for p in products:
        # 處理自定義選項字串轉陣列
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
# 2. 共用函數：處理訂單提交 (核心邏輯)
# ==========================================
def process_order_submission(request, order_type_override=None):
    display_lang = request.form.get('lang_input', 'zh')
    
    # --- Debug ---
    print(f"DEBUG: Processing Order. OverrideType={order_type_override}")

    conn = get_db_connection()
    conn.autocommit = False 
    cur = conn.cursor()

    try:
        # --- A. 檢查店鋪狀態 ---
        cur.execute("SELECT key, value FROM settings WHERE key IN ('shop_open', 'delivery_enabled')")
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
        
        # 決定訂單類型 (優先順序：程式指定 > 表單指定 > 預設 dine_in)
        order_type = order_type_override if order_type_override else request.form.get('order_type', 'dine_in')
        
        # 如果是外送單，但後台關閉了外送 -> 阻擋
        if order_type == 'delivery' and not delivery_enabled:
             return "Delivery Service is currently disabled / 外送服務目前關閉中", 403

        # --- C. 處理編輯模式：抓取舊訂單資料作為後備 ---
        db_old_data = {}
        if old_order_id:
            cur.execute("""
                SELECT lang, order_type, delivery_info, delivery_fee, 
                       customer_name, customer_phone, customer_address, scheduled_for, table_number,
                       tax_id, carrier_type, carrier_num
                FROM orders WHERE id=%s
            """, (old_order_id,))
            row = cur.fetchone()
            if row:
                db_old_data = {
                    'lang': row[0],
                    'order_type': row[1],
                    'delivery_info': row[2],
                    'delivery_fee': row[3],
                    'customer_name': row[4],
                    'customer_phone': row[5],
                    'customer_address': row[6],
                    'scheduled_for': row[7],
                    'table_number': row[8],
                    'tax_id': row[9],
                    'carrier_type': row[10],
                    'carrier_num': row[11]
                }
                final_lang = db_old_data['lang']

        # 【發票資訊】優先取表單提交的值，若無（例如編輯模式未改動）則取舊訂單值
        # 注意：載具類別與號碼若為空字串，我們依然寫入空字串或 None
        tax_id = request.form.get('tax_id')
        if tax_id is None: tax_id = db_old_data.get('tax_id') or ''
        
        carrier_type = request.form.get('carrier_type')
        if carrier_type is None: carrier_type = db_old_data.get('carrier_type') or ''
        
        carrier_num = request.form.get('carrier_num')
        if carrier_num is None: carrier_num = db_old_data.get('carrier_num') or ''

        # --- D. 處理外送與客戶資訊 (優先級：Form > Session > DB Old Order) ---
        sess_data = session.get('delivery_data', {})
        sess_info = session.get('delivery_info', {})

        customer_name = (request.form.get('customer_name') or request.form.get('name') or 
                         sess_data.get('name') or db_old_data.get('customer_name') or '')
        
        customer_phone = (request.form.get('customer_phone') or request.form.get('phone') or 
                          sess_data.get('phone') or db_old_data.get('customer_phone') or '')
        
        customer_address = (request.form.get('delivery_address') or request.form.get('address') or 
                            sess_data.get('address') or db_old_data.get('customer_address') or '')
        
        note = request.form.get('delivery_note') or request.form.get('note') or sess_data.get('note') or ''
        
        scheduled_for = (request.form.get('scheduled_for') or sess_data.get('scheduled_for') or 
                         db_old_data.get('scheduled_for') or '')
        
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
            
            if sess_fee is not None:
                delivery_fee = int(float(sess_fee))
            elif form_fee:
                delivery_fee = int(float(form_fee))
            elif db_old_data.get('delivery_fee'):
                delivery_fee = db_old_data['delivery_fee']
            else:
                delivery_fee = 0

            old_delivery_info = {}
            if db_old_data.get('delivery_info'):
                try:
                    old_delivery_info = json.loads(db_old_data['delivery_info'])
                except:
                    old_delivery_info = {}

            delivery_info_dict = {
                'name': customer_name,
                'phone': customer_phone,
                'address': customer_address, 
                'scheduled_for': scheduled_for,
                'distance_km': sess_info.get('distance_km') or request.form.get('distance_km') or old_delivery_info.get('distance_km'),
                'note': note or old_delivery_info.get('note'),
                'shipping_fee': delivery_fee
            }
            delivery_info_json_str = json.dumps(delivery_info_dict, ensure_ascii=False)
            table_number = "外送"
        else:
            delivery_fee = 0
            if raw_table_number and raw_table_number.strip():
                table_number = raw_table_number
                order_type = 'dine_in'
            elif db_old_data.get('table_number') and db_old_data['table_number'] not in ["外送", "外帶"]:
                table_number = db_old_data['table_number']
                order_type = 'dine_in'
            else:
                table_number = "外帶"
                order_type = 'takeout'

        if not cart_json or cart_json == '[]': 
            return "Empty Cart", 400

        # --- E. 計算總金額與產生訂單內容 ---
        cart_items = json.loads(cart_json)
        total_price = 0
        display_list = []

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

        # --- F. 寫入資料庫 ---
        print("接收到的表單資料:", request.form)
        # 確保併發環境下 daily_seq 正確
        cur.execute("LOCK TABLE orders IN SHARE ROW EXCLUSIVE MODE")

        cur.execute("""
            INSERT INTO orders (
                table_number, items, total_price, lang, 
                daily_seq, 
                content_json, need_receipt, created_at,
                order_type, delivery_info, delivery_fee,
                customer_name, customer_phone, customer_address, scheduled_for,
                tax_id, carrier_type, carrier_num
            )
            VALUES (
                %s, %s, %s, %s, 
                (SELECT COALESCE(MAX(daily_seq), 0) + 1 FROM orders WHERE created_at >= CURRENT_DATE), 
                %s, %s, NOW(),
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            )
            RETURNING id, daily_seq
        """, (
            table_number, items_str, total_price, final_lang, 
            cart_json, need_receipt, 
            order_type, delivery_info_json_str, delivery_fee,
            customer_name, customer_phone, customer_address, scheduled_for,
            tax_id, carrier_type, carrier_num
        ))

        res = cur.fetchone()
        oid = res[0]
        
        if old_order_id:
            cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (old_order_id,))
        
        conn.commit()
        
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


# ==========================================
# 3. 路由定義
# ==========================================

@menu_bp.route('/')
def index():
    table_num = request.args.get('table', '')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings WHERE key IN ('shop_open', 'delivery_enabled')")
    settings = dict(cur.fetchall())
    conn.close()
    
    shop_open = settings.get('shop_open', '1') == '1'
    delivery_enabled = settings.get('delivery_enabled', '1') == '1'

    session.clear()
    
    return render_template('index.html', 
                           table_num=table_num, 
                           shop_open=shop_open, 
                           delivery_enabled=delivery_enabled)


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
                           current_mode='dine_in',
                           is_delivery_mode=False)


@menu_bp.route('/delivery', methods=['GET', 'POST'])
def delivery_menu():
    if request.method == 'POST':
        return process_order_submission(request, order_type_override='delivery')
    
    settings, products = get_menu_data()
    
    if settings.get('delivery_enabled', '1') != '1':
        return redirect(url_for('menu.index'))

    display_lang = request.args.get('lang', 'zh')
    t_all = load_translations()
    t = t_all.get(display_lang, t_all['zh'])
    
    session_delivery = session.get('delivery_data', {})
    
    return render_template('menu.html', 
                           products=products, texts=t, table_num="外送", 
                           display_lang=display_lang, order_lang=display_lang, 
                           preload_cart="null", edit_oid=None, config=settings,
                           current_mode='delivery',
                           is_delivery_mode=True,
                           session_delivery=session_delivery)
    


# --- 下單成功頁面 ---
@menu_bp.route('/success')
def order_success():
    oid = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # ==========================================
    # 1. 讀取訂單詳細資料 (加入發票欄位)
    # ==========================================
    cur.execute("""
        SELECT daily_seq, content_json, total_price, created_at, 
               order_type, delivery_info, delivery_fee,
               customer_name, customer_phone, customer_address, scheduled_for,
               table_number, tax_id, carrier_type, carrier_num
        FROM orders WHERE id=%s
    """, (oid,))
    row = cur.fetchone()
    
    # ==========================================
    # 2. 讀取所有產品的客製化選項
    # ==========================================
    cur.execute("""
        SELECT name, custom_options, custom_options_en, custom_options_jp, custom_options_kr 
        FROM products
    """)
    product_map = {}
    for p_row in cur.fetchall():
        p_name = p_row[0]
        
        def split_opts(opt_str):
            if not opt_str: return []
            return [o.strip() for o in opt_str.split(',') if o.strip()]
        
        product_map[p_name] = {
            'zh': split_opts(p_row[1]),
            'en': split_opts(p_row[2]),
            'jp': split_opts(p_row[3]),
            'kr': split_opts(p_row[4])
        }
        
    cur.close()
    conn.close()
    
    if not row: return "Order Not Found", 404
    
    # 解構變數包含發票資訊
    seq, json_str, total, created_at, order_type, delivery_info_json, delivery_fee, c_name, c_phone, c_addr, c_time, table_num_db, tax_id, carrier_type, carrier_num = row
    
    type_is_delivery = (str(order_type or '').strip().lower() == 'delivery')
    table_is_delivery = (str(table_num_db or '').strip() == '外送')
    is_delivery = type_is_delivery or table_is_delivery
    
    delivery_info_dict = {}
    if delivery_info_json:
        try:
            delivery_info_dict = json.loads(delivery_info_json)
        except:
            delivery_info_dict = {}

    d_name = c_name if c_name else delivery_info_dict.get('name', 'N/A')
    d_phone = c_phone if c_phone else delivery_info_dict.get('phone', 'N/A')
    d_addr = c_addr if c_addr else delivery_info_dict.get('address', 'N/A')
    d_note = delivery_info_dict.get('note', '')
    
    d_scheduled = ""
    if c_time:
        d_scheduled = str(c_time)
    elif delivery_info_dict.get('scheduled_for'):
        d_scheduled = str(delivery_info_dict.get('scheduled_for'))
        
    if d_scheduled and len(d_scheduled) > 16:
        d_scheduled = d_scheduled[:16]

    # 選項動態翻譯函式
    def translate_option(p_name, opt_str, target_lang):
        if p_name not in product_map:
            return opt_str
        p_data = product_map[p_name]
        found_idx = -1
        for l in ['zh', 'en', 'jp', 'kr']:
            if opt_str in p_data[l]:
                found_idx = p_data[l].index(opt_str)
                break
        if found_idx != -1:
            target_list = p_data.get(target_lang, [])
            if found_idx < len(target_list):
                return target_list[found_idx]
        return opt_str

    items = json.loads(json_str) if json_str else []
    items_html = ""
    
    for i in items:
        row_total = int(float(i['unit_price'])) * int(float(i['qty']))
        name_zh = i.get('name_zh', i.get('name', 'Product'))
        d_name_prod = i.get(f'name_{lang}', name_zh)
        
        raw_ops = i.get(f'options_{lang}') or i.get('options_zh') or i.get('options') or []
        if isinstance(raw_ops, str):
            raw_ops = [raw_ops]
            
        translated_ops = []
        for opt in raw_ops:
            translated_ops.append(translate_option(name_zh, str(opt).strip(), lang))
            
        opt_str = f"<div class='item-options'>└ {', '.join(translated_ops)}</div>" if translated_ops else ""
        
        items_html += f"""
        <div class="item-row">
            <div class="item-info">
                <div class="item-name">{d_name_prod} <span class="item-qty">x{i['qty']}</span></div>
                {opt_str}
            </div>
            <div class="item-price">${row_total}</div>
        </div>
        """
    
    delivery_html = ""
    fee_row_html = ""

    if is_delivery:
        fee_label = "Delivery Fee" if lang == 'en' else "運費"
        fee_row_html = f"""
        <div class="fee-row">
            <div>🛵 {fee_label}</div>
            <div>${delivery_fee}</div>
        </div>
        """
        time_display = f"<div class='d-time'><b>📅 預約時間:</b> {d_scheduled}</div>" if d_scheduled else ""
        delivery_html = f"""
        <div class="delivery-box">
            <h4>🛵 外送資訊 / Delivery Info</h4>
            {time_display}
            <div class="d-info-row"><b>姓名:</b> {d_name}</div>
            <div class="d-info-row"><b>電話:</b> <a href="tel:{d_phone}">{d_phone}</a></div>
            <div class="d-info-row"><b>地址:</b> {d_addr}</div>
            <div class="d-note"><b>備註:</b> {d_note if d_note else '無'}</div>
        </div>
        """
        status_msg = "Order Received / 訂單已收到"
        wait_msg = "Please wait for confirmation call.<br>請留意電話，我們將與您確認餐點與外送時間。"
    else:
        status_msg = t.get('pay_at_counter', '請至櫃檯結帳')
        wait_msg = t.get('kitchen_prep', 'Kitchen is preparing your meal.<br>廚房正在為您準備餐點。')

    # 建立發票資訊的 HTML 區塊
    invoice_html = ""
    if tax_id or carrier_type:
        c_type_name = "綠界會員載具" if carrier_type == '1' else ("自然人憑證" if carrier_type == '2' else ("手機條碼" if carrier_type == '3' else "無"))
        invoice_html = f"""
        <div class="delivery-box" style="background: #F3F4F6; border-color: #D1D5DB; color: #374151;">
            <h4 style="color: #374151; border-bottom-color: #D1D5DB; margin-bottom: 8px;">🧾 發票資訊 / Invoice Info</h4>
            {f'<div class="d-info-row"><b>統一編號:</b> {tax_id}</div>' if tax_id else ''}
            {f'<div class="d-info-row"><b>載具類別:</b> {c_type_name}</div>' if carrier_type else ''}
            {f'<div class="d-info-row"><b>載具條碼:</b> {carrier_num}</div>' if carrier_num else ''}
        </div>
        """

    tw_time = created_at + timedelta(hours=8)
    time_str = tw_time.strftime('%Y-%m-%d %H:%M:%S')

    back_link = url_for('menu.index', lang=lang)
    back_text = "Back to Menu / 返回菜單"

    return f"""
    <!DOCTYPE html>
    <html lang="{lang}">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Order Success</title>
        <style>
            :root {{
                --bg-color: #F7F9FA;
                --card-bg: transparent; 
                --text-main: #1F2937;
                --text-muted: #6B7280;
                --success: #10B981;
                --primary: #FF5A5F;
                --warning-bg: #FEF3C7;
                --warning-text: #B45309;
                --border: #E5E7EB;
                --delivery-bg: #EFF6FF;
                --delivery-border: #BFDBFE;
                --delivery-text: #1E3A8A;
            }}
            
            body {{ 
                margin: 0; padding: 0; 
                font-family: 'Inter', '-apple-system', 'BlinkMacSystemFont', 'Microsoft JhengHei', sans-serif; 
                color: var(--text-main);
                -webkit-font-smoothing: antialiased;
            }}

            body::before {{
                content: "";
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background: url('https://i.ibb.co/20MXKCFk/1622093786-81a879fc1f1b41afed71696a0e45d95f.png') no-repeat center center;
                background-size: contain; 
                opacity: 0.6; 
                z-index: -1; 
            }}

            .text-outline {{
                text-shadow: 
                    -1px -1px 0 #FFF,
                     0   -1px 0 #FFF,
                     1px -1px 0 #FFF,
                     1px  0   0 #FFF,
                     1px  1px 0 #FFF,
                     0    1px 0 #FFF,
                    -1px  1px 0 #FFF,
                    -1px  0   0 #FFF;
            }}
            
            .container {{ 
                min-height: 100vh; 
                display: flex; flex-direction: column; 
                padding: 20px; box-sizing: border-box; 
                max-width: 600px; margin: 0 auto; 
            }}
            
            .card {{ 
                background: var(--card-bg); 
                flex-grow: 1; border-radius: 20px; 
                box-shadow: 0 4px 24px rgba(0,0,0,0.06); 
                padding: 30px 20px; text-align: center; 
                display: flex; flex-direction: column; 
            }}
            
            .header-sec {{ margin-bottom: 24px; }}
            .success-icon {{ 
                width: 64px; height: 64px; 
                background: #D1FAE5; color: var(--success); 
                border-radius: 50%; display: flex; 
                align-items: center; justify-content: center; 
                font-size: 32px; margin: 0 auto 16px auto; 
            }}
            .status-title {{ margin: 0; font-size: 1.6em; font-weight: 900; color: var(--text-main); }}
            
            .seq-box {{ 
                background: #FFF1F2; border-radius: 16px; 
                padding: 24px 16px; margin-bottom: 24px; 
            }}
            .seq-label {{ font-size: 0.9em; color: var(--primary); font-weight: bold; letter-spacing: 1px; margin-bottom: 8px; }}
            .seq-number {{ font-size: 4.5em; font-weight: 900; color: var(--primary); line-height: 1; }}
            
            .notice-box {{ 
                background: var(--warning-bg); padding: 16px; 
                border-radius: 12px; margin-bottom: 24px; text-align: left; 
            }}
            .notice-title {{ font-weight: 900; color: var(--warning-text); font-size: 1.1em; margin-bottom: 4px; }}
            .notice-desc {{ color: var(--warning-text); font-size: 0.95em; line-height: 1.5; }}
            
            .delivery-box {{ 
                background: var(--delivery-bg); border: 1px solid var(--delivery-border); 
                padding: 16px; border-radius: 12px; margin-bottom: 24px; 
                text-align: left; color: var(--delivery-text);
            }}
            .delivery-box h4 {{ margin: 0 0 12px 0; border-bottom: 1px dashed var(--delivery-border); padding-bottom: 8px; font-size: 1.1em; }}
            .d-time {{ color: #D97706; font-size: 1.05em; margin-bottom: 8px; }}
            .d-info-row {{ margin-bottom: 6px; font-size: 0.95em; }}
            .d-info-row a {{ color: var(--delivery-text); text-decoration: none; font-weight: bold; }}
            .d-note {{ font-size: 0.9em; margin-top: 10px; background: transparent; padding: 8px; border-radius: 8px; border: 1px solid var(--delivery-border); }}
            
            .details-area {{ text-align: left; margin-bottom: 30px; }}
            .details-title {{ 
                font-size: 1.1em; font-weight: 900; color: var(--text-main); 
                border-bottom: 2px solid var(--border); 
                padding-bottom: 10px; margin: 0 0 16px 0; 
            }}
            
            .item-row {{ 
                display: flex; justify-content: space-between; 
                align-items: flex-start; padding: 12px 0; 
                border-bottom: 1px solid var(--border); 
            }}
            .item-info {{ flex: 1; padding-right: 12px; }}
            .item-name {{ font-size: 1.05em; font-weight: bold; color: var(--text-main); line-height: 1.3; }}
            .item-qty {{ color: var(--text-muted); font-weight: normal; margin-left: 4px; font-size: 0.9em; }}
            .item-options {{ color: var(--text-muted); font-size: 0.85em; margin-top: 4px; }}
            .item-price {{ font-weight: bold; font-size: 1.1em; white-space: nowrap; color: var(--text-main); }}
            
            .fee-row {{ 
                display: flex; justify-content: space-between; align-items: center; 
                padding: 16px 0 0 0; color: #3B82F6; font-weight: bold; font-size: 1.05em; 
            }}
            .total-row {{ 
                display: flex; justify-content: space-between; align-items: center; 
                margin-top: 16px; padding-top: 16px; border-top: 2px solid var(--border); 
            }}
            .total-label {{ font-size: 1.2em; font-weight: bold; }}
            .total-price {{ font-size: 1.8em; font-weight: 900; color: var(--primary); }}
            
            .order-time {{ color: var(--text-muted); font-size: 0.85em; margin: 24px 0; text-align: center; }}
            .home-btn {{ 
                display: block; padding: 16px; background: var(--text-main); 
                color: white; text-decoration: none; border-radius: 12px; 
                font-weight: bold; font-size: 1.1em; margin-top: auto; 
                transition: transform 0.1s;
                text-shadow: none; 
            }}
            .home-btn:active {{ transform: scale(0.98); }}
        </style>
    </head>
    <body class="text-outline">
        <div class="container">
            <div class="card">
                
                <div class="header-sec">
                    <div class="success-icon">✓</div>
                    <h1 class="status-title">{t.get('order_success', '下單成功')}</h1>
                </div>
                
                <div class="seq-box">
                    <div class="seq-label">取餐單號 / ORDER NO.</div>
                    <div class="seq-number">#{seq:03d}</div>
                </div>

                <div class="notice-box">
                    <div class="notice-title">⚠️ {status_msg}</div>
                    <div class="notice-desc">{wait_msg}</div>
                </div>

                {delivery_html}
                
                {invoice_html}

                <div class="details-area">
                    <h3 class="details-title">🧾 {t.get('order_details', '訂單明細')}</h3>
                    
                    {items_html}
                    {fee_row_html}
                    
                    <div class="total-row">
                        <div class="total-label">{t.get('total', 'Total')}</div>
                        <div class="total-price">${total}</div>
                    </div>
                </div>
                
                <div class="order-time">下單時間: {time_str}</div>
                
                <a href="{back_link}" class="home-btn">{back_text}</a>
            </div>
        </div>
    </body>
    </html>
    """
