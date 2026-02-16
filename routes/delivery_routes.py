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
    # 移除開頭郵遞區號
    addr = re.sub(r'^\d{3,5}\s?', '', addr)
    # 擷取到「號」為止
    match = re.search(r'(.+?[路街大道巷弄].+?\d+號)', addr)
    if match:
        return match.group(1).replace(" ", "")
    return addr.strip()

def nlsc_geocode(address):
    """
    強化版 NLSC 定位：支援多種回傳欄位格式 (lat/lon, y/x)
    """
    try:
        url = "https://maps.nlsc.gov.tw/S_Maps/Search"
        params = {"lang": "zh_TW", "q": address}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://maps.nlsc.gov.tw/"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        data = response.json()
        
        # 遍歷結果，尋找有效的經緯度
        if isinstance(data, list) and len(data) > 0:
            for res in data:
                # 優先抓取 WGS84 座標格式 (y 為緯度, x 為經度)
                lat = res.get('lat') or res.get('y') or res.get('LAT')
                lon = res.get('lon') or res.get('x') or res.get('LON')
                
                if lat and lon:
                    try:
                        return (float(lat), float(lon))
                    except ValueError:
                        continue
        print(f"NLSC 找不到座標: {address}")
    except Exception as e:
        print(f"NLSC API 連線失敗: {e}")
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

    # 1. 精確清洗地址
    search_target = advanced_taiwan_address_cleaner(raw_address)
    
    # 2. 定位 (優先搜尋完整地址)
    user_coords = nlsc_geocode(search_target)
    
    # 3. Fallback: 如果搜不到地址，嘗試「路名 + 號」
    fallback_note = ""
    if not user_coords:
        # 如果使用者輸入包含「松山區」，有時官方地圖要「臺北市松山區」才能搜到
        if "臺北" not in search_target:
            search_target = "臺北市" + search_target
            user_coords = nlsc_geocode(search_target)

    if user_coords:
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
            'note': fallback_note
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
