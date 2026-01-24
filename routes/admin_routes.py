import io
import json
import ssl
import threading
import urllib.request
from datetime import datetime, timedelta
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app

# 假設您的 db 連線函式在 utils 或 app.py 中
# from app import get_db_connection 

admin_bp = Blueprint('admin', __name__)

# --- 郵件發送核心功能 ---

def send_daily_report(manual_config=None, is_test=False):
    # (此處省略您提供的完整 send_daily_report 邏輯，代碼同您提供的內容)
    # ... (請將原始碼中的 send_daily_report 內容貼於此)
    pass

def async_send_report(app_instance, manual_config=None, is_test=False):
    with app_instance.app_context():
        send_daily_report(manual_config, is_test)

# --- 路由功能 ---

@admin_bp.route('/admin')
def admin_panel():
    conn = get_db_connection()
    cur = conn.cursor()
    msg = request.args.get('msg', '')
    
    # 處理 POST 動作
    if request.method == 'POST':
        # ... (處理 save_settings, test_email, send_report_now, add_product 邏輯)
        pass

    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    cur.execute("SELECT id, name, price, category, is_available, print_category, sort_order, image_url FROM products ORDER BY sort_order ASC, id DESC")
    prods = cur.fetchall()
    conn.close()

    # 將資料傳遞給 template
    return render_template('admin.html', config=config, prods=prods, msg=msg)

@admin_bp.route('/admin/export_menu')
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

@admin_bp.route('/admin/toggle_product/<int:pid>', methods=['POST'])
def toggle_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_available FROM products WHERE id = %s", (pid,))
    row = cur.fetchone()
    if row:
        new_s = not row[0]
        cur.execute("UPDATE products SET is_available = %s WHERE id = %s", (new_s, pid))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'is_available': new_s})
    conn.close()
    return jsonify({'status': 'error'}), 404

# ... 其餘路由如 delete_product, reorder_products, edit_product 請依此類推放入 ...
