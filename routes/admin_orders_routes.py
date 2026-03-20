# routes/admin_orders_routes.py

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
import bcrypt # 用於密碼驗證
from ecpay_invoice import invalid_ecpay_invoice # 引入您剛剛寫好的作廢功能
import database # 您的資料庫模組
from database import get_db_connection # 確保引入資料庫連線功能

# 🛡️ 引入我們在 utils.py 寫好的雙重防護罩
from utils import login_required, role_required  

# 💡 修正：將 app = Flask(__name__) 改為使用 Blueprint
admin_orders_bp = Blueprint('admin_orders', __name__)

# ==========================================
# 🛡️ 登入與登出系統
# ==========================================

@admin_orders_bp.route('/login', methods=['GET', 'POST'])
def login():
    """處理管理員登入"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return render_template('login.html', error="請輸入帳號和密碼")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, password_hash, role FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            
            if user:
                user_id, hashed_pw, role = user
                # 驗證加密密碼
                if bcrypt.checkpw(password.encode('utf-8'), hashed_pw.encode('utf-8')):
                    session['user_id'] = user_id
                    session['username'] = username
                    session['role'] = role
                    # 成功登入後跳轉 (假設您的 admin_bp 裡有 admin_panel)
                    return redirect(url_for('admin.admin_panel'))
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
            
    return render_template('login.html')

@admin_orders_bp.route('/logout')
def logout():
    """處理登出"""
    session.clear() 
    # 跳轉回登入頁 (如果 login 是在別的 blueprint，請維持 'admin.login'；如果就是上面那個，可改為 'admin_orders.login')
    return redirect(url_for('admin.login'))


# ==========================================
# 📋 訂單與發票 API (加上了權限防護)
# ==========================================

# 1. 取得指定日期的訂單 API
@admin_orders_bp.route('/api/orders', methods=['GET'])
@login_required          
@role_required('admin')  
def get_orders():
    target_date = request.args.get('date') # 格式預期為 YYYY-MM-DD
    if not target_date:
        return jsonify({"success": False, "message": "請提供日期"})
    
    try:
        # 呼叫您的資料庫查詢該日訂單 (請依據您的 database.py 實際寫法調整)
        # 預期回傳一個 list of dict，包含訂單與發票號碼
        orders = database.get_orders_by_date(target_date) 
        return jsonify({"success": True, "orders": orders})
    except Exception as e:
        print(f"API Error (get_orders): {e}")
        return jsonify({"success": False, "message": "資料庫查詢失敗"})

# 2. 作廢發票 API
@admin_orders_bp.route('/api/invoice/void', methods=['POST'])
@login_required          
@role_required('admin')  
def void_invoice():
    data = request.json
    invoice_no = data.get('invoice_no')
    reason = data.get('reason', '管理員後台作廢')
    
    if not invoice_no:
        return jsonify({"success": False, "message": "缺少發票號碼"})
        
    # 呼叫 ecpay_invoice.py 的作廢功能
    result = invalid_ecpay_invoice(invoice_no, reason)
    
    # 如果作廢成功，記得更新資料庫的發票狀態
    if result.get("success"):
        try:
            database.update_invoice_status(invoice_no, "已作廢")
        except Exception as e:
            print(f"DB Update Error (void_invoice): {e}")
            
    return jsonify(result)

# 3. 渲染管理員後台網頁
@admin_orders_bp.route('/admin/orders')
@login_required          # 💡 幫您在網頁也加上防護，避免沒登入就被看光
@role_required('admin')  # 💡 只有管理員可以看這個頁面
def admin_orders_page():
    return render_template('admin_orders.html') # 回傳 HTML 檔案
