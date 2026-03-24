# routes/admin_orders_routes.py

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
import bcrypt # 用於密碼驗證

# 💡 修正：已經為您換成正確的 issue_ecpay_invoice
from ecpay_invoice import invalid_ecpay_invoice, issue_ecpay_invoice 

import database # 您的資料庫模組
from database import get_db_connection # 確保引入資料庫連線功能

# 🛡️ 引入我們在 utils.py 寫好的雙重防護罩
from utils import login_required, role_required  

# 使用 Blueprint
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
                    # 成功登入後跳轉
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
    return redirect(url_for('admin.login'))


# ==========================================
# 📋 訂單與發票 API (加上了權限防護)
# ==========================================

# 1. 取得指定日期「或」指定發票號碼的訂單 API
@admin_orders_bp.route('/api/orders', methods=['GET'])
@login_required          
@role_required('admin')  
def get_orders():
    target_date = request.args.get('date') # 格式預期為 YYYY-MM-DD
    invoice_no = request.args.get('invoice_no') # 格式預期為發票號碼字串
    
    try:
        # 如果有傳入發票號碼，優先用發票號碼搜尋
        if invoice_no:
            orders = database.get_order_by_invoice(invoice_no)
        # 否則用日期搜尋
        elif target_date:
            orders = database.get_orders_by_date(target_date)
        else:
            return jsonify({"success": False, "message": "請提供日期或發票號碼"})
            
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
            # 這裡把狀態更新為 '已作廢'
            database.update_invoice_status(invoice_no, "已作廢")
        except Exception as e:
            print(f"DB Update Error (void_invoice): {e}")
            
    return jsonify(result)


# 3. 開立(重開)發票 API
@admin_orders_bp.route('/api/invoice/issue', methods=['POST'])
@login_required          
@role_required('admin')  
def issue_invoice():
    data = request.json
    order_id = data.get('order_id')
    
    if not order_id:
        return jsonify({"success": False, "message": "缺少訂單編號"})
        
    try:
        # 步驟 A: 從資料庫撈出這筆訂單的詳細資訊 (包含金額、商品名稱等，綠界需要這些)
        order_data = database.get_order_by_id(order_id)
        if not order_data:
             return jsonify({"success": False, "message": "找不到該筆訂單"})
             
        # 步驟 B: 💡 修正：正確呼叫您的 issue_ecpay_invoice 函數
        result = issue_ecpay_invoice(order_data)
        
        # 步驟 C: 如果綠界成功開出發票，取得新發票號碼並存回資料庫
        if result.get("success"):
            new_invoice_no = result.get("invoice_no")
            # 呼叫 database.py 寫入新發票號碼，並將狀態改為 '正常'
            database.update_order_invoice(order_id, new_invoice_no, "正常")
            
        return jsonify(result)
        
    except Exception as e:
        print(f"Issue Invoice Error: {e}")
        return jsonify({"success": False, "message": "開立發票發生系統錯誤"})


# 4. 渲染管理員後台網頁
@admin_orders_bp.route('/admin/orders')
@login_required          
@role_required('admin')  
def admin_orders_page():
    return render_template('admin_orders.html')
