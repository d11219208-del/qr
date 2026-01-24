from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from database import get_db_connection
from translations import load_translations
import json

# 建立藍圖
menu_bp = Blueprint('menu', __name__)

# --- 語言選擇首頁 ---
@menu_bp.route('/')
def index():
    return render_template('index.html')

# --- 點餐主頁面 ---
@menu_bp.route('/menu')
def menu():
    lang = request.args.get('lang', 'zh')
    table_num = request.args.get('table', '')
    
    # 載入翻譯
    translations = load_translations()
    texts = translations.get(lang, translations['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 抓取所有可用餐點，並按 sort_order 排序
    cur.execute("""
        SELECT id, name, price, category, image_url, is_available, custom_options,
               name_en, name_jp, name_kr, 
               custom_options_en, custom_options_jp, custom_options_kr,
               category_en, category_jp, category_kr
        FROM products 
        WHERE is_available = TRUE 
        ORDER BY sort_order ASC, id ASC
    """)
    rows = cur.fetchall()
    
    # 轉換成前端易讀的格式
    products = []
    categories = set()
    for r in rows:
        # 根據語系選擇正確的名稱與分類
        p_name = r[1] if lang=='zh' else (r[7] if lang=='en' else (r[8] if lang=='jp' else r[9]))
        p_cat = r[3] if lang=='zh' else (r[13] if lang=='en' else (r[14] if lang=='jp' else r[15]))
        p_opts = r[6] if lang=='zh' else (r[10] if lang=='en' else (r[11] if lang=='jp' else r[12]))
        
        products.append({
            "id": r[0],
            "name": p_name or r[1], # 若無翻譯則用中文
            "price": r[2],
            "category": p_cat or r[3],
            "image_url": r[4],
            "custom_options": p_opts
        })
        categories.add(p_cat or r[3])
    
    cur.close()
    conn.close()
    
    return render_template('menu.html', 
                           products=products, 
                           categories=sorted(list(categories)), 
                           texts=texts, 
                           lang=lang, 
                           table_num=table_num)

# --- 送出訂單 API ---
@menu_bp.route('/submit_order', methods=['POST'])
def submit_order():
    data = request.json
    table_number = data.get('table_number', 'Unknown')
    items = data.get('items', [])
    total_price = data.get('total_price', 0)
    need_receipt = data.get('need_receipt', False)
    lang = data.get('lang', 'zh')

    if not items:
        return jsonify({"success": False, "message": "Empty cart"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 計算今日單號 (Daily Sequence)
        cur.execute("SELECT COUNT(*) FROM orders WHERE created_at::date = CURRENT_DATE")
        daily_seq = cur.fetchone()[0] + 1
        
        # 插入訂單
        cur.execute("""
            INSERT INTO orders (table_number, items, total_price, status, daily_seq, content_json, need_receipt, lang)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            table_number, 
            ", ".join([f"{i['name']} x{i['qty']}" for i in items]),
            total_price, 
            'Pending', 
            daily_seq, 
            json.dumps(items), 
            need_receipt,
            lang
        ))
        order_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({"success": True, "order_id": order_id, "daily_seq": daily_seq})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()
        conn.close()