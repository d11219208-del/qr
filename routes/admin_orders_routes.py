# 這裡以 Flask 為例，如果您用 FastAPI 邏輯也十分類似
from flask import Flask, request, jsonify, render_template
from ecpay_invoice import invalid_ecpay_invoice # 引入您剛剛寫好的作廢功能
import database # 您的資料庫模組
# 🛡️ 引入我們在 utils.py 寫好的雙重防護罩
from utils import login_required, role_required  

app = Flask(__name__)

# ==========================================
# 🛡️ 登入與登出系統
# ==========================================

@admin_bp.route('/login', methods=['GET', 'POST'])
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
                if bcrypt.checkpw(password.encode('utf-8'), hashed_pw.encode('utf-8')):
                    session['user_id'] = user_id
                    session['username'] = username
                    session['role'] = role
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

@admin_bp.route('/logout')
def logout():
    """處理登出"""
    session.clear() 
    return redirect(url_for('admin.login'))



# 1. 取得指定日期的訂單 API
@app.route('/api/orders', methods=['GET'])
@login_required          
@role_required('admin')  
def get_orders():
    target_date = request.args.get('date') # 格式預期為 YYYY-MM-DD
    if not target_date:
        return jsonify({"success": False, "message": "請提供日期"})
    
    # 呼叫您的資料庫查詢該日訂單 (請依據您的 database.py 實際寫法調整)
    # 預期回傳一個 list of dict，包含訂單與發票號碼
    orders = database.get_orders_by_date(target_date) 
    
    return jsonify({"success": True, "orders": orders})

# 2. 作廢發票 API
@app.route('/api/invoice/void', methods=['POST'])
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
        database.update_invoice_status(invoice_no, "已作廢")
        
    return jsonify(result)

# 3. 渲染管理員後台網頁
@app.route('/admin/orders')
def admin_orders_page():
    return render_template('admin_orders.html') # 回傳下面的 HTML 檔案
