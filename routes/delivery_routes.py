from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from haversine import haversine, Unit
from database import get_db_connection
import re  # 匯入正規表達式模組

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (請確保這裡是你餐廳的正確座標)
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

def normalize_address(addr):
    """
    第一階段清洗：移除郵遞區號、樓層、室號
    輸入: '104臺北市中山區長春路348-4號1樓'
    輸出: '臺北市中山區長春路348-4號'
    """
    # 1. 移除開頭的郵遞區號 (3到5碼數字)
    addr = re.sub(r'^\d{3,5}\s?', '', addr)
    
    # 2. 移除樓層與室號 (例如: 1樓, 5F, 5f, -1樓, B1, 室)
    # 這裡會把 '1樓' 及其後面的字全部切掉
    addr = re.sub(r'(\d+[Ff樓].*)|(B\d+.*)|(地下.*)|(室.*)', '', addr)
    
    return addr.strip()

def remove_dash_number(addr):
    """
    第二階段清洗：處理 '之' 或 '-' 的門牌
    輸入: '臺北市中山區長春路348-4號' 或 '348號之4'
    輸出: '臺北市中山區長春路348號'
    (OpenStreetMap 常常找不到附號，但主號位置是一樣的)
    """
    # 針對 '348-4號' -> 取 '348號'
    addr = re.sub(r'(\d+)[-‐‑]\d+號', r'\1號', addr)
    # 針對 '348號之4' -> 取 '348號'
    addr = re.sub(r'(\d+號)之\d+', r'\1', addr)
    return addr

# 1. 外送首頁
@delivery_bp.route('/setup')
def setup():
    settings = get_delivery_settings()
    if not settings['enabled']:
        return "<script>alert('抱歉，目前暫停外送服務'); window.location.href='/';</script>"

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
    raw_address = data.get('address', '').strip()
    name = data.get('name')
    phone = data.get('phone')
    
    if not raw_address or not name or not phone:
        return jsonify({'success': False, 'msg': '請填寫完整資訊'})
    
    geolocator = Nominatim(user_agent="tw_food_delivery_v3_fix")
    
    location = None
    
    # --- 智慧搜尋策略 (三階段嘗試) ---
    try:
        # 步驟 1: 基礎清洗 (移除 104, 1樓)
        search_addr_1 = normalize_address(raw_address)
        print(f"嘗試 1: {search_addr_1}")
        location = geolocator.geocode(f"台灣 {search_addr_1}", timeout=10)
        
        # 步驟 2: 如果找不到，移除連字號 (348-4號 -> 348號)
        if not location and ('-' in search_addr_1 or '之' in search_addr_1):
            search_addr_2 = remove_dash_number(search_addr_1)
            print(f"嘗試 2 (移除附號): {search_addr_2}")
            # 如果清洗後地址變了，才搜第二次
            if search_addr_2 != search_addr_1:
                location = geolocator.geocode(f"台灣 {search_addr_2}", timeout=10)
        
        # 步驟 3: (保底) 真的還找不到，試試看只搜「路名」? 
        # (這裡選擇不自動搜路名，因為怕定位到該路頭或路尾，導致運費誤差太大，寧願報錯)

        if not location:
             return jsonify({
                 'success': False, 
                 'msg': '找不到此地址。系統已嘗試移除樓層與附號搜尋仍失敗，請檢查路名或門牌號碼是否正確。'
             })

        # --- 定位成功，開始計算 ---
        user_coords = (location.latitude, location.longitude)
        dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
        
        settings = get_delivery_settings()
        
        if dist > settings['max_km']:
            return jsonify({
                'success': False, 
                'msg': f'超出外送範圍 (距離 {dist:.1f}km, 目前限制 {settings["max_km"]}km)'
            })

        shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])
        
        # 存入 Session
        session['delivery_data'] = {
            'name': name,
            'phone': phone,
            'address': raw_address # 保留使用者原始輸入 (含樓層)
        }

        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': round(dist, 1),
            'shipping_fee': shipping_fee,
            'min_price': settings['min_price']
        }
        
        session['table_num'] = '外送'
        session.modified = True
        
        return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})

    except (GeocoderTimedOut, GeocoderUnavailable):
        return jsonify({'success': False, 'msg': '地圖連線逾時，請稍後再試'})
    except Exception as e:
        print(f"Geo Error: {e}")
        return jsonify({'success': False, 'msg': '系統發生錯誤'})
