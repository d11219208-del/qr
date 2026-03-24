# routes/admin_orders_routes.py

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
import bcrypt
from datetime import date
import datetime # 用於產生 Log 的時間戳記

from ecpay_invoice import invalid_ecpay_invoice, issue_ecpay_invoice 
import database
from database import get_db_connection
from utils import login_required, role_required  

admin_orders_bp = Blueprint('admin_orders', __name__)

# ==========================================
# 🛡️ 登入與登出系統
# ==========================================

@admin_orders_bp.route('/login', methods=['GET', 'POST'])
def login():
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
    session.clear() 
    return redirect(url_for('admin_orders.login'))


# ==========================================
# 📋 訂單與發票 API
# ==========================================

@admin_orders_bp.route('/api/orders', methods=['GET'])
@login_required          
@role_required('admin')  
def get_orders():
    target_date = request.args.get('date')
    invoice_no = request.args.get('invoice_no')
    
    try:
        if invoice_no:
            orders = database.get_order_by_invoice(invoice_no)
        elif target_date:
            orders = database.get_orders_by_date(target_date)
        else:
            return jsonify({"success": False, "message": "請提供日期或發票號碼"})
            
        return jsonify({"success": True, "orders": orders})
    except Exception as e:
        print(f"API Error: {e}")
        return jsonify({"success": False, "message": "資料庫查詢失敗"})


@admin_orders_bp.route('/api/invoice/void', methods=['POST'])
@login_required          
@role_required('admin')  
def void_invoice():
    data = request.json
    invoice_no = data.get('invoice_no')
    reason = data.get('reason', '管理員後台作廢')
    
    if not invoice_no:
        return jsonify({"success": False, "message": "缺少發票號碼"})
        
    result = invalid_ecpay_invoice(invoice_no, reason)
    
    if result.get("success"):
        try:
            database.update_invoice_status(invoice_no, "已作廢")
        except Exception as e:
            print(f"DB Update Error (void_invoice): {e}")
            
    return jsonify(result)


@admin_orders_bp.route('/api/invoice/issue', methods=['POST'])
@login_required          
@role_required('admin')  
def issue_invoice():
    data = request.json
    order_id = data.get('order_id')
    
    if not order_id:
        return jsonify({"success": False, "message": "缺少訂單編號"})
        
    try:
        order_data = database.get_order_by_id(order_id)
        if not order_data:
             return jsonify({"success": False, "message": "找不到該筆訂單"})
             
        result = issue_ecpay_invoice(order_data)
        
        if result.get("success"):
            new_invoice_no = result.get("invoice_no")
            database.update_order_invoice(order_id, new_invoice_no, "正常")
            
        return jsonify(result)
        
    except Exception as e:
        print(f"Issue Invoice Error: {e}")
        return jsonify({"success": False, "message": "開立發票發生系統錯誤"})


