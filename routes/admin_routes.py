import io
import json
import ssl
import threading
import urllib.request
import traceback
from datetime import datetime, timedelta
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app

# 確保從你的主程式導入資料庫連線
# from app import get_db_connection 

admin_bp = Blueprint('admin', __name__)

# --- 郵件發送核心功能 ---

def send_daily_report(manual_config=None, is_test=False):
    """
    發送日結報告。
    manual_config: 測試時傳入的臨時設定
    is_test: 是否為連線測試信
    """
    print(">>> 準備執行郵件發送程序...")
    from app import get_db_connection # 延遲導入避免循環引用
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. 取得設定
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

        # 2. 時間區間處理 (台灣時間轉 UTC)
        utc_now = datetime.utcnow()
        tw_now = utc_now + timedelta(hours=8)
        today_str = tw_now.strftime('%Y-%m-%d')
        
        tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
        tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        utc_start_query = tw_start - timedelta(hours=8)
        utc_end_query = tw_end - timedelta(hours=8)
        time_filter = f"created_at >= '{utc_start_query}' AND created_at <= '{utc_end_query}'"

        if is_test:
            subject = f"【連線測試】Resend API 設定確認 ({today_str})"
            email_content = "✅ Resend API 連線成功！\n此為測試信件。"
        else:
            # 抓取統計數據
            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            v_count, v_total = cur.fetchone()
            
            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
            x_count, x_total = cur.fetchone()

            # 抓取品項明細
            cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            valid_rows = cur.fetchall()
            
            stats = {}
            for r in valid_rows:
                if not r[0]: continue
                try:
                    items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                    # 確保 items 是串列
                    if isinstance(items, dict): items = [items]
                    for i in items:
                        name = i.get('name_zh', i.get('name', '未知'))
                        qty = int(float(i.get('qty', 0)))
                        stats[name] = stats.get(name, 0) + qty
                except: pass

            item_detail_text = "\n【品項銷量統計】\n"
            if stats:
                for name, qty in sorted(stats.items(), key=lambda x: x[1], reverse=True):
                    item_detail_text += f"• {name}: {qty}\n"
            else:
                item_detail_text += "(今日尚無有效銷量)\n"

            subject = f"【日結單】{today_str} 營業統計報告"
            email_content = f"""
🍴 餐廳日結報表 ({today_str})
---------------------------------
✅ 【有效營收】
單量：{v_count or 0} 筆
總額：${v_total or 0}{item_detail_text}
---------------------------------
❌ 【作廢統計】
單量：{x_count or 0} 筆
總額：${x_total or 0}
---------------------------------
報告產出時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')} (TW)
資料統計區間：00:00 ~ 23:59 (TW)
            """

        # 3. 呼叫 Resend API
        payload = {
            "from": sender_email,
            "to": [to_email],
            "subject": subject,
            "text": email_content
        }
        
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
            print(f"✅ 發送成功: {res.status}")
            return "✅ 成功"
            
    except Exception as e:
        traceback.print_exc()
        return f"❌ 錯誤: {str(e)}"
    finally:
        cur.close()
        conn.close()

def async_send_report(app_instance, manual_config=None, is_test=False):
    def run():
        with app_instance.app_context():
            send_daily_report(manual_config, is_test)
    threading.Thread(target=run).start()

# --- 路由 ---

@admin_bp.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    from app import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    msg = request.args.get('msg', '')
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save_settings':
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ('report_email', request.form.get('report_email')))
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ('sender_email', request.form.get('sender_email')))
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ('resend_api_key', request.form.get('resend_api_key')))
            conn.commit()
            return redirect(url_for('admin.admin_panel', msg="✅ 設定已儲存"))
        
        elif action == 'test_email':
            temp_config = {
                'report_email': request.form.get('report_email'),
                'sender_email': request.form.get('sender_email'),
                'resend_api_key': request.form.get('resend_api_key')
            }
            async_send_report(current_app._get_current_object(), temp_config, True)
            return redirect(url_for('admin.admin_panel', msg="🧪 測試信發送中"))

        elif action == 'send_report_now':
            async_send_report(current_app._get_current_object())
            return redirect(url_for('admin.admin_panel', msg="📊 報表發送中"))

    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    cur.execute("SELECT id, name, price, category, is_available, print_category, sort_order, image_url FROM products ORDER BY sort_order ASC, id DESC")
    prods = cur.fetchall()
    conn.close()
    return render_template('admin.html', config=config, prods=prods, msg=msg)

# 其他路由 (export_menu, toggle_product 等) 請保持在 admin_bp 之下...
