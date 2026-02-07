from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
# 記得先 pip install geopy haversine
from geopy.geocoders import Nominatim
from haversine import haversine, Unit
from database import get_db_connection

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (範例：臺北市中山區龍江路164號)
RESTAURANT_COORDS = (25.054358, 121.543468)

def get_delivery_settings():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    rows = cur.fetchall()
    conn.close()
    s = {row[0]: row[1] for row in rows}
    return {
        'enabled': s.get('delivery_enabled', '1') == '1',
        'min_price': int(s.get('delivery_min_price', 500)),
        'max_km': float(s.get('delivery_max_km', 5.0)),
        'base_fee': int(s.get('delivery_base_fee', 30)),
        'fee_per_km': int(s.get('delivery_fee_per_km', 10))
    }

# 1. 外送首頁 (顯示表單)
@delivery_bp.route('/setup')
def setup():
    settings = get_delivery_settings()
    if not settings['enabled']:
        return "<script>alert('抱歉，目前暫停外送服務'); window.location.href='/';</script>"

    # 產生日期選項
    date_options = []
    now = datetime.utcnow() + timedelta(hours=8)
    for i in range(3):
        d = now + timedelta(days=i)
        val = d.strftime("%Y-%m-%d")
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        label = f"{d.strftime('%m/%d')} ({weekdays[d.weekday()]})"
        if i == 0: label += " (今天)"
        date_options.append({'value': val, 'label': label})

    return render_template('delivery_setup.html', dates=date_options)

# 2. 檢查地址並計算運費 API
@delivery_bp.route('/check', methods=['POST'])
def check_address():
    data = request.json
    address = data.get('address')
    name = data.get('name')
    phone = data.get('phone')
    
    geolocator = Nominatim(user_agent="tw_food_delivery_app_v1")
    
    try:
        # 加上 "台灣" 提高準確度
        location = geolocator.geocode(f"台灣 {address}")
        
        if not location:
            return jsonify({'success': False, 'msg': '找不到此地址，請輸入更完整的路名或地標'})

        user_coords = (location.latitude, location.longitude)
        dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
        
        settings = get_delivery_settings()
        
        if dist > settings['max_km']:
            return jsonify({
                'success': False, 
                'msg': f'超出外送範圍 (距離 {dist:.1f}km, 目前限制 {settings["max_km"]}km)'
            })

        # 計算運費
        shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])
        
        # --- 關鍵修改：同步存入 Session ---
        # 1. 存入詳細資訊 (供檢查用)
        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': round(dist, 1),
            'shipping_fee': shipping_fee,
            'min_price': settings['min_price']
        }
        
        # 2. 存入 Menu 頁面需要的 key (避免菜單頁抓不到資料)
        session['order_type'] = 'delivery'
        session['table_num'] = '外送'
        session['cust_phone'] = phone
        session['cust_address'] = address # 儲存原始輸入地址
        
        return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})

    except Exception as e:
        print(f"Geo Error: {e}")
        return jsonify({'success': False, 'msg': '地址系統忙碌中，請稍後再試'})
