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

def clean_address_for_nlsc(addr):
    """
    針對國土測繪雲進行地址精確預處理：
    1. 移除郵遞區號 (如: 10576)
    2. 移除所有空白與特殊符號
    3. 只保留縣市以後的內容
    """
    if not addr: return ""
    # 轉半形
    addr = addr.translate(str.maketrans('０１２３４５６７８９－', '0123456789-'))
    # 移除開頭郵遞區號
    addr = re.sub(r'^\d{3,5}', '', addr).strip()
    # 移除空格與樓層資訊，國土測繪雲最喜歡「號」結尾的字串
    match = re.search(r'(.+?[路街大道巷弄].+?\d+號)', addr)
    if match:
        return match.group(1)
    return addr

def nlsc_geocode(address):
    """
    呼叫國土測繪圖資服務雲 API
    """
    try:
        # NLSC 的搜尋建議介面 (支援門牌與地標)
        url = "https://maps.nlsc.gov.tw/S_Maps/Search"
        params = {"q": address}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://maps.nlsc.gov.tw/"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        data = response.json()
        
        # 遍歷結果尋找經緯度
        if isinstance(data, list) and len(data) > 0:
            for item in data:
                # 取得座標 (NLSC 可能回傳 x,y 或 lon,lat)
                lon = item.get('x') or item.get('lon')
                lat = item.get('y') or item.get('lat')
                
                if lat and lon:
                    return (float(lat), float(lon))
    except Exception as e:
        print(f"NLSC 解析失敗: {e}")
    return None

def generate_time_slots(base_date):
    """產生外送時段邏輯 (略，保持原始邏輯)"""
    slots = []
    current_dt = datetime.combine(base_date, time(10, 30))
    end_dt = datetime.combine(base_date, time(20, 30))
    now_tw = datetime.utcnow() + timedelta(hours=8)
    while current_dt <= end_dt:
        if not (time(11,30) <= current_dt.time() <= time(13,30)):
            if not (base_date == now_tw.date() and current_dt < (now_tw + timedelta(minutes=30))):
                slots.append(current_dt.strftime("%H:%M"))
        current_dt += timedelta(minutes=30)
    return slots

@delivery_bp.route('/setup')
def setup():
    settings = get_delivery_settings()
    if not settings['enabled']:
        return "<script>alert('抱歉，暫停外送'); window.location.href='/';</script>"
    date_options = []
    now = datetime.utcnow() + timedelta(hours=8)
    for i in range(3):
        d = (now + timedelta(days=i)).date()
        slots = generate_time_slots(d)
        if slots:
            date_options.append({'value': d.strftime("%Y-%m-%d"), 'label': d.strftime('%m/%d'), 'slots': slots})
    return render_template('delivery_setup.html', dates=date_options)

@delivery_bp.route('/check', methods=['POST'])
def check_address():
    data = request.json
    raw_address = data.get('address', '').strip()
    
    if not raw_address:
        return jsonify({'success': False, 'msg': '請輸入地址'})
    
    settings = get_delivery_settings()
    
    # --- 關鍵修正：先清洗地址再搜尋 ---
    search_target = clean_address_for_nlsc(raw_address)
    user_coords = nlsc_geocode(search_target)
    
    fallback_note = ""
    # 如果精確門牌找不到，嘗試只搜路名
    if not user_coords:
        road_only = re.search(r'.+?[路街大道巷弄]', search_target)
        if road_only:
            user_coords = nlsc_geocode(road_only.group(0))
            fallback_note = "(地址定位不精確，以路段中心估算)"

    if user_coords:
        dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
        
        if dist > settings['max_km']:
            return jsonify({'success': False, 'msg': f'超出外送範圍 ({dist:.1f}km)'})

        shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])

        session['delivery_data'] = {
            'name': data.get('name'), 'phone': data.get('phone'), 
            'address': raw_address, 'scheduled_for': f"{data.get('date')} {data.get('time')}"
        }
        session['delivery_info'] = {
            'is_delivery': True, 'distance_km': round(dist, 1),
            'shipping_fee': shipping_fee, 'min_price': settings['min_price'], 'note': fallback_note
        }
        session['table_num'] = '外送'
        session.modified = True
        return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})
    
    # 最終失敗則轉人工模式
    session['delivery_info'] = {'is_delivery': True, 'shipping_fee': settings['base_fee'], 'note': "⚠️ 地址解析失敗，將由專人電話確認"}
    session.modified = True
    return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh'), 'msg': '轉為人工確認'})
