# routes/admin_orders_routes.py

import psycopg2
import psycopg2.extras
from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
import bcrypt
from datetime import date, datetime

# 💡 引入您的綠界發票功能 (加上了 print_ecpay_invoice)
from ecpay_invoice import invalid_ecpay_invoice, issue_ecpay_invoice, print_ecpay_invoice

import database
from database import get_db_connection
from utils import login_required, role_required  

admin_orders_bp = Blueprint('admin_orders', __name__)

def get_current_time_str():
    """產生時間戳記供 Log 使用"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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
# 📋 訂單與發票 API (升級使用 psycopg2 寫法)
# ==========================================

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
            c = get_db_connection()
            cur = c.cursor()
            cur.execute("UPDATE orders SET invoice_status='Void' WHERE invoice_number=%s", (invoice_no,))
            c.commit()
            c.close()
        except Exception as e:
            print(f"[{get_current_time_str()}] DB Update Error (void_invoice): {e}")
            
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
        c = get_db_connection()
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM orders WHERE id=%s", (order_id,))
        order_data = cur.fetchone()
        
        if not order_data:
             c.close()
             return jsonify({"success": False, "message": "找不到該筆訂單"})
             
        result = issue_ecpay_invoice(order_data)
        
        if result.get("success"):
            new_invoice_no = result.get("invoice_no")
            cur.execute("""
                UPDATE orders 
                SET invoice_number=%s, invoice_status='Issued' 
                WHERE id=%s
            """, (new_invoice_no, order_id))
            c.commit()
            print(f"[{get_current_time_str()}] 🧾 後台發票開立成功: {new_invoice_no}")
            
        c.close()
        return jsonify(result)
        
    except Exception as e:
        print(f"[{get_current_time_str()}] Issue Invoice Error: {e}")
        return jsonify({"success": False, "message": "開立發票發生系統錯誤"})


# ==========================================
# 🛑 核心作廢路由：確保帳務防呆
# ==========================================
@admin_orders_bp.route('/admin/cancel/<int:oid>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_cancel_order(oid):
    try:
        c = get_db_connection()
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 1. 先取得訂單資訊
        cur.execute("SELECT * FROM orders WHERE id=%s", (oid,))
        order = cur.fetchone()

        if not order:
            c.close()
            return jsonify({"success": False, "message": "找不到該訂單"}), 404

        invoice_no = order.get('invoice_number') 
        invoice_status = order.get('invoice_status')

        # 2. 如果這單有發票且尚未作廢，先執行綠界發票作廢
        if invoice_no and invoice_status == 'Issued':
            void_res = invalid_ecpay_invoice(invoice_no, f"訂單 {oid} 作廢連動")
            
            # 🛑 阻斷機制
            if not void_res.get('success'):
                c.close()
                error_msg = void_res.get('message', '未知錯誤')
                print(f"[{get_current_time_str()}] ⚠️ 發票作廢失敗: {error_msg}")
                return jsonify({
                    "success": False, 
                    "message": f"綠界發票作廢失敗：{error_msg}。\n為避免帳務錯誤，此訂單尚未作廢！"
                }), 400
                
            # 作廢成功，更新發票狀態
            print(f"[{get_current_time_str()}] 🗑️ 發票作廢成功: {invoice_no}")
            cur.execute("UPDATE orders SET invoice_status='Void' WHERE id=%s", (oid,))

        # 3. 再將「訂單」狀態改為 Cancelled
        cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (oid,))
        c.commit()
        c.close()

        print(f"[{get_current_time_str()}] 🗑️ 訂單作廢: ID {oid}")
        return jsonify({"success": True, "message": "訂單與發票皆已作廢！"})
        
    except Exception as e:
        print(f"[{get_current_time_str()}] ❌ Cancel Order Error: {e}")
        return jsonify({"success": False, "message": "系統內部錯誤"}), 500


# ==========================================
# 🖨️ 發票列印 API
# ==========================================
@admin_orders_bp.route('/admin/print_invoice/<invoice_no>')
@login_required
@role_required('admin')
def admin_print_invoice(invoice_no):
    """向綠界索取發票 HTML 並回傳給瀏覽器"""
    try:
        print(f"[{get_current_time_str()}] 🖨️ 準備列印發票: {invoice_no}")
        result = print_ecpay_invoice(invoice_no)
        
        if result.get("success"):
            return result.get("html")
        else:
            return f"<h1>列印失敗</h1><p>綠界回傳錯誤: {result.get('message')}</p>", 400
            
    except Exception as e:
        print(f"Error printing invoice: {e}")
        return f"<h1>系統錯誤</h1><p>{str(e)}</p>", 500


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

    # 使用 psycopg2 直接取資料以避免 database.py 發生欄位衝突
    c = get_db_connection()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    if search_invoice:
        cur.execute("SELECT * FROM orders WHERE invoice_number = %s ORDER BY id DESC", (search_invoice,))
    else:
        # 假設您的日期欄位叫做 created_at，若不同請自行調整
        cur.execute("SELECT * FROM orders WHERE DATE(created_at) = %s ORDER BY id DESC", (search_date,))
        
    orders = cur.fetchall()
    c.close()

    table_html = ""
    if orders:
        for order in orders:
            oid = order.get('id', '')
            c_name = order.get('customer_name') or '門市顧客'
            t_price = order.get('total_price', 0)
            
            # 💡 對齊您的 DB 欄位名稱
            inv_no = order.get('invoice_number') 
            inv_status = order.get('invoice_status')
            order_status = order.get('status')

            has_invoice = bool(inv_no)
            is_voided = (inv_status == 'Void')
            is_issued = (inv_status == 'Issued')
            is_cancelled = (order_status == 'Cancelled')

            # 狀態標籤
            if is_voided or is_cancelled:
                status_badge = '<span class="badge bg-danger">已作廢</span>'
            elif is_issued:
                status_badge = '<span class="badge bg-success">已開立</span>'
            else:
                status_badge = '<span class="badge bg-secondary">未開立</span>'

            # 按鈕 HTML 組裝
            buttons = f"""<div style="display:flex; flex-direction:column; gap:5px;">"""

            # 列印發票按鈕
            if has_invoice and is_issued:
                buttons += f"""<button onclick="window.open('/admin/print_invoice/{inv_no}', '_blank')" class='btn btn-outline-primary btn-sm' style='width:100%;'>🖨️ 列印發票</button>"""
            else:
                buttons += f"""<button disabled class='btn btn-outline-secondary btn-sm' style='width:100%;'>🖨️ 無發票可印</button>"""
                
            buttons += f"""
                <div style="display:flex; gap:5px;">
                    <button onclick='if(confirm("⚠️ 確定只要作廢發票，並將此單更改為【作廢狀態】嗎？")) action("/admin/cancel/{oid}")' class='btn btn-sm' style='flex:1; background:#f44336; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;' {'disabled' if is_cancelled else ''}>🗑️ 作廢訂單</button>
                    
                    <button onclick='if(confirm("⚠️ 確定要作廢發票並重新修改此單嗎？")) {{ fetch("/admin/cancel/{oid}").then(res => res.json()).then(data => {{ if(data.success){{ window.open("/menu?edit_oid={oid}&lang=zh", "_blank"); window.location.reload(); }} else {{ alert("作廢失敗：" + data.message); }} }}); }}' class='btn btn-sm' style='flex:1; background:#ff9800; color:white; border:none; border-radius:4px; padding:6px; cursor:pointer;' {'disabled' if is_cancelled else ''}>✏️ 作廢並修改</button>
                </div>
                <div style="display:flex; gap:5px;">
            """
            
            # 獨立發票操作
            if has_invoice and is_issued:
                buttons += f"""<button class="btn btn-sm btn-outline-danger" style="flex:1;" onclick="voidInvoice('{inv_no}')">🧾 僅作廢發票</button>"""
                
            if not has_invoice or is_voided:
                btn_text = '重開發票' if is_voided else '獨立開立'
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
