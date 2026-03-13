import json
import urllib.request
import urllib.error
import threading
import time
import ssl
import traceback
from datetime import datetime, timedelta
from database import get_db_connection

# === 🛡️ 引入 Flask 相關工具 ===
from flask import session, redirect, url_for, request, jsonify, has_request_context
from functools import wraps
from werkzeug.routing import BuildError

# ==========================================
# 0. 🛡️ 權限防護罩
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Unauthorized'}), 401
            bp = request.blueprint or ''
            target = 'try_debug.login' if bp in ['try', 'try_debug'] else f'{bp}.login'
            return redirect(url_for(target) if bp else url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                bp = request.blueprint or ''
                return redirect(url_for(f'{bp}.login' if bp else 'admin.login'))
            if session.get('role') not in allowed_roles:
                return "<h3>❌ 權限不足</h3>", 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==========================================
# 1. Email 報告發送核心 (含人員自動判定邏輯)
# ==========================================
def send_daily_report(app, manual_config=None, is_test=False):
    conn, cur = None, None
    
    with app.app_context():
        try:
            # 💡 人員邏輯：判斷是否有網頁請求上下文 (手動 vs 自動)
            operator_name = "系統自動發送"
            operator_role = "System"
            
            if has_request_context():
                # 如果是手動觸發，沿用測試信邏輯從 Session 抓值
                operator_name = session.get('username', '未知人員')
                operator_role = session.get('role', '未知角色')

            conn = get_db_connection()
            cur = conn.cursor()
            
            # 讀取設定
            if manual_config:
                config = manual_config
            else:
                cur.execute("SELECT key, value FROM settings")
                config = dict(cur.fetchall())

            api_key = config.get('resend_api_key', '').strip()
            to_email = config.get('report_email', '').strip()
            sender_email = (config.get('sender_email') or 'onboarding@resend.dev').strip()

            if not api_key or not to_email:
                print("⚠️ Email 設定不完整，取消任務")
                return "❌ 設定不完整"

            tw_now = datetime.utcnow() + timedelta(hours=8)
            today_str = tw_now.strftime('%Y-%m-%d')

            if is_test:
                subject = f"【測試】Resend API 連線確認 ({today_str})"
                email_content = (
                    f"👤 值班人員: {operator_name} ({operator_role})\n"
                    f"------------------------\n"
                    f"✅ 連線測試成功！\n"
                    f"寄件者: {sender_email}\n"
                    f"收件者: {to_email}"
                )
            else:
                # 修正時間區間：確保涵蓋整天 (台灣時間 00:00 ~ 23:59)
                tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
                utc_start = tw_start - timedelta(hours=8)
                utc_end = utc_start + timedelta(hours=24)
                
                time_filter = "created_at >= %s AND created_at < %s"
                params = (utc_start, utc_end)

                # 1. 處理有效訂單
                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'", params)
                v_res = cur.fetchone()
                v_count = v_res[0] or 0
                v_total = float(v_res[1] or 0)

                cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'", params)
                v_rows = cur.fetchall()
                v_stats = {}
                for r in v_rows:
                    try:
                        items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                        if isinstance(items, dict): items = [items]
                        for i in items:
                            name = i.get('name_zh', i.get('name', '未知'))
                            v_stats[name] = v_stats.get(name, 0) + int(i.get('qty', 0))
                    except: continue

                # 2. 處理作廢訂單
                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'", params)
                x_res = cur.fetchone()
                x_count = x_res[0] or 0
                x_total = float(x_res[1] or 0)

                cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status = 'Cancelled'", params)
                x_rows = cur.fetchall()
                x_stats = {}
                for r in x_rows:
                    try:
                        items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                        if isinstance(items, dict): items = [items]
                        for i in items:
                            name = i.get('name_zh', i.get('name', '未知'))
                            x_stats[name] = x_stats.get(name, 0) + int(i.get('qty', 0))
                    except: continue

                # 格式化輸出文字
                v_text = "\n".join([f"• {k}: {v}" for k, v in sorted(v_stats.items(), key=lambda x:x[1], reverse=True)]) or "(無銷量)"
                x_text = "\n".join([f"• {k}: {v}" for k, v in sorted(x_stats.items(), key=lambda x:x[1], reverse=True)]) or "(無作廢)"

                subject = f"【日結單】{today_str} 營業報告"
                email_content = (
                    f"👤 值班人員: {operator_name} ({operator_role})\n"
                    f"🍴 餐廳日結 ({today_str})\n"
                    f"------------------------\n"
                    f"✅ 有效: {v_count} 筆 (${int(v_total):,})\n"
                    f"{v_text}\n"
                    f"------------------------\n"
                    f"❌ 作廢: {x_count} 筆 (${int(x_total):,})\n"
                    f"{x_text}\n"
                    f"------------------------\n"
                    f"💰 實收總計: ${int(v_total):,}"
                )

            # --- Resend API 發送邏輯 ---
            payload = {"from": sender_email, "to": [to_email], "subject": subject, "text": email_content}
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0" # 模擬瀏覽器避免 403
            }
            
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE # 解決雲端環境 SSL 憑證問題

            req = urllib.request.Request("https://api.resend.com/emails", 
                                          data=json.dumps(payload).encode('utf-8'), 
                                          headers=headers, method='POST')
            
            with urllib.request.urlopen(req, context=ctx, timeout=15) as res:
                print(f"✅ Email 發送成功，回應碼: {res.status}")
                return "✅ 發送成功"

        except Exception as e:
            print(f"❌ Email 任務嚴重錯誤: {str(e)}")
            traceback.print_exc() # 會在日誌顯示具體哪一行報錯
            return f"❌ 錯誤: {str(e)}"
        finally:
            if cur: cur.close()
            if conn: conn.close()

