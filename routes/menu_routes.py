from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from database import get_db_connection
from translations import load_translations
from datetime import timedelta, datetime
import json

menu_bp = Blueprint('menu', __name__)

# --- 語言選擇首頁 ---
@menu_bp.route('/')
def index():
    table_num = request.args.get('table', '')
    return render_template('index.html', table_num=table_num)

# --- 點餐頁面 (包含外送邏輯) ---
@menu_bp.route('/menu', methods=['GET', 'POST'])
def menu():
    display_lang = request.args.get('lang', 'zh')
    t_all = load_translations()
    t = t_all.get(display_lang, t_all['zh'])
    
    conn = get_db_connection()
    # 重要：關閉自動提交，以便手動控制 Transaction 與 Lock
    conn.autocommit = False 
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            # 1. 基本欄位
            table_number = request.form.get('table_number')
            cart_json = request.form.get('cart_data')
            need_receipt = request.form.get('need_receipt') == 'on'
            final_lang = request.form.get('lang_input', 'zh')
            old_order_id = request.form.get('old_order_id')
            
            # 2. 外送欄位 (新增)
            order_type = request.form.get('order_type', 'dine_in') # 預設內用
            delivery_fee = int(float(request.form.get('delivery_fee', 0)))
            
            delivery_info = None
            if order_type == 'delivery':
                # 組合外送資訊 JSON
                delivery_info = json.dumps({
                    'name': request.form.get('customer_name'),
                    'phone': request.form.get('customer_phone'),
                    'address': request.form.get('delivery_address'),
                    'distance_km': request.form.get('distance_km'),
                    'note': request.form.get('delivery_note')
                }, ensure_ascii=False)
                # 外送單沒有桌號，設為 None 或特定標示
                table_number = None 

            if not cart_json or cart_json == '[]': 
                return "Empty Cart", 400

            cart_items = json.loads(cart_json)
            total_price = 0
            display_list = []

            # 如果是修改訂單，保持原本的語言
            if old_order_id:
                cur.execute("SELECT lang FROM orders WHERE id=%s", (old_order_id,))
                orig_res = cur.fetchone()
                if orig_res: final_lang = orig_res[0] 

            # 3. 計算餐點總額
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
            
            # --- [新增邏輯] 檢查外送低消 ---
            # 這裡檢查的是尚未加上運費的「餐點總金額」
            if order_type == 'delivery' and total_price < 1000:
                conn.rollback() # 雖然還沒寫入，但保持好習慣
                return "外送訂單餐點金額需滿 $1000 元 (不含運費) 才能下單。", 400
            # -----------------------------

            # 4. 加入運費到總金額 (如果是外送)
            total_price += delivery_fee

            # --- 核心修正：利用資料庫鎖定解決並發流水號重複問題 ---
            
            # 鎖定資料表
            cur.execute("LOCK TABLE orders IN SHARE ROW EXCLUSIVE MODE")

            # 插入資料 (新增 order_type, delivery_info, delivery_fee)
            cur.execute("""
                INSERT INTO orders (
                    table_number, items, total_price, lang, 
                    daily_seq, 
                    content_json, need_receipt, created_at,
                    order_type, delivery_info, delivery_fee
                )
                VALUES (
                    %s, %s, %s, %s, 
                    (SELECT COALESCE(MAX(daily_seq), 0) + 1 FROM orders WHERE created_at >= CURRENT_DATE), 
                    %s, %s, NOW(),
                    %s, %s, %s
                )
                RETURNING id, daily_seq
            """, (
                table_number, items_str, total_price, final_lang, 
                cart_json, need_receipt, 
                order_type, delivery_info, delivery_fee
            ))

            res = cur.fetchone()
            oid = res[0]
            
            # 如果是編輯訂單，將舊單作廢
            if old_order_id:
                cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (old_order_id,))
            
            conn.commit()
            
            if old_order_id: 
                return f"<script>localStorage.removeItem('cart_cache'); alert('訂單已更新'); if(window.opener) window.opener.location.reload(); window.close();</script>"
            
            return redirect(url_for('menu.order_success', order_id=oid, lang=final_lang))
            
        except Exception as e:
            conn.rollback()
            return f"Order Failed: {e}", 500
        finally:
            cur.close()
            conn.close()

    # --- GET 邏輯 ---
    url_table = request.args.get('table', '')
    edit_oid = request.args.get('edit_oid')
    preload_cart = "null" 
    order_lang = display_lang 

    if edit_oid:
        cur.execute("SELECT table_number, content_json, lang FROM orders WHERE id=%s", (edit_oid,))
        old_data = cur.fetchone()
        if old_data:
            if not url_table: url_table = old_data[0]
            preload_cart = old_data[1] 
            order_lang = old_data[2] if old_data[2] else 'zh'

    # 讀取產品與設定 (設定用來判斷外送是否開啟)
    cur.execute("SELECT key, value FROM settings")
    settings = dict(cur.fetchall())
    
    cur.execute("""
        SELECT id, name, price, category, image_url, is_available, custom_options, sort_order,
               name_en, name_jp, name_kr, custom_options_en, custom_options_jp, custom_options_kr, 
               print_category, category_en, category_jp, category_kr
        FROM products ORDER BY sort_order ASC, id ASC
    """)
    products = cur.fetchall()
    cur.close(); conn.close()

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
    
    # 將 settings 傳入 template
    return render_template('menu.html', products=p_list, texts=t, table_num=url_table, 
                           display_lang=display_lang, order_lang=order_lang, 
                           preload_cart=preload_cart, edit_oid=edit_oid, config=settings)

