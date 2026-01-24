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
    # --- Email 報告發送邏輯 (改用 UTC 時間範圍精準鎖定) ---
def send_daily_report():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT key, value FROM settings")
        config = dict(cur.fetchall())
        api_key = config.get('resend_api_key', '').strip()
        to_email = config.get('report_email', '').strip()
        if not api_key or not to_email: return "❌ 未設定 Email 或 API Key"

        # --- 【核心修正】改用時間範圍查詢 (Range Query) ---
        
        # 1. 取得現在的台灣時間
        utc_now = datetime.utcnow()
        tw_now = utc_now + timedelta(hours=8)
        
        # 2. 取得「台灣今天」的 00:00:00 和 23:59:59
        # 例如：如果是 1月20日，起點就是 2026-01-20 00:00:00
        tw_start_of_day = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
        tw_end_of_day = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)

        # 3. 將這兩個時間點「減 8 小時」轉回 UTC
        # 因為資料庫(Render)裡面存的是 UTC 時間
        # 例如：台灣 00:00 其實是前一天的 UTC 16:00
        utc_start_query = tw_start_of_day - timedelta(hours=8)
        utc_end_query = tw_end_of_day - timedelta(hours=8)

        # 4. 建立 SQL 篩選條件
        # 語法解釋：created_at 必須在 "計算好的UTC起始時間" 與 "計算好的UTC結束時間" 之間
        time_filter = f"created_at >= '{utc_start_query}' AND created_at <= '{utc_end_query}'"

        # --- 以下邏輯維持不變，但 SQL 查詢會引用新的 time_filter ---

        # 1. 抓取統計數據
        cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
        v_count, v_total = cur.fetchone()
        
        cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
        x_count, x_total = cur.fetchone()

        # 2. 抓取品項明細
        cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'")
        valid_rows = cur.fetchall()
        
        def agg_items(rows):
            stats = {}
            for r in rows:
                if not r[0]: continue
                try:
                    items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                    for i in items:
                        name = i.get('name_zh', i.get('name', '未知'))
                        qty = int(i.get('qty', 0))
                        stats[name] = stats.get(name, 0) + qty
                except: pass
            return stats

        valid_stats = agg_items(valid_rows)
        
        # 3. 組裝 Email 文字
        today_str = tw_now.strftime('%Y-%m-%d') # 信件標題用的日期 (台灣日期)
        
        item_detail_text = ""
        if valid_stats:
            item_detail_text = "\n【品項銷量統計】\n"
            for name, qty in sorted(valid_stats.items(), key=lambda x: x[1], reverse=True):
                item_detail_text += f"• {name}: {qty}\n"
        else:
            item_detail_text = "\n(今日尚無有效銷量)\n"

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
報告產出時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')} (Taiwan Time)
資料統計區間：{tw_start_of_day.strftime('%H:%M')} ~ {tw_end_of_day.strftime('%H:%M')}
        """

        # 4. 發送
        payload = {
            "from": config.get('sender_email', 'onboarding@resend.dev').strip(),
            "to": [to_email],
            "subject": f"【日結單】{today_str} 營業統計報告",
            "text": email_content
        }
        
        req = urllib.request.Request(
            "https://api.resend.com/emails", 
            data=json.dumps(payload).encode('utf-8'),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, 
            method='POST'
        )
        with urllib.request.urlopen(req) as res: 
            return "✅ 成功"
            
    except Exception as e:
        # 為了除錯，如果失敗請印出詳細錯誤
        import traceback
        traceback.print_exc()
        return f"❌ 錯誤: {str(e)}"
    finally: 
        cur.close(); conn.close()
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

