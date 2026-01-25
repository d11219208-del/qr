import io
import json
import ssl
import threading
import urllib.request
import traceback
from datetime import datetime, timedelta
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app

# 從資料庫模組匯入連線函式
from database import get_db_connection 

admin_bp = Blueprint('admin', __name__)

# --- 郵件發送核心功能 ---
def send_daily_report(manual_config=None, is_test=False):
    """發送日結報告核心邏輯"""
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
            email_content = "✅ Resend API 連線成功！"
        else:
            # (此處省略部分數據統計邏輯以節省篇幅，維持你原本的邏輯即可)
            subject = f"【日結單】{today_str} 營業統計報告"
            email_content = f"🍴 餐廳日結報表 ({today_str})"

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

# --- 路由：後台主面板 ---
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
            return redirect(url_for('admin.admin_panel', msg="✅ 設定已儲存"))
        
        elif action == 'add_product':
            cur.execute("""INSERT INTO products (name, price, category, print_category, image_url, name_en, name_jp, name_kr) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", 
                       (request.form.get('name'), request.form.get('price'), request.form.get('category'), 
                        request.form.get('print_category'), request.form.get('image_url'),
                        request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr')))
            conn.commit()
            return redirect(url_for('admin.admin_panel', msg="✅ 品項已新增"))

    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    # 確保查詢欄位與 admin.html 的 p[0], p[1] 索引對應
    cur.execute("SELECT id, name, price, category, is_available, print_category, sort_order, image_url, name_en, name_jp, name_kr FROM products ORDER BY sort_order ASC, id DESC")
    prods = cur.fetchall()
    conn.close()
    return render_template('admin.html', config=config, prods=prods, msg=msg)

# --- 路由：編輯產品 (修正 Blueprint 格式) ---
@admin_bp.route('/edit_product/<int:pid>', methods=['GET','POST'])
def edit_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        try:
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
            return redirect(url_for('admin.admin_panel', msg="✅ 產品已更新"))
        except Exception as e:
            conn.rollback()
            return f"Update Error: {e}"
        finally:
            conn.close()

    # 明確指定 SELECT 欄位順序
    sql_query = """
        SELECT 
            id, name, price, category, image_url, 
            custom_options, sort_order,
            name_en, name_jp, name_kr,
            custom_options_en, custom_options_jp, custom_options_kr,
            print_category,
            category_en, category_jp, category_kr
        FROM products WHERE id=%s
    """
    cur.execute(sql_query, (pid,))
    row = cur.fetchone()
    conn.close()
    
    if not row: return "找不到該產品", 404

    idx = {
        'id': 0, 'name': 1, 'price': 2, 'category': 3, 'image_url': 4,
        'custom_options': 5, 'sort_order': 6,
        'name_en': 7, 'name_jp': 8, 'name_kr': 9,
        'custom_options_en': 10, 'custom_options_jp': 11, 'custom_options_kr': 12,
        'print_category': 13,
        'category_en': 14, 'category_jp': 15, 'category_kr': 16
    }

    def v(key):
        val = row[idx[key]]
        return val if val is not None else ""

    # 使用 f-string 渲染 HTML (注意：在大括號中使用雙大括號 {{}} 逃避 CSS/JS)
    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>編輯產品</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    <style>
        body {{ padding: 20px; background: #f4f7f6; font-family: sans-serif; }}
        .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); max-width: 800px; margin: auto; }}
        h5 {{ background: #9b4dca; color: white; padding: 5px 10px; border-radius: 4px; margin-top: 20px; }}
        hr {{ margin: 30px 0; }}
    </style>
    </head>
    <body>
        <div class="container">
            <h3>📝 編輯產品 #{v('id')}</h3>
            <form method="POST">
                <h5>1. 基本資料 & 排序</h5>
                <div class="row">
                    <div class="column"><label>名稱 (中文)</label><input type="text" name="name" value="{v('name')}" required></div>
                    <div class="column"><label>價格</label><input type="number" name="price" value="{v('price')}" required></div>
                    <div class="column"><label>排序</label><input type="number" name="sort_order" value="{v('sort_order')}"></div>
                </div>
                <h5>2. 分類與區域</h5>
                <div class="row">
                    <div class="column"><label>分類 (中)</label><input type="text" name="category" value="{v('category')}"></div>
                    <div class="column"><label>出單區域</label>
                        <select name="print_category">
                            <option value="Noodle" {'selected' if v('print_category')=='Noodle' else ''}>麵區</option>
                            <option value="Soup" {'selected' if v('print_category')=='Soup' else ''}>湯區</option>
                        </select>
                    </div>
                </div>
                <h5>🌐 多國語言品名</h5>
                <div class="row">
                    <div class="column"><label>English</label><input type="text" name="name_en" value="{v('name_en')}"></div>
                    <div class="column"><label>日本語</label><input type="text" name="name_jp" value="{v('name_jp')}"></div>
                    <div class="column"><label>한국어</label><input type="text" name="name_kr" value="{v('name_kr')}"></div>
                </div>
                <div style="margin-top:30px; text-align: right;">
                    <a href="{url_for('admin.admin_panel')}" class="button button-outline">❌ 取消</a>
                    <button type="submit">💾 儲存變更</button>
                </div>
            </form>
        </div>
    </body></html>"""

# --- 其他功能路由 (切換、刪除、排序) ---
@admin_bp.route('/toggle_product/<int:pid>', methods=['POST'])
def toggle_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT is_available FROM products WHERE id = %s", (pid,))
    row = cur.fetchone()
    if row:
        new_s = not row[0]
        cur.execute("UPDATE products SET is_available = %s WHERE id = %s", (new_s, pid))
        conn.commit(); conn.close()
        return jsonify({'status': 'success', 'is_available': new_s})
    return jsonify({'status': 'error'}), 404

@admin_bp.route('/delete_product/<int:pid>')
def delete_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 產品已刪除"))

@admin_bp.route('/reorder_products', methods=['POST'])
def reorder_products():
    data = request.json
    conn = get_db_connection(); cur = conn.cursor()
    for idx, pid in enumerate(data.get('order', [])):
        cur.execute("UPDATE products SET sort_order = %s WHERE id = %s", (idx, pid))
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})
