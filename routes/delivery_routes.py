from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta
# 引入錯誤處理模組
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from haversine import haversine, Unit
from database import get_db_connection
import re # 用於正規表達式處理地址

delivery_bp = Blueprint('delivery', __name__)

# 餐廳座標 (範例：臺北市中山區龍江路164號)
# 建議：你可以去 Google Maps 對你的餐廳按右鍵取得精準的經緯度
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

def clean_address_for_search(addr):
    """
    清洗地址：移除樓層、室號等詳細資訊，增加搜尋成功率。
    例如：'台北市中山區南京東路三段100號5樓之1' -> '台北市中山區南京東路三段100號'
    """
    # 移除 'F', 'f', '樓', '室' 後面的所有字元 (包含該字)
    # 這是為了讓 OpenStreetMap 只要找到「該棟建築物」即可計算距離
    import re
    # 移除 "5樓", "5F", "5f", "R5", "室" 及其之後的內容
    addr = re.sub(r'(\d+[Ff樓].*)|(室.*)|(Rm.*)', '', addr)
    return addr.strip()

# 1. 外送首頁 (顯示表單)
@delivery_bp.route('/setup')
def setup():
    settings = get_delivery_settings()
    if not settings['enabled']:
        return "<script>alert('抱歉，目前暫停外送服務'); window.location.href='/';</script>"

    date_options = []
    # UTC+8
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
        return jsonify({'success': False, 'msg': '請填寫完整資訊 (姓名、電話、地址)'})
    
    # 建立 Geocoder 物件，務必設定 user_agent
    geolocator = Nominatim(user_agent="tw_food_delivery_app_v2_debug")
    
    location = None
    try:
        # 
        # 第一次嘗試：搜尋完整地址
        # 加上 "台灣" 避免搜尋到中國或其他國家同名路段
        search_query = f"台灣 {raw_address}"
        # timeout 設定為 10 秒，避免網路稍慢就報錯
        location = geolocator.geocode(search_query, timeout=10)
        
        # 第二次嘗試：若找不到，進行地址清洗 (移除樓層資訊)
        if not location:
            cleaned_addr = clean_address_for_search(raw_address)
            if cleaned_addr != raw_address: # 如果清洗後有變短，才試第二次
                print(f"原地址找不到，嘗試清洗後搜尋: {cleaned_addr}")
                location = geolocator.geocode(f"台灣 {cleaned_addr}", timeout=10)

        # 判定結果
        if not location:
            return jsonify({'success': False, 'msg': '找不到此地址，請嘗試只輸入「路名+號碼」(不要輸入樓層)，或檢查是否有錯字'})

        # 計算距離
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
        
        # --- 關鍵修正：Session 結構必須與 menu.html 對應 ---
        
        # 1. 儲存基本資料 (menu.html 用這個判斷是否顯示外送卡片)
        session['delivery_data'] = {
            'name': name,
            'phone': phone,
            'address': raw_address # 這裡存使用者輸入的完整地址(含樓層)
        }

        # 2. 儲存計算結果
        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': round(dist, 1),
            'shipping_fee': shipping_fee,
            'min_price': settings['min_price']
        }
        
        # 3. 設定桌號為外送
        session['table_num'] = '外送'
        
        # 4. 強制更新 Session
        session.modified = True
        
        return jsonify({'success': True, 'redirect': url_for('menu', lang='zh')})

    except (GeocoderTimedOut, GeocoderUnavailable):
        return jsonify({'success': False, 'msg': '地圖服務連線逾時，請稍後再試 (或檢查網路連線)'})
    except Exception as e:
        print(f"Geo Error: {e}")
        return jsonify({'success': False, 'msg': f'系統發生錯誤: {str(e)}'})
