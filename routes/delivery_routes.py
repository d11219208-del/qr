from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from haversine import haversine, Unit
from database import get_db_connection
import re

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (請確認這是正確的)
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
    """基本清洗：移除郵遞區號與樓層"""
    addr = re.sub(r'^\d{3,5}\s?', '', addr) # 移除開頭郵遞區號
    addr = re.sub(r'(\d+[Ff樓].*)|(B\d+.*)|(地下.*)|(室.*)', '', addr) # 移除樓層
    return addr.strip()

def extract_road_only(addr):
    """
    終極手段：只抓取路名
    輸入: '臺北市中山區長春路348-4號'
    輸出: '臺北市中山區長春路'
    """
    # 抓取直到 "路"、"街"、"大道"、"巷" 為止的字串
    match = re.search(r'.+?[縣市].+?[區鄉鎮市].+?[路街大道巷]', addr)
    if match:
        return match.group(0)
    # 如果上面沒抓到，嘗試抓簡化版 (只要有路/街)
    match_simple = re.search(r'.+?[路街大道]', addr)
    if match_simple:
        return match_simple.group(0)
    return addr # 真的沒招了，回傳原值

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

@delivery_bp.route('/check', methods=['POST'])
def check_address():
    data = request.json
    raw_address = data.get('address', '').strip()
    name = data.get('name')
    phone = data.get('phone')
    
    if not raw_address or not name or not phone:
        return jsonify({'success': False, 'msg': '請填寫完整資訊'})
    
    geolocator = Nominatim(user_agent="tw_food_delivery_final_v4")
    
    location = None
    fallback_level = 0
    
    try:
        # --- 第一層：標準清洗 (去樓層) ---
        search_addr = normalize_address(raw_address)
        # 加上 "台灣" 強制鎖定區域
        query = f"台灣 {search_addr}"
        location = geolocator.geocode(query, timeout=10)
        
        # --- 第二層：去連字號 (348-4 -> 348號) ---
        if not location:
            # 將 "348-4號" 轉為 "348號"，將 "之4" 去掉
            addr_no_dash = re.sub(r'(\d+)[-‐‑]\d+號', r'\1號', search_addr)
            addr_no_dash = re.sub(r'(\d+號)之\d+', r'\1', addr_no_dash)
            
            if addr_no_dash != search_addr:
                print(f"嘗試降級搜尋 (去號): {addr_no_dash}")
                location = geolocator.geocode(f"台灣 {addr_no_dash}", timeout=10)
                fallback_level = 1

        # --- 第三層 (大絕招)：只搜路名 ---
        if not location:
            road_only = extract_road_only(search_addr)
            if road_only != search_addr:
                print(f"嘗試終極搜尋 (只搜路名): {road_only}")
                location = geolocator.geocode(f"台灣 {road_only}", timeout=10)
                fallback_level = 2

        # --- 判斷結果 ---
        if not location:
             return jsonify({
                 'success': False, 
                 'msg': '找不到此地址。請確認您輸入了正確的「行政區」與「路名」。'
             })

        # 計算距離
        user_coords = (location.latitude, location.longitude)
        dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
        
        settings = get_delivery_settings()
        
        # 檢查距離限制
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
            'address': raw_address # 使用者原始輸入 (含樓層)
        }

        # 根據 fallback 等級，給予不同的精準度提示 (可選)
        note = ""
        if fallback_level == 2:
            note = "(以路段中心估算)"

        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': round(dist, 1),
            'shipping_fee': shipping_fee,
            'min_price': settings['min_price'],
            'note': note
        }
        
        session['table_num'] = '外送'
        session.modified = True
        
        return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})

    except (GeocoderTimedOut, GeocoderUnavailable):
        return jsonify({'success': False, 'msg': '地圖連線逾時，請稍後再試'})
    except Exception as e:
        print(f"Geo Error: {e}")
        return jsonify({'success': False, 'msg': '系統發生錯誤'})
