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
    清洗地址以符合國土測繪雲搜尋格式
    """
    if not addr: return ""
    addr = addr.translate(str.maketrans('０１２３４５６７８９－', '0123456789-'))
    addr = re.sub(r'^\d{3,5}\s?', '', addr)
    # 國土測繪雲對備註詞容忍度較高，但仍保留抓取到『號』的邏輯作為主搜尋
    match = re.search(r'(.+?[路街大道巷弄].+?\d+號)', addr)
    if match:
        return match.group(1).replace(" ", "")
    return addr.strip()

def nlsc_geocode(address):
    """
    利用國土測繪圖資服務雲 (NLSC) 進行門牌定位
    """
    try:
        # NLSC 地標與門牌查詢 API (這是公用查詢介面使用的 API)
        url = "https://maps.nlsc.gov.tw/S_Maps/Search"
        params = {
            "lang": "zh_TW",
            "q": address
        }
        # 模擬瀏覽器 Headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://maps.nlsc.gov.tw/"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=8)
        data = response.json()
        
        # NLSC 回傳格式通常是清單，取第一個匹配項
        if data and len(data) > 0:
            # 國土測繪雲回傳通常包含 lat, lon 或 x, y (EPSG:4326)
            # 部分回傳格式可能是 { "lat": ..., "lon": ... } 或是在內容字串中
            res = data[0]
            lat = float(res.get('lat') or res.get('y'))
            lon = float(res.get('lon') or res.get('x'))
            return (lat, lon)
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

    # 1. 地址清洗
    search_target = advanced_taiwan_address_cleaner(raw_address)
    
    # 2. 使用國土測繪雲 (NLSC) 定位
    user_coords = nlsc_geocode(search_target)
    
    # 3. Fallback: 如果精確地址失敗，嘗試只搜尋路名
    fallback_note = ""
    if not user_coords:
        road_only = re.search(r'.+?[路街大道巷弄]', search_target)
        if road_only:
            user_coords = nlsc_geocode(road_only.group(0))
            fallback_note = "(精確門牌搜尋失敗，改用路段中心估算)"

    if user_coords:
        # 4. 計算距離
        dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
        
        if dist > settings['max_km']:
            return jsonify({
                'success': False, 
                'msg': f'超出外送範圍 (距離 {dist:.1f}km, 目前限制 {settings["max_km"]}km)'
            })

        shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])

        # 5. 存入 Session
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
        # 如果 NLSC 也失敗，轉人工模式
        session['delivery_data'] = {'name': name, 'phone': phone, 'address': raw_address, 'scheduled_for': scheduled_for}
        session['delivery_info'] = {
            'is_delivery': True, 'distance_km': 0, 'shipping_fee': settings['base_fee'],
            'min_price': settings['min_price'], 'note': "⚠️ 地址解析失敗，將由專人電話確認"
        }
        session.modified = True
        return jsonify({
            'success': True, 
            'redirect': url_for('menu.menu', lang='zh'),
            'msg': '無法精確定位地址，已轉為人工確認'
        })
