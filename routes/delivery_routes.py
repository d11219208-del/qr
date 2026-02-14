from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta, time
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError
from haversine import haversine, Unit
from database import get_db_connection
import re
import random

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (請確認這是正確的)
RESTAURANT_COORDS = (25.054358, 121.543468)

def get_delivery_settings():
    """
    從資料庫讀取外送設定
    確保欄位名稱與 database.py 的設定 (settings 表) 完全一致
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    rows = cur.fetchall()
    conn.close()
    
    # 將資料轉為字典，例如: {'delivery_fee_base': '0', 'delivery_max_km': '5', ...}
    s = {row[0]: row[1] for row in rows}
    
    return {
        'enabled': s.get('delivery_enabled', '1') == '1',
        'min_price': int(s.get('delivery_min_price', 500)),
        
        # --- 修正處：確保這裡讀取的是 database.py 定義的鍵名 ---
        'max_km': float(s.get('delivery_max_km', 5.0)),          # 最大距離
        'base_fee': int(s.get('delivery_fee_base', 0)),          # 基礎運費 (對應 DB 的 delivery_fee_base)
        'fee_per_km': int(s.get('delivery_fee_per_km', 10))      # 每公里加價
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
    match = re.search(r'.+?[縣市].+?[區鄉鎮市].+?[路街大道巷]', addr)
    if match:
        return match.group(0)
    match_simple = re.search(r'.+?[路街大道]', addr)
    if match_simple:
        return match_simple.group(0)
    return addr 

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
        # 如果是「今天」，過濾掉已經過去的時間 (保留30分鐘緩衝)
        if base_date == now_tw.date() and current_dt < (now_tw + timedelta(minutes=30)):
            current_dt += timedelta(minutes=30)
            continue
            
        in_forbidden_zone = (t >= block_start and t <= block_end)
        
        if not in_forbidden_zone:
            time_str = current_dt.strftime("%H:%M")
            slots.append(time_str)
            
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
        val = d.strftime("%Y-%m-%d")
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        label_date = f"{d.strftime('%m/%d')} ({weekdays[d.weekday()]})"
        if i == 0: label_date += " (今天)"
        
        slots = generate_time_slots(d)
        if slots:
            date_options.append({
                'value': val, 
                'label': label_date,
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
    
    if not raw_address or not name or not phone or not delivery_date or not delivery_time:
        return jsonify({'success': False, 'msg': '請填寫完整資訊 (含日期與時間)'})
    
    # 隨機 User-Agent 避免封鎖
    ua_string = f"mbdv_delivery_app_user_{random.randint(10000, 99999)}"
    geolocator = Nominatim(user_agent=ua_string)
    
    location = None
    fallback_level = 0
    settings = get_delivery_settings() # 這裡會取得正確的 DB 設定

    scheduled_for = f"{delivery_date} {delivery_time}"

    try:
        # --- 第一層：標準清洗 ---
        search_addr = normalize_address(raw_address)
        query = f"台灣 {search_addr}"
        location = geolocator.geocode(query, timeout=5)
        
        # --- 第二層：去連字號 ---
        if not location:
            addr_no_dash = re.sub(r'(\d+)[-‐‑]\d+號', r'\1號', search_addr)
            addr_no_dash = re.sub(r'(\d+號)之\d+', r'\1', addr_no_dash)
            if addr_no_dash != search_addr:
                print(f"嘗試降級搜尋 (去號): {addr_no_dash}")
                location = geolocator.geocode(f"台灣 {addr_no_dash}", timeout=5)
                fallback_level = 1

        # --- 第三層：只搜路名 ---
        if not location:
            road_only = extract_road_only(search_addr)
            if road_only != search_addr:
                print(f"嘗試終極搜尋 (只搜路名): {road_only}")
                location = geolocator.geocode(f"台灣 {road_only}", timeout=5)
                fallback_level = 2

        # --- 判斷結果 ---
        if location:
            user_coords = (location.latitude, location.longitude)
            dist = haversine(RESTAURANT_COORDS, user_coords, unit=Unit.KILOMETERS)
            
            # 使用從 DB 讀取的 max_km
            if dist > settings['max_km']:
                return jsonify({
                    'success': False, 
                    'msg': f'超出外送範圍 (距離 {dist:.1f}km, 目前限制 {settings["max_km"]}km)'
                })

            # 計算運費：基本費 (從 DB delivery_fee_base 讀來) + (距離 * 每公里費率)
            shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])
            
            note = ""
            if fallback_level == 2:
                note = "(以路段中心估算)"

            session['delivery_data'] = {
                'name': name,
                'phone': phone,
                'address': raw_address,
                'scheduled_for': scheduled_for
            }
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

        else:
            return jsonify({
                'success': False, 
                'msg': '找不到此地址，請確認路名是否正確。'
            })

    except Exception as e:
        print(f"Geo Error (切換至人工模式): {e}")
        
        # 發生錯誤時，運費暫時設為基本費
        session['delivery_data'] = {
            'name': name,
            'phone': phone,
            'address': raw_address,
            'scheduled_for': scheduled_for
        }
        
        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': 0,
            'shipping_fee': settings['base_fee'], # 使用 DB 設定的基礎運費
            'min_price': settings['min_price'],
            'note': "⚠️ 地圖連線忙碌，運費僅為預估，將由專人電話確認"
        }
        
        session['table_num'] = '外送'
        session.modified = True
        
        return jsonify({
            'success': True, 
            'redirect': url_for('menu.menu', lang='zh'),
            'msg': '地圖連線忙碌，將轉為人工確認模式'
        })
