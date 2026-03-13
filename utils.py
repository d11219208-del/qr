import json
import urllib.request
import urllib.error
import threading
import time
import ssl
import traceback
from datetime import datetime, timedelta
from database import get_db_connection

# === 🛡️ 引入 Flask 與 functools 用於製作權限防護罩 ===
from flask import session, redirect, url_for, request, jsonify
from functools import wraps
from werkzeug.routing import BuildError  # 💡 處理沒有寫對應 logout 路由的情況

# ==========================================
# 0. 🛡️ 多重權限防護罩系統 (Decorators)
# ==========================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Unauthorized: 請先登入'}), 401
            
            bp = request.blueprint or ''
            if bp == 'admin':
                return redirect(url_for('admin.login'))
            elif bp == 'kitchen':
                return redirect(url_for('kitchen.login'))
            elif bp == 'try' or bp == 'try_debug':
                return redirect(url_for('try_debug.login')) 
            else:
                return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                bp = request.blueprint or ''
                if bp == 'kitchen':
                    return redirect(url_for('kitchen.login'))
                elif bp == 'try' or bp == 'try_debug':
                    return redirect(url_for('try_debug.login'))
                else:
                    return redirect(url_for('admin.login'))
            
            user_role = session.get('role', '')
            if user_role not in allowed_roles:
                return "<h3>❌ 權限不足：您的帳號沒有權限訪問此頁面！</h3> <a href='javascript:history.back()'>回上一頁</a>", 403
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==========================================
# 1. Email 報告發送核心 (User-Agent 修正版)
# ==========================================
def send_daily_report(app, manual_config=None, is_test=False):
    """發送日結報告"""
    conn = None
    cur = None
    
    with app.app_context():
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            
            if manual_config:
                config = manual_config
            else:
                cur.execute("SELECT key, value FROM settings")
                config = dict(cur.fetchall())

            api_key = config.get('resend_api_key', '').strip()
            to_email = config.get('report_email', '').strip()
            sender_email = (config.get('sender_email') or 'onboarding@resend.dev').strip()

            if not api_key or not to_email:
                print("❌ 未設定 Email 或 API Key，取消發送")
                return "❌ 設定不完整"

            utc_now = datetime.utcnow()
            tw_now = utc_now + timedelta(hours=8)
            today_str = tw_now.strftime('%Y-%m-%d')

            if is_test:
                subject = f"【測試】Resend API 設定確認 ({today_str})"
                email_content = f"✅ Resend API 連線成功！\n\n寄件者: {sender_email}\n收件者: {to_email}\n此為測試信件。"
            else:
                tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
                tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
                utc_start = tw_start - timedelta(hours=8)
                utc_end = tw_end - timedelta(hours=8)
                time_filter = f"created_at >= '{utc_start}' AND created_at <= '{utc_end}'"

                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
                v_res = cur.fetchone()
                v_count, v_total = (v_res[0] or 0), (v_res[1] or 0)

                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
                x_res = cur.fetchone()
                x_count, x_total = (x_res[0] or 0), (x_res[1] or 0)

                cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'")
                rows = cur.fetchall()
                stats = {}
                for r in rows:
                    if not r[0]: continue
                    try:
                        items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                        if isinstance(items, dict): items = [items]
                        for i in items:
                            name = i.get('name_zh', i.get('name', '未知'))
                            qty = int(i.get('qty', 0))
                            stats[name] = stats.get(name, 0) + qty
                    except: pass
                
                sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)
                item_text = "\n".join([f"• {k}: {v}" for k, v in sorted_stats]) if sorted_stats else "(無銷量)"

                subject = f"【日結單】{today_str} 營業報告"
                email_content = f"🍴 餐廳日結 ({today_str})\n------------------------\n✅ 有效: {v_count} 筆 (${int(v_total):,})\n{item_text}\n------------------------\n❌ 作廢: {x_count} 筆 (${int(x_total):,})"

            payload = {
                "from": sender_email,
                "to": [to_email],
                "subject": subject,
                "text": email_content
            }
            
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            headers = {
                "Authorization": f"Bearer {api_key}", 
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" 
            }

            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, context=ctx, timeout=10) as res:
                print(f"✅ Email 發送成功: {res.status}")
                return "✅ 發送成功"

        except Exception as e:
            print(f"❌ Email 任務出錯: {e}")
            return f"❌ 錯誤: {str(e)}"
        finally:
            if cur: cur.close()
            if conn: conn.close()

# ==========================================
# 2. 背景維護工作 (Aiven DB 連線優化版)
# ==========================================
def run_maintenance_tasks(app):
    print("⏳ 背景任務等待啟動中 (Wait 30s)...")
    time.sleep(30)
    print("🚀 背景維護執行緒已正式啟動")
    
    last_sent_time = ""
    next_ping_time = datetime.now()

    while True:
        try:
            now_obj = datetime.now()
            now_str = now_obj.strftime("%H:%M:%S")

            # --- A. 自動發信檢查 ---
            tw_time = datetime.utcnow() + timedelta(hours=8)
            current_hm = tw_time.strftime("%H:%M")
            target_times = ["13:00", "18:00", "20:30"]
            
            if current_hm in target_times and current_hm != last_sent_time:
                print(f"[{current_hm}] ⏰ 執行自動發信...")
                send_daily_report(app)
                last_sent_time = current_hm

            # --- B. 防休眠 Ping (Web + Aiven DB) ---
            if now_obj >= next_ping_time:
                # 1. Ping 網站
                try:
                    urllib.request.urlopen("https://qr-mbdv.onrender.com", timeout=5)
                    print(f"[{now_str}] ✅ Web Ping 成功")
                except Exception: 
                    print(f"[{now_str}] ⚠️ Web Ping 失敗: {e}")
                
                # 2. Ping Aiven 資料庫 (發送真實指令維持連線)
                try:
                    conn = get_db_connection()
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1;") # Aiven 需要實際 Query 才能保活
                        cur.fetchone()
                    conn.close()
                    print(f"[{now_str}] 💓 Aiven DB Heartbeat 成功 (SELECT 1)")
                except Exception as e: 
                    print(f"[{now_str}] ⚠️ DB Heartbeat 失敗: {e}")
                
                next_ping_time = now_obj + timedelta(seconds=300)

            time.sleep(30) # 縮短掃描間隔，確保不漏掉 target_times
        except Exception as e:
            print(f"⚠️ 背景任務主要迴圈錯誤: {e}")
            time.sleep(60)

def start_background_tasks(app):
    t = threading.Thread(target=run_maintenance_tasks, args=(app,), daemon=True)
    t.start()

# ==========================================
# 3. 👤 自動注入登入資訊 (Context Processor)
# ==========================================
def inject_user_info():
    current_username = session.get('username')
    current_role = session.get('role', '未知角色')
    current_bp = request.blueprint
    
    logout_url = None
    if current_username and current_bp:
        try:
            logout_url = url_for(f'{current_bp}.logout')
        except BuildError:
            logout_url = '#'

    return {
        'current_username': current_username,
        'current_role': current_role,
        'logout_url': logout_url
    }
