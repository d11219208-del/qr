import io
import json
import ssl
import threading
import urllib.request
import traceback
from datetime import datetime, timedelta
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app

# 確保 database.py 存在且 get_db_connection 可用
from database import get_db_connection 

admin_bp = Blueprint('admin', __name__)

# --- 郵件發送核心功能 ---

def send_daily_report(manual_config=None, is_test=False):
    """發送日結報告核心邏輯"""
    print(">>> 準備執行郵件發送程序...")
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        if manual_config:
            config = manual_config
        else:
            cur.execute("SELECT key, value FROM settings")
            config = dict(cur.fetchall())

        api_key = config.get('resend_api_key', '').strip()
        to_email = config.get('report_email', '').strip()
        sender_email = config.get('sender_email', 'onboarding@resend.dev').strip()

        if not api_key or not to_email:
            return "❌ 未設定 Email 或 API Key"

        tw_now = datetime.utcnow() + timedelta(hours=8)
        today_str = tw_now.strftime('%Y-%m-%d')
        
        if is_test:
            subject = f"【連線測試】Resend API 設定確認 ({today_str})"
            email_content = "✅ Resend API 連線成功！此為測試信件。"
        else:
            tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
            tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
            utc_start = tw_start - timedelta(hours=8)
            utc_end = tw_end - timedelta(hours=8)
            time_filter = f"created_at >= '{utc_start}' AND created_at <= '{utc_end}'"

            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            v_count, v_total = cur.fetchone()
            
            cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            valid_rows = cur.fetchall()
            stats = {}
            for r in valid_rows:
                if not r[0]: continue
                try:
                    items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                    if isinstance(items, dict): items = [items]
                    for i in items:
                        name = i.get('name_zh', i.get('name', '未知'))
                        qty = int(float(i.get('qty', 0)))
                        stats[name] = stats.get(name, 0) + qty
                except: continue

            item_detail = "\n【品項銷量統計】\n"
            for name, qty in sorted(stats.items(), key=lambda x: x[1], reverse=True):
                item_detail += f"• {name}: {qty} 份\n"

            subject = f"【日結單】{today_str} 營業統計報告"
            email_content = f"🍴 餐廳日結報表 ({today_str})\n------------------\n單量：{v_count or 0}\n總額：${v_total or 0}\n{item_detail}"

        payload = {"from": sender_email, "to": [to_email], "subject": subject, "text": email_content}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            "https://api.resend.com/emails", 
            data=json.dumps(payload).encode('utf-8'),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, 
            method='POST'
        )
        with urllib.request.urlopen(req, context=ctx) as res:
            return "✅ 成功"
    except Exception as e:
        traceback.print_exc()
        return f"❌ 錯誤: {str(e)}"
    finally:
        cur.close(); conn.close()

# --- 路由功能 ---

