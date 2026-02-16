from flask import Blueprint, render_template, request, jsonify, session, url_for
from datetime import datetime, timedelta, time
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from haversine import haversine, Unit
from database import get_db_connection
import re
import random

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (10491臺北市中山區龍江路164號)
RESTAURANT_COORDS = (25.0549998,121.5377779)

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
    結合地籍圖資正規化邏輯的清洗器：
    1. 統一全形轉半形 (處理數字與連字號)
    2. 移除郵遞區號
    3. 核心邏輯：強制擷取至「號」為止，捨棄後方所有備註 (如：圖書二館、x樓)
    """
    if not addr: return ""
    
    # 轉半形 (將 ０-９ 轉為 0-9，－ 轉為 -)
    addr = addr.translate(str.maketrans('０１２３４５６７８９－', '0123456789-'))
    
    # 移除開頭的郵遞區號
    addr = re.sub(r'^\d{3,5}\s?', '', addr)
    
    # 正規化規則：匹配 [縣市] + [區/鎮/鄉] + [路/街/大道/巷/弄] + [數字 + 號]
    # 範例：'114臺北市內湖區環山路一段56號圖書二館' -> '臺北市內湖區環山路一段56號'
    match = re.search(r'(.+?[路街大道巷弄].+?\d+號)', addr)
    if match:
        return match.group(1).replace(" ", "")
    
    return addr.strip()

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
    
    # 模擬隨機瀏覽器請求，避免被 OSM 暫時封鎖
    ua_list = [f"Taiwan_Delivery_Bot_{random.randint(100,999)}", "Mozilla/5.0", "Map_Calculator_v1"]
    geolocator = Nominatim(user_agent=random.choice(ua_list))
    
    settings = get_delivery_settings()
    scheduled_for = f"{delivery_date} {delivery_time}"

    try:
        # --- 核心邏輯：仿地籍圖資精確化清洗 ---
        search_target = advanced_taiwan_address_cleaner(raw_address)
        
        # 第一次嘗試：搜尋完整精確門牌
        location = geolocator.geocode(search_target, country_codes='tw', timeout=10)
        
        # 第二次嘗試 (Fallback)：如果找不到號碼，退回搜尋「路段」
        fallback_note = ""
        if not location:
            road_only = re.search(r'.+?[路街大道巷弄]', search_target)
            if road_only:
                location = geolocator.geocode(road_only.group(0), country_codes='tw', timeout=10)
                fallback_note = "(精確門牌定位失敗，以路段中心計算)"

        if location:
            user_coords = (location.latitude, location.longitude)
            # 使用 Haversine 公式計算直線距離
            dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
            
            if dist > settings['max_km']:
                return jsonify({
                    'success': False, 
                    'msg': f'超出外送範圍 (距離 {dist:.1f}km, 目前限制 {settings["max_km"]}km)'
                })

            shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])

            # 存入 Session
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
            return jsonify({'success': False, 'msg': '地圖無法解析此地址，請確認路名與門牌'})

    except (GeocoderTimedOut, GeocoderServiceError):
        # 伺服器忙碌時的保險機制 (人工模式)
        session['delivery_data'] = {'name': name, 'phone': phone, 'address': raw_address, 'scheduled_for': scheduled_for}
        session['delivery_info'] = {
            'is_delivery': True, 'distance_km': 0, 'shipping_fee': settings['base_fee'],
            'min_price': settings['min_price'], 'note': "⚠️ 地圖連線忙碌，運費請與店家確認"
        }
        session.modified = True
        return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})
