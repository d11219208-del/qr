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

# ==========================================
# 0. 🛡️ 多重權限防護罩系統 (Decorators)
# ==========================================

def login_required(f):
    """
    【通用登入防護罩 - 智慧版】
    只要加上 @login_required，就會檢查是否登入。
    會根據使用者當下訪問的藍圖 (Blueprint)，自動導向對應的登入頁面！
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # 1. 處理前端 JS (Fetch/AJAX) 打 API 的狀況
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Unauthorized: 請先登入'}), 401
            
            # 2. 智慧導向：根據現在的 Blueprint 名稱決定踢去哪裡
            bp = request.blueprint or ''
            
            if bp == 'admin':
                return redirect(url_for('admin.login'))
            elif bp == 'kitchen':
                # 假設你的廚房端有自己的 login，名稱為 kitchen.login
                # 如果沒有，可以先統一導向 admin.login
                return redirect(url_for('kitchen.login'))
            elif bp == 'try' or bp == 'try_debug':
                # 假設你的測試/資料庫端 login
                return redirect(url_for('admin.login')) 
            else:
                # 預設的最後防線
                return redirect(url_for('admin.login'))
                
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    """
    【進階角色防護罩】
    用法範例： @role_required('admin') 或 @role_required('admin', 'staff')
    除了檢查有沒有登入，還會檢查「身分」對不對！
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 1. 先確認有沒有登入
            if 'user_id' not in session:
                return redirect(url_for('admin.login'))
            
            # 2. 確認角色是否在允許的名單內
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
            
            # 決定設定來源
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

            # 準備時間與內容
            utc_now = datetime.utcnow()
            tw_now = utc_now + timedelta(hours=8)
            today_str = tw_now.strftime('%Y-%m-%d')

            if is_test:
                subject = f"【測試】Resend API 設定確認 ({today_str})"
                email_content = f"✅ Resend API 連線成功！\n\n寄件者: {sender_email}\n收件者: {to_email}\n此為測試信件。"
            else:
                # 抓取正式數據
                tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
                tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
                utc_start = tw_start - timedelta(hours=8)
                utc_end = tw_end - timedelta(hours=8)
                time_filter = f"created_at >= '{utc_start}' AND created_at <= '{utc_end}'"

                # 統計數據
                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
                v_res = cur.fetchone()
                v_count, v_total = (v_res[0] or 0), (v_res[1] or 0)

                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
                x_res = cur.fetchone()
                x_count, x_total = (x_res[0] or 0), (x_res[1] or 0)

                # 品項統計
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
                email_content = f"""
🍴 餐廳日結 ({today_str})
------------------------
✅ 有效: {v_count} 筆 (${int(v_total):,})
{item_text}
------------------------
❌ 作廢: {x_count} 筆 (${int(x_total):,})
"""

            # 發送請求
            payload = {
                "from": sender_email,
                "to": [to_email],
                "subject": subject,
                "text": email_content
            }
            
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            # 加入 User-Agent 偽裝成瀏覽器
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
            
            print(f"📡 正在連線 Resend API (From: {sender_email} -> To: {to_email}) ...")
            
            with urllib.request.urlopen(req, context=ctx, timeout=10) as res:
                print(f"✅ Email 發送成功: {res.status}")
                return "✅ 發送成功"

        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode('utf-8')
            except:
                error_body = "無法讀取錯誤內容"

            print(f"❌ Resend API 拒絕連線 (HTTP {e.code}): {error_body}")
            
            if e.code == 403 and "1010" in error_body:
                 return "❌ 發送失敗: 被 Cloudflare 防火牆阻擋 (User-Agent)"

            try:
                err_json = json.loads(error_body)
                msg = err_json.get('message', error_body)
                return f"❌ 發送失敗: {msg}"
            except:
                return f"❌ 發送失敗 (HTTP {e.code})"

        except Exception as e:
            traceback.print_exc()
            return f"❌ 程式錯誤: {str(e)}"
        
        finally:
            if cur: cur.close()
            if conn: conn.close()

# ==========================================
# 2. 背景維護工作 (修正 Print 顯示)
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

            # --- B. 防休眠 Ping ---
            if now_obj >= next_ping_time:
                # 1. Ping 網站
                try:
                    urllib.request.urlopen("https://qr-mbdv.onrender.com/", timeout=5)
                    print(f"[{now_str}] ✅ Web Ping 成功")
                except Exception: 
                    pass 
                
                # 2. Ping 資料庫 (維持連線)
                try:
                    conn = get_db_connection()
                    conn.close()
                    print(f"[{now_str}] 💓 DB Heartbeat 成功")
                except Exception: 
                    pass
                
                # 設定下次 Ping 的時間 (5分鐘後)
                next_ping_time = now_obj + timedelta(seconds=300)

            time.sleep(60)
        except Exception as e:
            print(f"⚠️ 背景任務錯誤: {e}")
            time.sleep(60)

def start_background_tasks(app):
    t = threading.Thread(target=run_maintenance_tasks, args=(app,), daemon=True)
    t.start()