@admin_bp.route('/', methods=['GET', 'POST'])
def admin_panel():
    conn = get_db_connection()
    cur = conn.cursor()
    msg = request.args.get('msg', '')
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save_settings':
            for k in ['report_email', 'sender_email', 'resend_api_key']:
                cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (k, request.form.get(k, '').strip()))
            conn.commit()
            msg = "✅ 設定已儲存"
        elif action == 'test_email':
            threading.Thread(target=send_daily_report, kwargs={'is_test': True}).start()
            msg = "🧪 測試信發送中"
        elif action == 'add_product':
            cur.execute("""INSERT INTO products (name, price, category, print_category, image_url, name_en, name_jp, name_kr, custom_options) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                       (request.form.get('name'), request.form.get('price'), request.form.get('category'), 
                        request.form.get('print_category'), request.form.get('image_url'),
                        request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'),
                        request.form.get('custom_options')))
            conn.commit()
            msg = "✅ 新增成功"

    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    cur.execute("SELECT id, name, price, category, is_available, print_category, sort_order, image_url, name_en, name_jp, name_kr FROM products ORDER BY sort_order ASC, id DESC")
    prods = cur.fetchall()
    conn.close()
    return render_template('admin.html', config=config, prods=prods, msg=msg)

@admin_bp.route('/edit_product/<int:pid>', methods=['GET', 'POST'])
def edit_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        cur.execute("""
            UPDATE products SET 
            name=%s, price=%s, category=%s, image_url=%s, custom_options=%s,
            name_en=%s, name_jp=%s, name_kr=%s,
            custom_options_en=%s, custom_options_jp=%s, custom_options_kr=%s,
            print_category=%s, sort_order=%s,
            category_en=%s, category_jp=%s, category_kr=%s
            WHERE id=%s
        """, (
            request.form.get('name'), request.form.get('price'), request.form.get('category'),
            request.form.get('image_url'), request.form.get('custom_options'),
            request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'),
            request.form.get('custom_options_en'), request.form.get('custom_options_jp'), request.form.get('custom_options_kr'),
            request.form.get('print_category'), request.form.get('sort_order'),
            request.form.get('category_en'), request.form.get('category_jp'), request.form.get('category_kr'),
            pid
        ))
        conn.commit()
        conn.close()
        return redirect(url_for('admin.admin_panel', msg="✅ 產品已更新"))

    # 明確指定欄位順序以避免錯位
    sql = "SELECT id, name, price, category, image_url, custom_options, sort_order, name_en, name_jp, name_kr, custom_options_en, custom_options_jp, custom_options_kr, print_category, category_en, category_jp, category_kr FROM products WHERE id=%s"
    cur.execute(sql, (pid,))
    row = cur.fetchone()
    conn.close()
    
    if not row: return "Product not found", 404
    
    # 將元組轉為字典方便 template 讀取
    cols = ['id','name','price','category','image_url','custom_options','sort_order','name_en','name_jp','name_kr','custom_options_en','custom_options_jp','custom_options_kr','print_category','category_en','category_jp','category_kr']
    p = dict(zip(cols, row))
    
    return render_template('edit_product.html', p=p) # 建議另外存成獨立檔案，或使用原本 HTML 內容

@admin_bp.route('/toggle_product/<int:pid>', methods=['POST'])
def toggle_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_available FROM products WHERE id = %s", (pid,))
    row = cur.fetchone()
    if row:
        new_s = not row[0]
        cur.execute("UPDATE products SET is_available = %s WHERE id = %s", (new_s, pid))
        conn.commit(); conn.close()
        return jsonify({'status': 'success', 'is_available': new_s})
    conn.close()
    return jsonify({'status': 'error'}), 404

@admin_bp.route('/delete_product/<int:pid>')
def delete_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit(); conn.close()
    return redirect(url_for('admin.admin_panel', msg=f"🗑️ 產品已刪除"))

@admin_bp.route('/reorder_products', methods=['POST'])
def reorder_products():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    for idx, pid in enumerate(data.get('order', [])):
        cur.execute("UPDATE products SET sort_order = %s WHERE id = %s", (idx, pid))
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

@admin_bp.route('/export_menu')
def export_menu():
    try:
        conn = get_db_connection()
        df = pd.read_sql("SELECT * FROM products ORDER BY sort_order ASC", conn)
        conn.close()
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="menu_export.xlsx")
    except Exception as e:
        return redirect(url_for('admin.admin_panel', msg=f"❌ 匯出失敗: {e}"))

@admin_bp.route('/import_menu', methods=['POST'])
def import_menu():
    try:
        file = request.files.get('menu_file')
        if not file: return redirect(url_for('admin.admin_panel', msg="❌ 無檔案"))
        df = pd.read_excel(file).where(pd.notnull(pd.read_excel(file)), None)
        conn = get_db_connection()
        cur = conn.cursor()
        for _, p in df.iterrows():
            if not p.get('name'): continue
            cur.execute("""
                INSERT INTO products (name, price, category, print_category, sort_order, is_available, image_url, name_en, name_jp, name_kr, category_en, category_jp, category_kr, custom_options, custom_options_en, custom_options_jp, custom_options_kr)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (str(p.get('name')), p.get('price',0), p.get('category'), p.get('print_category','Noodle'), p.get('sort_order',99), True, p.get('image_url'), p.get('name_en'), p.get('name_jp'), p.get('name_kr'), p.get('category_en'), p.get('category_jp'), p.get('category_kr'), p.get('custom_options'), p.get('custom_options_en'), p.get('custom_options_jp'), p.get('custom_options_kr')))
        conn.commit(); conn.close()
        return redirect(url_for('admin.admin_panel', msg="✅ 匯入成功"))
    except Exception as e:
        return redirect(url_for('admin.admin_panel', msg=f"❌ 匯入失敗: {e}"))

@admin_bp.route('/reset_menu')
def reset_menu():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("TRUNCATE TABLE products RESTART IDENTITY CASCADE")
    conn.commit(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 菜單已清空"))

@admin_bp.route('/reset_orders')
def reset_orders():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("TRUNCATE TABLE orders RESTART IDENTITY CASCADE")
    conn.commit(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="💥 訂單已清空"))
