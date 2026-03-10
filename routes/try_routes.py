from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from database import get_db_connection
import psycopg2
import bcrypt  # 💡 新增：引入 bcrypt 用來驗證密碼
from utils import login_required  # 🛡️ 引入我們在 utils.py 寫好的防護罩

try_bp = Blueprint('try_debug', __name__)

# 建立資料庫欄位的中英對照表 (依照 database.py 的註解)
COLUMN_MAP = {
    # --- Products (產品表) ---
    'products': {
        'id': '自動遞增 ID',
        'name': '產品名稱 (必填)',
        'price': '價格 (必填)',
        'category': '分類名稱',
        'image_url': '圖片網址',
        'is_available': '是否上架',
        'custom_options': '自定義選項 (中文)',
        'sort_order': '排序序號',
        'name_en': '英文品名',
        'name_jp': '日文品名',
        'name_kr': '韓文品名',
        'custom_options_en': '英文自定義選項',
        'custom_options_jp': '日文自定義選項',
        'custom_options_kr': '韓文自定義選項',
        'print_category': '出單分類 (廚房用)',
        'category_en': '英文分類',
        'category_jp': '日文分類',
        'category_kr': '韓文分類'
    },
    # --- Orders (訂單表) ---
    'orders': {
        'id': '訂單 ID',
        'table_number': '桌號',
        'items': '訂單內容 (簡述)',
        'total_price': '總金額',
        'status': '訂單狀態 (Pending/Completed)',
        'created_at': '建立時間',
        'daily_seq': '當日流水號 (No.001)',
        'content_json': '完整訂單明細 (JSON)',
        'need_receipt': '是否需要收據/統編',
        'lang': '下單語系',
        'order_type': '訂單類型 (內用/外送)',
        'delivery_info': '外送綜合資訊',
        'customer_name': '客戶姓名',
        'customer_phone': '客戶電話',
        'customer_address': '客戶地址',
        'scheduled_for': '預約送達時間',
        'delivery_fee': '外送費'
    },
    # --- Settings (系統設定表) ---
    'settings': {
        'key': '設定鍵名 (如: shop_open)',
        'value': '設定值'
    },
    # === Users (使用者/管理員表) ===
    'users': {
        'id': '使用者 ID',
        'username': '帳號名稱',
        'password_hash': '密碼雜湊值 (加密後)',
        'role': '角色權限 (admin/staff)',
        'created_at': '建立時間'
    }
}

# ==========================================
# 🛡️ 登入與登出系統
# ==========================================

@try_bp.route('/try/login', methods=['GET', 'POST'])
def login():
    """處理管理員登入"""
    # 1. 如果是 POST，代表使用者送出帳號密碼
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return render_template('login.html', error="請輸入帳號和密碼")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # 尋找資料庫中是否有此帳號
            cur.execute("SELECT id, password_hash, role FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            
            if user:
                user_id, hashed_pw, role = user
                
                # 🛡️ 關鍵：使用 bcrypt 比對密碼
                if bcrypt.checkpw(password.encode('utf-8'), hashed_pw.encode('utf-8')):
                    # 比對成功！核發通行證 (Session)
                    session['user_id'] = user_id
                    session['username'] = username
                    session['role'] = role
                    
                    # 登入成功，導向資料庫檢視頁面
                    return redirect(url_for('try_debug.show_db_structure'))
                else:
                    return render_template('login.html', error="密碼錯誤")
            else:
                return render_template('login.html', error="找不到此帳號")
                
        except Exception as e:
            print(f"Login Error: {e}")
            return render_template('login.html', error="系統發生錯誤，請稍後再試")
        finally:
            cur.close()
            conn.close()
            
    # 2. 如果是 GET，顯示登入網頁
    return render_template('login.html')

@try_bp.route('/try/logout')
def logout():
    """處理登出"""
    session.clear() # 清除通行證
    return redirect(url_for('try_debug.login'))

# ==========================================
# 🔒 受保護的路由 (需要登入才能操作)
# ==========================================

@try_bp.route('/try')
@login_required  # 🛡️ 加入防護罩：沒登入的人會被導向 /try/login
def show_db_structure():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 抓取所有資料表名稱 (public schema)
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_type = 'BASE TABLE';
    """)
    tables = [row[0] for row in cur.fetchall()]
    
    db_info = {}
    
    for table in tables:
        # 2. 針對每個表，抓取欄位詳細資訊
        cur.execute(f"""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = '{table}'
            ORDER BY ordinal_position;
        """)
        raw_columns = cur.fetchall()
        
        # 處理欄位資訊，加入中文說明
        columns_info = []
        for col in raw_columns:
            col_name = col[0]
            col_type = col[1]
            
            # 從對照表中尋找中文說明
            description = COLUMN_MAP.get(table, {}).get(col_name, '-')
            
            columns_info.append({
                'name': col_name,
                'type': col_type,
                'nullable': col[2],
                'default': col[3],
                'desc': description
            })
        
        # 3. 判斷該表的 Primary Key (PK)
        pk_col = 'key' if table == 'settings' else 'id'

        # 4. 抓取該表的所有資料
        cur.execute(f"SELECT * FROM {table} ORDER BY {pk_col} DESC LIMIT 50")
        sample_rows = cur.fetchall()
        
        db_info[table] = {
            'schema': columns_info,
            'data': sample_rows,
            'pk_col': pk_col 
        }

    cur.close()
    conn.close()
    
    # 將當前登入者名稱傳給前端 (可顯示於網頁右上角)
    return render_template('try.html', db_info=db_info, current_user=session.get('username'))

@try_bp.route('/try/update', methods=['POST'])
@login_required  # 🛡️ 加入防護罩：未登入者無法打這支 API 修改資料庫
def update_db_data():
    """
    接收 JSON 格式: { table, pk_col, pk_val, column, value }
    用途：讓開發者在 try.html 頁面上直接修改資料庫內容
    """
    data = request.json
    table = data.get('table')
    pk_col = data.get('pk_col')
    pk_val = data.get('pk_val')
    column = data.get('column')
    new_value = data.get('value')

    if not table or not column or not pk_val:
        return jsonify({'success': False, 'error': 'Missing parameters'})

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = f"UPDATE {table} SET {column} = %s WHERE {pk_col} = %s"
        cur.execute(query, (new_value, pk_val))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback() 
        print(f"Update Error: {e}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cur.close()
        conn.close()