# ==========================================
# 🛑 核心修復：結合發票與訂單作廢的防呆機制
# ==========================================
@admin_orders_bp.route('/kitchen/cancel/<int:order_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def cancel_order_and_invoice(order_id):
    """
    這個路由處理「作廢訂單」按鈕。
    防呆機制：有發票 -> 嘗試作廢發票 -> 失敗則【阻斷】訂單作廢 -> 成功則繼續作廢訂單。
    """
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        # 1. 取得訂單資訊
        order = database.get_order_by_id(order_id)
        if not order:
            return jsonify({"success": False, "message": "找不到該訂單"}), 404

        invoice_no = order.get('invoice_no')
        invoice_status = order.get('invoice_status')

        # 2. 如果這單有發票，而且還沒被作廢過，先執行綠界發票作廢
        if invoice_no and invoice_status != '已作廢':
            inv_result = invalid_ecpay_invoice(invoice_no, "訂單作廢連動")
            
            # 🛑 阻斷機制：如果發票作廢失敗，回傳錯誤，不執行後續的訂單作廢！
            if not inv_result.get("success"):
                error_msg = inv_result.get('message', '未知錯誤')
                print(f"[{now}] ⚠️ 發票作廢失敗: {error_msg}")
                return jsonify({
                    "success": False, 
                    "message": f"綠界發票作廢失敗：{error_msg}。\n為避免帳務錯誤，此訂單尚未作廢！"
                }), 400
                
            # 發票作廢成功，更新發票狀態
            database.update_invoice_status(invoice_no, "已作廢")
            print(f"[{now}] ✅ 發票已作廢: {invoice_no}")

        # 3. 發票處理安全通過後，再將「訂單」狀態改為作廢
        # 💡 請確保 database.py 裡有 update_order_status 這個函數 (若您的函數名稱不同，請自行調整)
        database.update_order_status(order_id, "已作廢") 
        print(f"[{now}] 🗑️ 訂單作廢: ID {order_id}")
        
        return jsonify({"success": True, "message": "訂單與發票皆已作廢！"})
        
    except Exception as e:
        print(f"[{now}] ❌ Cancel Order Error: {e}")
        return jsonify({"success": False, "message": "系統內部錯誤"}), 500


# ==========================================
# 📋 網頁: 訂單管理後台 (動態產生 HTML)
# ==========================================
@admin_orders_bp.route('/admin/orders')
@login_required          
@role_required('admin')  
def admin_orders_page():
    search_date = request.args.get('date')
    search_invoice = request.args.get('invoice_no', '').strip()
    
    if not search_date and not search_invoice:
        search_date = date.today().strftime('%Y-%m-%d')

    if search_invoice:
        orders = database.get_order_by_invoice(search_invoice)
    else:
        orders = database.get_orders_by_date(search_date)

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

            if is_voided:
                status_badge = '<span class="badge bg-danger">已作廢</span>'
            elif has_invoice:
                status_badge = '<span class="badge bg-success">正常</span>'
            else:
                status_badge = '<span class="badge bg-secondary">未開立</span>'

            # 按鈕 HTML 組裝
            buttons = f"""
            <div style="display:flex; flex-direction:column; gap:5px;">
                <button onclick='askPrintType({oid})' class='btn btn-outline-secondary btn-sm' style='width:100%;'>🖨️ 補印單據</button>
                
                <div style="display:flex; gap:5px;">
                    <button onclick='if(confirm("⚠️ 確定只要作廢發票，並將此單更改為【作廢狀態】嗎？")) action("/kitchen/cancel/{oid}")' class='btn btn-sm' style='flex:1; background:#f44336; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;'>🗑️ 作廢訂單</button>
                    
                    <button onclick='if(confirm("⚠️ 確定要作廢發票並重新修改此單嗎？")) {{ fetch("/kitchen/cancel/{oid}").then(res => res.json()).then(data => {{ if(data.success){{ window.open("/menu?edit_oid={oid}&lang=zh", "_blank"); window.location.reload(); }} else {{ alert("修改前置作廢失敗：" + data.message); }} }}); }}' class='btn btn-sm' style='flex:1; background:#ff9800; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;'>✏️ 作廢並修改</button>
                </div>
                <div style="display:flex; gap:5px;">
            """
            
            if has_invoice and not is_voided:
                buttons += f"""<button class="btn btn-sm btn-outline-danger" style="flex:1;" onclick="voidInvoice('{inv_no}')">🧾 僅作廢發票</button>"""
                
            if not has_invoice or is_voided:
                btn_text = '重開發票' if is_voided else '獨立開立發票'
                buttons += f"""<button class="btn btn-sm btn-outline-success" style="flex:1;" onclick="issueInvoice({oid})">🧾 {btn_text}</button>"""
                
            buttons += "</div></div>"

            inv_text = inv_no if has_invoice else '<span class="text-muted">-</span>'

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

    return render_template('admin_orders.html', 
                           table_html=table_html, 
                           search_date=search_date, 
                           search_invoice=search_invoice)
