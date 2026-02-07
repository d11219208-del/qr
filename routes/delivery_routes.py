from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
# 請確保已安裝套件: pip install geopy haversine
from geopy.geocoders import Nominatim
from haversine import haversine, Unit
from database import get_db_connection

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (範例：臺北市中山區龍江路164號，請改為你的實際座標)
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
    # 台灣時間 UTC+8
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
    # 獲取前端傳來的資料
    address = data.get('address')
    name = data.get('name')
    phone = data.get('phone')
    
    # 驗證必填欄位
    if not address or not name or not phone:
        return jsonify({'success': False, 'msg': '請填寫完整資訊 (姓名、電話、地址)'})
    
    # 設定 User-Agent 避免被 Nominatim 封鎖
    geolocator = Nominatim(user_agent="tw_food_delivery_app_v1")
    
    try:
        # 加上 "台灣" 提高搜尋準確度
        search_query = f"台灣 {address}"
        location = geolocator.geocode(search_query, timeout=10)
        
        if not location:
            return jsonify({'success': False, 'msg': '找不到此地址，請輸入更完整的路名或地標'})

        user_coords = (location.latitude, location.longitude)
        
        # 計算距離 (單位：公里)
        dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
        
        settings = get_delivery_settings()
        
        # 檢查距離限制
        if dist > settings['max_km']:
            return jsonify({
                'success': False, 
                'msg': f'超出外送範圍 (距離 {dist:.1f}km, 目前限制 {settings["max_km"]}km)'
            })

        # 計算運費
        shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])
        
        # --- 【關鍵修正】 ---
        # 1. 存入 delivery_data (這才是 menu.html 判斷是否為外送的依據)
        session['delivery_data'] = {
            'name': name,
            'phone': phone,
            'address': address
        }

        # 2. 存入 delivery_info (運費與計算資訊)
        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': round(dist, 1),
            'shipping_fee': shipping_fee,
            'min_price': settings['min_price']
        }
        
        # 3. 為了保險起見，也設定 table_number 為外送
        session['table_num'] = '外送'
        
        # 讓 Session 變更生效
        session.modified = True
        
        return jsonify({'success': True, 'redirect': url_for('menu', lang='zh')})

    except Exception as e:
        print(f"Geo Error: {e}")
        # 如果 Geopy 連線超時或失敗，這裡做簡單的錯誤處理
        return jsonify({'success': False, 'msg': '地址定位系統忙碌中，請稍後再試或檢查網路'})
