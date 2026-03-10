# routes/try_routes.py
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from database import get_db_connection
import psycopg2
import bcrypt
# 🛡️ 引入我們在 utils.py 寫好的雙重防護罩
from utils import login_required, role_required  

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
    """處理管理員/員工登入"""
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
            
    # 如果是 GET，顯示登入網頁
    return render_template('login.html')

@try_bp.route('/try/logout')
def logout():
    """處理登出"""
    session.clear() # 清除通行證
    return redirect(url_for('try_debug.login'))

# ==========================================
# 🔒 受保護的路由 (需要登入且必須是 Admin 才能操作)
# ==========================================

@try_bp.route('/try')
@login_required 
@role_required('admin')  # 🛡️ 危險動作：只有管理員能看原始資料庫結構
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
@login_required 
@role_required('admin')  # 🛡️ 危險動作：只有管理員能直接修改資料庫欄位
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

# ==========================================
# 🆕 新增使用者帳號專屬網頁
# ==========================================

@try_bp.route('/try/add_user', methods=['GET', 'POST'])
@login_required 
@role_required('admin')  # 🛡️ 危險動作：只有管理員能新增其他員工或管理員
def add_user():
    # 如果是填完表單送出 (POST)
    if request.method == 'POST':
        new_username = request.form.get('username')
        new_password = request.form.get('password')
        role = request.form.get('role', 'staff')

        if not new_username or not new_password:
            return "帳號和密碼不能為空！ <a href='/try/add_user'>回上一頁</a>"

        # 💡 核心步驟：將新密碼進行 bcrypt 加密
        salt = bcrypt.gensalt()
        hashed_pw = bcrypt.hashpw(new_password.encode('utf-8'), salt).decode('utf-8')

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # 存入資料庫
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                (new_username, hashed_pw, role)
            )
            conn.commit()
            return f"<h3>✅ 成功新增帳號：{new_username}！</h3> <a href='/try'>返回資料庫後台</a>"
            
        except psycopg2.errors.UniqueViolation:
            # 處理帳號重複的錯誤
            conn.rollback()
            return "<h3>❌ 錯誤：這個帳號名稱已經存在了！</h3> <a href='/try/add_user'>重新輸入</a>"
        except Exception as e:
            conn.rollback()
            return f"<h3>❌ 系統錯誤：{e}</h3> <a href='/try/add_user'>回上一頁</a>"
        finally:
            cur.close()
            conn.close()

    # 如果是直接進入網址 (GET)，顯示一個簡單的新增表單
    html_form = """
<!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <title>新增帳號</title>
        <style>
            body { font-family: sans-serif; padding: 40px; background-color: #f4f4f9; }
            .container { background: white; padding: 20px 30px; border-radius: 8px; max-width: 400px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 0 auto; }
            input[type="text"], input[type="password"], select { width: 100%; padding: 8px; margin: 8px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box;}
            .password-container { margin-bottom: 20px; }
            .show-password-wrapper { display: flex; align-items: center; gap: 5px; font-size: 14px; color: #555; margin-top: 5px; cursor: pointer; }
            .show-password-wrapper input[type="checkbox"] { width: auto; margin: 0; cursor: pointer; }
            button { background-color: #28a745; color: white; border: none; padding: 10px 15px; border-radius: 4px; cursor: pointer; width: 100%; font-size: 16px; margin-top: 10px;}
            button:hover { background-color: #218838; }
            a { display: block; margin-top: 15px; text-align: center; color: #007bff; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>👤 新增使用者帳號</h2>
            <form method="POST">
                <label>帳號名稱：</label>
                <input type="text" name="username" placeholder="請輸入新帳號" required>
                
                <div class="password-container">
                    <label>設定密碼：</label>
                    <input type="password" id="pwInput" name="password" placeholder="請輸入密碼" required>
                    <label class="show-password-wrapper">
                        <input type="checkbox" onclick="togglePassword()"> 顯示密碼
                    </label>
                </div>
                
                <label>角色權限：</label>
                <select name="role">
                    <option value="staff">員工 (Staff) - 只能看訂單</option>
                    <option value="admin">管理員 (Admin) - 可修改菜單與設定</option>
                </select>
                
                <button type="submit">確認新增</button>
            </form>
            <a href="/try">返回資料庫後台</a>
        </div>

        <script>
            function togglePassword() {
                var pwField = document.getElementById("pwInput");
                if (pwField.type === "password") {
                    pwField.type = "text"; // 變成明文
                } else {
                    pwField.type = "password"; // 變回星號
                }
            }
        </script>
    </body>
    </html>
    """
    return html_form