# --- 下單成功頁面 (包含外送資訊顯示) ---
@menu_bp.route('/success')
def order_success():
    oid = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    
    conn = get_db_connection(); cur = conn.cursor()
    # 讀取 order_type, delivery_info, delivery_fee
    cur.execute("""
        SELECT daily_seq, content_json, total_price, created_at, order_type, delivery_info, delivery_fee 
        FROM orders WHERE id=%s
    """, (oid,))
    row = cur.fetchone()
    cur.close(); conn.close()
    
    if not row: return "Order Not Found", 404
    
    seq, json_str, total, created_at, order_type, delivery_info_json, delivery_fee = row
    tw_time = created_at + timedelta(hours=8)
    time_str = tw_time.strftime('%Y-%m-%d %H:%M:%S')
    items = json.loads(json_str) if json_str else []
    
    # 解析外送資訊
    is_delivery = (order_type == 'delivery')
    delivery_info = json.loads(delivery_info_json) if delivery_info_json else {}
    
    # 產生餐點 HTML
    items_html = ""
    # 餐點小計
    subtotal = 0
    
    for i in items:
        price = i['unit_price'] * i['qty']
        subtotal += price
        
        d_name = i.get(f'name_{lang}', i.get('name_zh', 'Product'))
        ops = i.get(f'options_{lang}', i.get('options_zh', []))
        opt_str = f"<br><small style='color:#777; font-size:0.9em;'>└ {', '.join(ops)}</small>" if ops else ""
        
        items_html += f"""
        <div style='display:flex; justify-content:space-between; align-items: flex-start; border-bottom:1px solid #eee; padding:15px 0;'>
            <div style="text-align: left; padding-right: 10px;">
                <div style="font-size:1.1em; font-weight:bold; color:#333;">{d_name} <span style="color:#888; font-weight:normal;">x{i['qty']}</span></div>
                {opt_str}
            </div>
            <div style="font-weight:bold; font-size:1.1em; white-space:nowrap;">${price}</div>
        </div>
        """
    
    # 產生外送資訊 HTML
    delivery_html = ""
    fee_row_html = ""
    
    if is_delivery:
        # 運費欄位
        fee_label = "Delivery Fee" if lang == 'en' else "運費"
        fee_row_html = f"""
        <div style='display:flex; justify-content:space-between; align-items: center; border-bottom:2px solid #333; padding:15px 0; color:#007bff;'>
            <div style="font-weight:bold;">🛵 {fee_label}</div>
            <div style="font-weight:bold; font-size:1.1em;">${delivery_fee}</div>
        </div>
        """
        
        # 客戶資訊欄位
        d_name = delivery_info.get('name', '')
        d_phone = delivery_info.get('phone', '')
        d_addr = delivery_info.get('address', '')
        d_note = delivery_info.get('note', '')
        
        delivery_html = f"""
        <div style="background:#e3f2fd; padding:15px; border-radius:10px; margin-bottom:20px; text-align:left; border:1px solid #90caf9;">
            <h4 style="margin:0 0 10px 0; color:#1565c0;">🛵 Delivery Info / 外送資訊</h4>
            <div style="margin-bottom:5px;"><b>Name:</b> {d_name}</div>
            <div style="margin-bottom:5px;"><b>Phone:</b> <a href="tel:{d_phone}">{d_phone}</a></div>
            <div style="margin-bottom:5px;"><b>Address:</b> {d_addr}</div>
            <div style="font-size:0.9em; color:#555;"><b>Note:</b> {d_note}</div>
        </div>
        """
        
        # 外送的提示訊息
        status_msg = "Order Received / 訂單已收到"
        wait_msg = "Please wait for confirmation call.<br>請留意電話，我們可能與您確認。"
    else:
        # 內用的提示訊息
        status_msg = t.get('pay_at_counter', '請至櫃檯結帳')
        wait_msg = t['kitchen_prep']

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
                <a href="{url_for('menu.index')}?lang={lang}" class="home-btn">回首頁 / Back to Menu</a>
            </div>
        </div>
    </body>
    </html>
    """