# ==========================================
# 2. 背景任務執行緒 (Aiven DB 穩定版)
# ==========================================
def run_maintenance_tasks(app):
    time.sleep(10) # 延遲啟動確保資料庫就緒
    print("🚀 背景維護執行緒已啟動")
    last_sent_time = ""
    next_ping_time = datetime.now()

    while True:
        try:
            now_obj = datetime.now()
            tw_time = datetime.utcnow() + timedelta(hours=8)
            current_hm = tw_time.strftime("%H:%M")
            
            # A. 定時發信檢查
            if current_hm in ["13:00", "18:00", "20:30", "09:25"] and current_hm != last_sent_time:
                print(f"⏰ 到達發信時間 {current_hm}，執行 send_daily_report...")
                send_daily_report(app)
                last_sent_time = current_hm

            # B. 每 5 分鐘執行一次資料庫與網頁保活
            if now_obj >= next_ping_time:
                try:
                    # Ping 網頁
                    urllib.request.urlopen("https://ding-dong-tipi.onrender.com", timeout=5)
                    # Ping 資料庫 (保活 Aiven)
                    conn = get_db_connection()
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1;")
                    conn.close()
                    print(f"[{now_obj.strftime('%H:%M:%S')}] 💓 心跳保活成功")
                except: pass
                next_ping_time = now_obj + timedelta(seconds=300)

            time.sleep(30)
        except Exception as e:
            print(f"⚠️ 背景迴圈異常: {e}")
            time.sleep(60)

def start_background_tasks(app):
    t = threading.Thread(target=run_maintenance_tasks, args=(app,), daemon=True)
    t.start()

# ==========================================
# 3. 模板全域變數注入 (Navbar 資訊)
# ==========================================
def inject_user_info():
    current_username = session.get('username')
    current_bp = request.blueprint
    try:
        logout_url = url_for(f'{current_bp}.logout') if current_username and current_bp else '#'
    except:
        logout_url = '#'
    return {
        'current_username': current_username,
        'current_role': session.get('role', '未知角色'),
        'logout_url': logout_url
    }
