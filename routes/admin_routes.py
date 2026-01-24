import io
import json
import ssl
import threading
import urllib.request
import traceback
from datetime import datetime, timedelta
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app

# 初始化 Blueprint
# 配合您的 app.py：app.register_blueprint(admin_bp, url_prefix='/admin')
admin_bp = Blueprint('admin', __name__)

# --- 郵件發送核心功能 ---

def send_daily_report(manual_config=None, is_test=False):
    """
    發送日結報告核心邏輯。
    manual_config: 測試時傳入的臨時設定 (dict)
    is_test: 是否僅為連線測試信
    """
    print(">>> 啟動郵件發送程序...")
    try:
        from app import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        
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
            print("❌ 發送失敗：缺少 API Key 或 收件信箱")
            return "❌ 未設定 Email 或 API Key"

        # 2. 時間區間處理 (台灣時間轉 UTC)
        utc_now = datetime.utcnow()
        tw_now = utc_now + timedelta(hours=8)
        today_str = tw_now.strftime('%Y-%m-%d')
        
        tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
        tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # 轉回 UTC 以對應資料庫儲存的時間
        utc_start_query = tw_start - timedelta(hours=8)
        utc_end_query = tw_end - timedelta(hours=8)
        time_filter = f"created_at >= '{utc_start_query}' AND created_at <= '{utc_end_query}'"

        if is_test:
            subject = f"【連線測試】Resend API 設定確認 ({today_str})"
            email_content = "✅ Resend API 連線成功！\n您的餐廳系統已準備好發送每日報表。"
        else:
            # 抓取統計數據
            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            v_count, v_total = cur.fetchone()
            
            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
            x_count, x_total = cur.fetchone()

            # 抓取並解析品項銷量
            cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            valid_rows = cur.fetchall()
            
            stats = {}
            for r in valid_rows:
                if not r[0]: continue
                try:
                    items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                    if isinstance(items, dict): items = [items] # 防呆
                    for i in items:
                        name = i.get('name_zh', i.get('name', '未知'))
                        qty = int(float(i.get('qty', 0)))
                        stats[name] = stats.get(name, 0) + qty
                except: continue

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
總額：${v_total or 0}
{item_detail_text}
---------------------------------
❌ 【作廢統計】
單量：{x_count or 0} 筆
總額：${x_total or 0}
---------------------------------
報告產出時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')} (TW)
資料統計區間：今日 00:00 ~ 23:59 (TW)
            """

        # 3. 執行 HTTPS 請求發送
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
            print(f"✅ 郵件發送成功: {res.status}")
            return "✅ 成功"
            
    except Exception as e:
        traceback.print_exc()
        return f"❌ 錯誤: {str(e)}"
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

def async_send_report(app_instance, manual_config=None, is_test=False):
    """異步發送，防止 Flask 請求卡死"""
    def run():
        with app_instance.app_context():
            send_daily_report(manual_config, is_test)
    threading.Thread(target=run).start()

# --- 路由功能 ---

# 注意：因為 app.py 註冊為 /admin，所以這裡寫 '/' 就代表訪問 /admin
@admin_bp.route('/', methods=['GET', 'POST'])
def admin_panel():
    from app import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    msg = request.args.get('msg', '')
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'save_settings':
            settings_to_save = {
                'report_email': request.form.get('report_email'),
                'sender_email': request.form.get('sender_email'),
                'resend_api_key': request.form.get('resend_api_key')
            }
            for key, val in settings_to_save.items():
                cur.execute("""
                    INSERT INTO settings (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key, val.strip()))
            conn.commit()
            return redirect(url_for('admin.admin_panel', msg="✅ 設定已儲存"))
        
        elif action == 'test_email':
            temp_config = {
                'report_email': request.form.get('report_email'),
                'sender_email': request.form.get('sender_email'),
                'resend_api_key': request.form.get('resend_api_key')
            }
            # 測試信建議同步執行，讓使用者能立即在介面看到是否連線成功
            result = send_daily_report(temp_config, is_test=True)
            return redirect(url_for('admin.admin_panel', msg=result))

        elif action == 'send_report_now':
            async_send_report(current_app._get_current_object())
            return redirect(url_for('admin.admin_panel', msg="📊 報表發送指令已下達，請稍候查收"))

    # 讀取現有資料
    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    cur.execute("""
        SELECT id, name, price, category, is_available, print_category, sort_order 
        FROM products 
        ORDER BY sort_order ASC, id DESC
    """)
    prods = cur.fetchall()
    conn.close()
    return render_template('admin.html', config=config, prods=prods, msg=msg)

@admin_bp.route('/toggle_product/<int:pid>', methods=['POST'])
def toggle_product(pid):
    from app import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_available FROM products WHERE id = %s", (pid,))
    row = cur.fetchone()
    if row:
        new_status = not row[0]
        cur.execute("UPDATE products SET is_available = %s WHERE id = %s", (new_status, pid))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'is_available': new_status})
    conn.close()
    return jsonify({'status': 'error'}), 404

@admin_bp.route('/delete_product/<int:pid>', methods=['POST'])
def delete_product(pid):
    from app import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 產品已刪除"))

@admin_bp.route('/export_menu')
def export_menu():
    try:
        from app import get_db_connection
        conn = get_db_connection()
        df = pd.read_sql("SELECT name, price, category, is_available, print_category, sort_order FROM products ORDER BY sort_order ASC", conn)
        conn.close()
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Menu')
        output.seek(0)
        
        return send_file(
            output, 
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            as_attachment=True, 
            download_name=f"menu_export_{datetime.now().strftime('%Y%m%d')}.xlsx"
        )
    except Exception as e:
        return redirect(url_for('admin.admin_panel', msg=f"❌ 匯出失敗: {e}"))
