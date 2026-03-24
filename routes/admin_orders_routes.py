# routes/admin_orders_routes.py

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
import bcrypt # 用於密碼驗證
from datetime import date

# 💡 已經為您換成正確的 issue_ecpay_invoice
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
                    return redirect(url_for('admin_orders.admin_orders_page'))
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
    return redirect(url_for('admin_orders.login'))


# ==========================================
# 📋 訂單與發票 API (加上了權限防護)
# ==========================================

# 1. 取得指定日期「或」指定發票號碼的訂單 API (保留供未來其他系統串接)
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
             
        # 步驟 B: 💡 呼叫您的 issue_ecpay_invoice 函數
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


# ==========================================
# 📋 網頁: 訂單管理後台 (💡 完全由 Python 產出 HTML)
# ==========================================

# 4. 渲染管理員後台網頁並動態生成表格
@admin_orders_bp.route('/admin/orders')
@login_required          
@role_required('admin')  
def admin_orders_page():
    # 取得網址上的參數
    search_date = request.args.get('date')
    search_invoice = request.args.get('invoice_no', '').strip()
    
    # 預設為今天
    if not search_date and not search_invoice:
        search_date = date.today().strftime('%Y-%m-%d')

    # 從資料庫撈取資料
    if search_invoice:
        orders = database.get_order_by_invoice(search_invoice)
    else:
        orders = database.get_orders_by_date(search_date)

    # 🟢 這裡開始：完全由 Python 組裝 HTML 表格字串
    table_html = ""
    if orders:
        for order in orders:
            oid = order.get('id', '')
            c_name = order.get('customer_name') or '門市顧客'
            t_price = order.get('total_price', 0)
            inv_no = order.get('invoice_no')
            inv_status = order.get('invoice_status')

            has_invoice = bool(inv_no)
            is_voided = (inv_status == '已作廢')

            # 1. 產生狀態標籤
            if is_voided:
                status_badge = '<span class="badge bg-danger">已作廢</span>'
            elif has_invoice:
                status_badge = '<span class="badge bg-success">正常</span>'
            else:
                status_badge = '<span class="badge bg-secondary">未開立</span>'

            # 2. 產生按鈕 HTML (Python 處理跳脫字元，使用雙大括號 {{ }} 避免跟 f-string 衝突)
            buttons = f"""
            <div style="display:flex; flex-direction:column; gap:5px;">
                <button onclick='askPrintType({oid})' class='btn btn-outline-secondary btn-sm' style='width:100%;'>🖨️ 補印單據</button>
                
                <div style="display:flex; gap:5px;">
                    <button onclick='if(confirm("⚠️ 確定只要作廢發票，並將此單更改為【作廢狀態】嗎？")) action("/kitchen/cancel/{oid}")' class='btn btn-sm' style='flex:1; background:#f44336; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;'>🗑️ 作廢訂單</button>
                    
                    <button onclick='if(confirm("⚠️ 確定要作廢發票並重新修改此單嗎？")) {{ fetch("/kitchen/cancel/{oid}").then(() => {{ window.open("/menu?edit_oid={oid}&lang=zh", "_blank"); window.location.reload(); }}); }}' class='btn btn-sm' style='flex:1; background:#ff9800; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;'>✏️ 作廢並修改</button>
                </div>
                <div style="display:flex; gap:5px;">
            """
            
            # 加入發票相關操作按鈕
            if has_invoice and not is_voided:
                buttons += f"""<button class="btn btn-sm btn-outline-danger" style="flex:1;" onclick="voidInvoice('{inv_no}')">🧾 僅作廢發票</button>"""
                
            if not has_invoice or is_voided:
                btn_text = '重開發票' if is_voided else '獨立開立發票'
                buttons += f"""<button class="btn btn-sm btn-outline-success" style="flex:1;" onclick="issueInvoice({oid})">🧾 {btn_text}</button>"""
                
            buttons += "</div></div>"

            # 3. 處理發票號碼顯示文字
            inv_text = inv_no if has_invoice else '<span class="text-muted">-</span>'

            # 4. 把每一行合併到字串中
            table_html += f"""
            <tr>
                <td>#{oid}</td>
                <td>{c_name}</td>
                <td>${t_price}</td>
                <td>{inv_text}</td>
                <td>{status_badge}</td>
                <td>{buttons}</td>
            </tr>
            """
    else:
        table_html = '<tr><td colspan="6" class="text-center text-muted">查無相符的訂單紀錄</td></tr>'

    # 傳遞組裝好的 table_html 與搜尋條件給模板
    return render_template('admin_orders.html', 
                           table_html=table_html, 
                           search_date=search_date, 
                           search_invoice=search_invoice)
