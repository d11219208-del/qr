import io
import json
import ssl
import threading
import urllib.request
import traceback
from datetime import datetime, timedelta
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app

# 【關鍵修正】直接從 database 匯入，不要從 app 匯入
from database import get_db_connection 

admin_bp = Blueprint('admin', __name__)

# --- 郵件發送核心功能 ---

def send_daily_report(manual_config=None, is_test=False):
    """發送日結報告核心邏輯"""
    print(">>> 準備執行郵件發送程序...")
    conn = get_db_connection() # 這裡現在可以直接使用
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

        # 時間處理 (台灣轉 UTC)
        tw_now = datetime.utcnow() + timedelta(hours=8)
        today_str = tw_now.strftime('%Y-%m-%d')
        tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
        tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
        utc_start = tw_start - timedelta(hours=8)
        utc_end = tw_end - timedelta(hours=8)
        
        time_filter = f"created_at >= '{utc_start}' AND created_at <= '{utc_end}'"

        if is_test:
            subject = f"【連線測試】Resend API 設定確認 ({today_str})"
            email_content = "✅ Resend API 連線成功！"
        else:
            # 數據統計
            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            v_count, v_total = cur.fetchone()
            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
            x_count, x_total = cur.fetchone()

            # 品項統計
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
            if stats:
                for name, qty in sorted(stats.items(), key=lambda x: x[1], reverse=True):
                    item_detail += f"• {name}: {qty}\n"
            else:
                item_detail += "(今日尚無有效銷量)\n"

            subject = f"【日結單】{today_str} 營業統計報告"
            email_content = f"🍴 餐廳日結報表 ({today_str})\n單量：{v_count or 0}\n總額：${v_total or 0}\n{item_detail}"

        # API 呼叫
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

def async_send_report(app_instance, manual_config=None, is_test=False):
    threading.Thread(target=lambda: send_daily_report(manual_config, is_test)).start()

# --- 路由 ---

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
        elif action == 'test_email':
            send_daily_report(is_test=True)
            return redirect(url_for('admin.admin_panel', msg="🧪 測試信發送中"))

    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    cur.execute("SELECT id, name, price, category, is_available, print_category, sort_order FROM products ORDER BY sort_order ASC, id DESC")
    prods = cur.fetchall()
    conn.close()
    return render_template('admin.html', config=config, prods=prods, msg=msg)

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

@admin_bp.route('/delete_product/<int:pid>', methods=['POST'])
def delete_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 產品已刪除"))

@admin_bp.route('/export_menu')
def export_menu():
    try:
        conn = get_db_connection()
        df = pd.read_sql("SELECT name, price, category, is_available FROM products", conn)
        conn.close()
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="menu.xlsx")
    except Exception as e:
        return redirect(url_for('admin.admin_panel', msg=f"❌ 匯出失敗: {e}"))
