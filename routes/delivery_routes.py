from flask import Blueprint, render_template, request, jsonify, session, url_for
from datetime import datetime, timedelta, time
import requests
from haversine import haversine, Unit
from database import get_db_connection
import re
import random

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (10491臺北市中山區龍江路164號)
RESTAURANT_COORDS = (25.0549998, 121.5377779)

def get_delivery_settings():
    """從資料庫讀取外送設定"""
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
        'base_fee': int(s.get('delivery_fee_base', 0)),
        'fee_per_km': int(s.get('delivery_fee_per_km', 10))
    }

def advanced_taiwan_address_cleaner(addr):
    """
    清洗地址：確保格式適合台灣官方地圖搜尋
    """
    if not addr: return ""
    # 統一全形轉半形
    addr = addr.translate(str.maketrans('０１２３４５６７８９－', '0123456789-'))
    # 統一「台」為「臺」 (官方資料庫多用「臺」)
    addr = addr.replace("台", "臺")
    # 移除開頭郵遞區號
    addr = re.sub(r'^\d{3,5}\s?', '', addr)
    # 擷取到「號」為止，避免「圖書二館」或「F樓」干擾
    match = re.search(r'(.+?[路街大道巷弄].+?\d+號)', addr)
    if match:
        return match.group(1).replace(" ", "")
    return addr.strip()

def nlsc_geocode(address):
    """
    終極強化版 NLSC 定位邏輯
    """
    try:
        # 使用 NLSC 的 LocationSearch 介面，這對門牌解析最友善
        url = "https://maps.nlsc.gov.tw/S_Maps/LocationSearch"
        params = {"term": address}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://maps.nlsc.gov.tw/"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        data = response.json()
        
        # NLSC LocationSearch 回傳格式通常是清單
        if isinstance(data, list) and len(data) > 0:
            for item in data:
                # 某些回傳會在標籤內含經緯度，或是直接有 lat/lon 欄位
                lat = item.get('lat') or item.get('y')
                lon = item.get('lon') or item.get('x')
                
                # 如果 LocationSearch 回傳的是地標名稱而非座標，嘗試解析它的 content 欄位
                # 部分回傳會把座標藏在像 "location": "121.5492,25.0617" 這樣的格式裡
                if not lat and 'content' in item:
                    # 嘗試從 content 提取座標 (例如: "... <span lon='121.5' lat='25.0'>")
                    lon_match = re.search(r"lon=['\"]([\d\.]+)['\"]", item['content'])
                    lat_match = re.search(r"lat=['\"]([\d\.]+)['\"]", item['content'])
                    if lon_match and lat_match:
                        lat, lon = lat_match.group(1), lon_match.group(1)

                if lat and lon:
                    return (float(lat), float(lon))
        
        # 備援：嘗試原本的 Search 介面
        search_url = "https://maps.nlsc.gov.tw/S_Maps/Search"
        res_search = requests.get(search_url, params={"q": address, "lang": "zh_TW"}, headers=headers, timeout=10)
        data_search = res_search.json()
        if isinstance(data_search, list) and len(data_search) > 0:
            res = data_search[0]
            lat = res.get('lat') or res.get('y')
            lon = res.get('lon') or res.get('x')
            if lat and lon:
                return (float(lat), float(lon))

    except Exception as e:
        print(f"NLSC API Error: {e}")
    return None

def generate_time_slots(base_date):
    """產生單日可外送時段"""
    slots = []
    start_time = time(10, 30)
    end_time = time(20, 30)
    block_start = time(11, 30)
    block_end = time(13, 30)
    
    current_dt = datetime.combine(base_date, start_time)
    end_dt = datetime.combine(base_date, end_time)
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    while current_dt <= end_dt:
        t = current_dt.time()
        if base_date == now_tw.date() and current_dt < (now_tw + timedelta(minutes=30)):
            current_dt += timedelta(minutes=30)
            continue
        if not (t >= block_start and t <= block_end):
            slots.append(current_dt.strftime("%H:%M"))
        current_dt += timedelta(minutes=30)
    return slots

@delivery_bp.route('/setup')
def setup():
    settings = get_delivery_settings()
    if not settings['enabled']:
        return "<script>alert('抱歉，目前暫停外送服務'); window.location.href='/';</script>"

    date_options = []
    now = datetime.utcnow() + timedelta(hours=8)
    for i in range(3):
        d = (now + timedelta(days=i)).date()
        slots = generate_time_slots(d)
        if slots:
            date_options.append({
                'value': d.strftime("%Y-%m-%d"), 
                'label': f"{d.strftime('%m/%d')} (今天)" if i==0 else d.strftime('%m/%d'),
                'slots': slots
            })
    return render_template('delivery_setup.html', dates=date_options)

@delivery_bp.route('/check', methods=['POST'])
def check_address():
    data = request.json
    raw_address = data.get('address', '').strip()
    name = data.get('name')
    phone = data.get('phone')
    delivery_date = data.get('date')
    delivery_time = data.get('time')
    
    if not all([raw_address, name, phone, delivery_date, delivery_time]):
        return jsonify({'success': False, 'msg': '請填寫完整資訊'})
    
    settings = get_delivery_settings()
    scheduled_for = f"{delivery_date} {delivery_time}"

    # 1. 精確清洗地址 (包含 台 -> 臺 的轉換)
    search_target = advanced_taiwan_address_cleaner(raw_address)
    
    # 2. 定位
    user_coords = nlsc_geocode(search_target)
    
    # 3. 如果第一次失敗，補上「臺北市」再試一次
    if not user_coords and "臺北" not in search_target:
        user_coords = nlsc_geocode("臺北市" + search_target)

    if user_coords:
        # 4. 計算距離
        dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
        
        if dist > settings['max_km']:
            return jsonify({
                'success': False, 
                'msg': f'超出範圍 (距離 {dist:.1f}km, 限制 {settings["max_km"]}km)'
            })

        shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])

        session['delivery_data'] = {
            'name': name, 'phone': phone, 'address': raw_address, 'scheduled_for': scheduled_for
        }
        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': round(dist, 1),
            'shipping_fee': shipping_fee,
            'min_price': settings['min_price'],
            'note': ""
        }
        session['table_num'] = '外送'
        session.modified = True
        
        return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})
    
    else:
        # 最終失敗：轉人工確認
        session['delivery_data'] = {'name': name, 'phone': phone, 'address': raw_address, 'scheduled_for': scheduled_for}
        session['delivery_info'] = {
            'is_delivery': True, 'distance_km': 0, 'shipping_fee': settings['base_fee'],
            'min_price': settings['min_price'], 'note': "⚠️ 地址解析忙碌，請點餐後由專人電話核實運費"
        }
        session.modified = True
        return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})
