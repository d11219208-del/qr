import os
import psycopg2
import json
import threading
import urllib.request
import urllib.error
import time  
import io
import resend
import ssl
import threading  # 新增：用於非同步發信，解決延遲問題
import pandas as pd  
from flask import Flask, request, jsonify, redirect, url_for, Response, send_file, current_app
from datetime import datetime, date, timedelta 

app = Flask(__name__)

# --- 資料庫連線 --- 
def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 翻譯字典 ---
def load_translations():
    return {
        "zh": {
            "title": "線上點餐", "welcome": "歡迎點餐", "table_placeholder": "請輸入桌號", 
            "table_label": "桌號", "add": "加入", "sold_out": "已售完", "cart_detail": "查看明細", 
            "total": "合計", "checkout": "去結帳", "cart_title": "購物車明細", "empty_cart": "購物車是空的", 
            "close": "關閉", "confirm_delete": "確定刪除？", "confirm_order": "確定送出訂單？", 
            "modal_unit_price": "單價", "modal_add_cart": "加入購物車", "modal_cancel": "取消", 
            "custom_options": "客製化選項", "order_success": "下單成功！", "kitchen_prep": "廚房備餐中", 
            "pay_at_counter": "請至櫃檯結帳", "order_details": "訂單明細", 
            "print_receipt_opt": "列印收據", "daily_seq_prefix": "單號", "ai_note": "翻譯由 AI 提供",
            "edit_options": "重選選項","save_changes": "💾 儲存修改"
        },
        "en": {
            "title": "Order", "welcome": "Welcome", "table_placeholder": "Table No.",
            "table_label": "Table", "add": "Add", "sold_out": "Sold Out", "cart_detail": "Cart",
            "total": "Total", "checkout": "Checkout", "cart_title": "Cart", "empty_cart": "Empty",
            "close": "Close", "confirm_delete": "Remove?", "confirm_order": "Submit?",
            "modal_unit_price": "Price", "modal_add_cart": "Add to Cart", "modal_cancel": "Cancel",
            "custom_options": "Options", "order_success": "Success!", "kitchen_prep": "Preparing...",
            "pay_at_counter": "Please pay at counter", "order_details": "Order Details",
            "print_receipt_opt": "Print Receipt", "daily_seq_prefix": "No.", "ai_note": "Translated by AI",
            "edit_options": "Edit Options","save_changes": "💾 Save Changes"
        },
        "jp": {
            "title": "注文", "welcome": "ようこそ", "table_placeholder": "卓番",
            "table_label": "卓番", "add": "追加", "sold_out": "完売", "cart_detail": "カート",
            "total": "合計", "checkout": "会計", "cart_title": "詳細", "empty_cart": "空です",
            "close": "閉じる", "confirm_delete": "削除？", "confirm_order": "送信？",
            "modal_unit_price": "単価", "modal_add_cart": "カートへ", "modal_cancel": "キャンセル",
            "custom_options": "オプション", "order_success": "送信完了", "kitchen_prep": "調理中...",
            "pay_at_counter": "レジでお会計ください", "order_details": "注文詳細",
            "print_receipt_opt": "レシート印刷", "daily_seq_prefix": "番号", "ai_note": "AIによる翻訳",
            "edit_options": "オプション変更","save_changes": "💾 変更を保存"
        },
        "kr": {
            "title": "주문", "welcome": "환영합니다", "table_placeholder": "테이블 번호",
            "table_label": "테이블", "add": "추가", "sold_out": "매진", "cart_detail": "장바구니",
            "total": "합계", "checkout": "결제하기", "cart_title": "상세 내역", "empty_cart": "비어 있음",
            "close": "닫기", "confirm_delete": "삭제하시겠습니까?", "confirm_order": "주문하시겠습니까?",
            "modal_unit_price": "단가", "modal_add_cart": "장바구니 담기", "modal_cancel": "취소",
            "custom_options": "옵션", "order_success": "주문 성공!", "kitchen_prep": "준비 중...",
            "pay_at_counter": "카운터에서 결제해주세요", "order_details": "주문 내역",
            "print_receipt_opt": "영수증 출력", "daily_seq_prefix": "번호", "ai_note": "AI 번역",
            "edit_options": "옵션 변경","save_changes": "💾 변경사항 저장"
        }
    }

# --- 1. 資料庫初始化 ---
@app.route('/init_db')
def init_db():
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL, price INTEGER NOT NULL,
                category VARCHAR(50), image_url TEXT, is_available BOOLEAN DEFAULT TRUE,
                custom_options TEXT, sort_order INTEGER DEFAULT 100,
                name_en VARCHAR(100), name_jp VARCHAR(100), name_kr VARCHAR(100),
                custom_options_en TEXT, custom_options_jp TEXT, custom_options_kr TEXT,
                print_category VARCHAR(20) DEFAULT 'Noodle',
                category_en VARCHAR(50), category_jp VARCHAR(50), category_kr VARCHAR(50)
            );
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY, table_number VARCHAR(10), items TEXT NOT NULL, 
                total_price INTEGER NOT NULL, status VARCHAR(20) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, daily_seq INTEGER DEFAULT 0,
                content_json TEXT, need_receipt BOOLEAN DEFAULT FALSE, lang VARCHAR(10) DEFAULT 'zh'
            );
        ''')
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);''')
        
        default_settings = [
            ('report_email', ''), ('resend_api_key', ''), ('sender_email', 'onboarding@resend.dev')
        ]
        for k, v in default_settings:
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))

        alters = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;"
        ]
        for cmd in alters:
            try: cur.execute(cmd)
            except: pass

        return "資料庫結構檢查完成。<a href='/admin'>進入後台管理</a>"
    except Exception as e:
        return f"DB Error: {e}"
    finally:
        cur.close(); conn.close()

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
        
        

# --- 背景定時任務 ---
def scheduler_loop():
    last_sent_time = ""
    while True:
        now_tw = datetime.utcnow() + timedelta(hours=8)
        current_time = now_tw.strftime("%H:%M")
        if current_time in ["13:00", "18:00", "20:30"] and current_time != last_sent_time:
            send_daily_report()
            last_sent_time = current_time
        time.sleep(30)
threading.Thread(target=scheduler_loop, daemon=True).start()

# --- 2. 首頁與語言選擇 (加大文字與視覺優化版) ---
@app.route('/')
def language_select():
    tbl = request.args.get('table', '')
    qs_table = f"&table={tbl}" if tbl else ""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Select Language</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
        <style>
            body {{
                font-family: 'Microsoft JhengHei', -apple-system, sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
                background: #ffffff;
                padding: 20px;
                box-sizing: border-box;
            }}
            .header-info {{
                text-align: center;
                margin-bottom: 40px;
            }}
            h2 {{
                color: #333;
                font-size: 2.2em; /* 放大店名文字 */
                margin: 0 0 10px 0;
                font-weight: 900;
            }}
            .sub-title {{
                color: #666;
                font-size: 1.2em;
                margin-bottom: 20px;
            }}
            .btn-container {{
                display: flex;
                flex-direction: column;
                width: 100%;
                max-width: 350px;
            }}
            .btn {{
                padding: 22px; /* 增加點擊區域 */
                margin: 12px 0;
                text-align: center;
                text-decoration: none;
                font-size: 1.6em; /* 放大按鈕文字 */
                font-weight: bold;
                border-radius: 60px;
                color: white;
                box-shadow: 0 6px 15px rgba(0,0,0,0.15);
                transition: transform 0.1s, box-shadow 0.1s;
                border: none;
            }}
            .btn:active {{
                transform: scale(0.95);
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }}
            /* 語言按鈕顏色 */
            .zh {{ background: linear-gradient(135deg, #e91e63, #c2185b); }} 
            .en {{ background: linear-gradient(135deg, #007bff, #0056b3); }} 
            .jp {{ background: linear-gradient(135deg, #ff9800, #f57c00); }} 
            .kr {{ background: linear-gradient(135deg, #20c997, #17a2b8); }}

            .footer-info {{
                margin-top: 50px;
                text-align: center;
                color: #555;
            }}
            .footer-info h3 {{
                font-size: 1.5em; /* 放大電話 */
                margin: 5px 0;
                color: #000;
            }}
            .footer-info h4 {{
                font-size: 1.1em; /* 放大地址 */
                margin: 5px 0;
                font-weight: normal;
                color: #666;
            }}
        </style>
    </head>
    <body>
        <div class="header-info">
            <h2>龍江路大鼎豬血湯專門店</h2>
            <div class="sub-title">請選擇語言 / Select Language</div>
        </div>

        <div class="btn-container">
            <a href="/menu?lang=zh{qs_table}" class="btn zh">中文</a>
            <a href="/menu?lang=en{qs_table}" class="btn en">English</a>
            <a href="/menu?lang=jp{qs_table}" class="btn jp">日本語</a>
            <a href="/menu?lang=kr{qs_table}" class="btn kr">한국어</a>
        </div>

        <div class="footer-info">
            <h3>📞 02-2515-2519</h3>
            <h4>📍 10491臺北市中山區龍江路164號</h4>
        </div>
    </body>
    </html>
    """


# --- 3. 點餐頁面 (bfcache 強化版) ---
@app.route('/menu', methods=['GET', 'POST'])
def menu():
    # 網頁介面顯示語言
    display_lang = request.args.get('lang', 'zh')
    t_all = load_translations()
    t = t_all.get(display_lang, t_all['zh'])
    
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            table_number = request.form.get('table_number')
            cart_json = request.form.get('cart_data')
            need_receipt = request.form.get('need_receipt') == 'on'
            final_lang = request.form.get('lang_input', 'zh')
            old_order_id = request.form.get('old_order_id')

            if not cart_json or cart_json == '[]': return "Empty Cart"

            cart_items = json.loads(cart_json)
            total_price = 0
            display_list = []

            if old_order_id:
                cur.execute("SELECT lang FROM orders WHERE id=%s", (old_order_id,))
                orig_res = cur.fetchone()
                if orig_res: final_lang = orig_res[0] 

            for item in cart_items:
                price = int(float(item['unit_price']))
                qty = int(float(item['qty']))
                total_price += (price * qty)
                
                name_key = f"name_{final_lang}"
                n_display = item.get(name_key, item.get('name_zh'))
                opt_key = f"options_{final_lang}"
                opts = item.get(opt_key, item.get('options_zh', []))
                opt_str = f"({','.join(opts)})" if opts else ""
                display_list.append(f"{n_display} {opt_str} x{qty}")

            items_str = " + ".join(display_list)

            cur.execute("""
                INSERT INTO orders (table_number, items, total_price, lang, daily_seq, content_json, need_receipt)
                VALUES (%s, %s, %s, %s, (SELECT COALESCE(MAX(daily_seq), 0) + 1 FROM orders WHERE created_at >= CURRENT_DATE), %s, %s) 
                RETURNING id
            """, (table_number, items_str, total_price, final_lang, cart_json, need_receipt))

            oid = cur.fetchone()[0]
            if old_order_id:
                cur.execute("UPDATE orders SET status='Cancelled' WHERE id=%s", (old_order_id,))
            
            conn.commit()
            
            # 成功提交後，確保舊快取被移除
            if old_order_id: 
                return f"<script>localStorage.removeItem('cart_cache'); alert('Order #{old_order_id} Updated'); if(window.opener) window.opener.location.reload(); window.close();</script>"
            
            return redirect(url_for('order_success', order_id=oid, lang=final_lang))
        except Exception as e:
            conn.rollback()
            return f"Order Failed: {e}"
        finally:
            cur.close(); conn.close()

    url_table = request.args.get('table', '')
    edit_oid = request.args.get('edit_oid')
    preload_cart = "null" 
    order_lang = display_lang 

    if edit_oid:
        cur.execute("SELECT table_number, content_json, lang FROM orders WHERE id=%s", (edit_oid,))
        old_data = cur.fetchone()
        if old_data:
            if not url_table: url_table = old_data[0]
            preload_cart = old_data[1] 
            order_lang = old_data[2] if old_data[2] else 'zh'

    cur.execute("""
        SELECT id, name, price, category, image_url, is_available, custom_options, sort_order,
               name_en, name_jp, name_kr, custom_options_en, custom_options_jp, custom_options_kr, 
               print_category, category_en, category_jp, category_kr
        FROM products ORDER BY sort_order ASC, id ASC
    """)
    products = cur.fetchall()
    cur.close(); conn.close()

    p_list = []
    for p in products:
        p_list.append({
            'id': p[0], 'name_zh': p[1], 'name_en': p[8] or p[1], 'name_jp': p[9] or p[1], 'name_kr': p[10] or p[1],
            'price': p[2], 'category_zh': p[3], 'category_en': p[15] or p[3], 'category_jp': p[16] or p[3], 'category_kr': p[17] or p[3],
            'image_url': p[4] or '', 'is_available': p[5], 
            'custom_options_zh': p[6].split(',') if p[6] else [],
            'custom_options_en': p[11].split(',') if p[11] else (p[6].split(',') if p[6] else []),
            'custom_options_jp': p[12].split(',') if p[12] else (p[6].split(',') if p[6] else []),
            'custom_options_kr': p[13].split(',') if p[13] else (p[6].split(',') if p[6] else []),
            'print_category': p[14] or 'Noodle'
        })
    return render_frontend(p_list, t, url_table, display_lang, order_lang, preload_cart, edit_oid)

def render_frontend(products, t, default_table, display_lang, order_lang, preload_cart, edit_oid):
    import json
    p_json = json.dumps(products)
    t_json = json.dumps(t)
    old_oid_input = f'<input type="hidden" name="old_order_id" value="{edit_oid}">' if edit_oid else ''
    edit_notice = f'<div style="background:#fff3cd;padding:12px;color:#856404;text-align:center;font-weight:bold;">⚠️ 正在編輯 #{edit_oid} ({order_lang})</div>' if edit_oid else ''

    return f"""
    <!DOCTYPE html>
    <html><head><title>{t['title']}</title><meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=0">
    <style>
        body{{font-family:'Microsoft JhengHei',sans-serif;margin:0;padding-bottom:160px;background:#f8f9fa;touch-action:manipulation;font-size:18px;}}
        .header{{background:white;padding:15px;position:sticky;top:0;z-index:99;box-shadow:0 2px 5px rgba(0,0,0,0.1);}}
        .cat-bar {{ display: flex; overflow-x: auto; white-space: nowrap; padding: 10px 0; gap: 10px; scrollbar-width: none; }}
        .cat-bar::-webkit-scrollbar {{ display: none; }}
        .cat-btn {{ background: #f1f3f5; border: 1px solid #dee2e6; padding: 8px 18px; border-radius: 25px; font-size: 1em; color: #495057; cursor: pointer; }}
        .cat-btn.active {{ background: #28a745; color: white; border-color: #28a745; }}
        .menu-item{{background:white;margin:12px;padding:15px;border-radius:12px;display:flex;box-shadow:0 2px 8px rgba(0,0,0,0.08);position:relative;}}
        .menu-img{{width:100px;height:100px;border-radius:10px;object-fit:cover;background:#eee;}}
        .menu-info{{flex:1;padding-left:15px;display:flex;flex-direction:column;justify-content:space-between;}}
        .menu-info b {{ font-size: 1.2em; }}
        .add-btn{{background:#28a745;color:white;border:none;padding:10px 20px;border-radius:20px;align-self:flex-end;font-size:1em;font-weight:bold;}}
        .sold-out {{ filter: grayscale(1); opacity: 0.6; pointer-events: none; }}
        .sold-out-badge {{ position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.7); color: white; padding: 4px 10px; border-radius: 5px; font-size: 0.9em; font-weight: bold; z-index: 5; }}
        .cart-bar{{position:fixed;bottom:0;width:100%;background:white;padding:15px;box-shadow:0 -4px 15px rgba(0,0,0,0.15);display:none;flex-direction:column;box-sizing:border-box;z-index:100;}}
        .cart-summary{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;}}
        .cart-buttons{{display:flex;gap:12px;}}
        .btn-view-cart{{background:#ff9800;color:white;border:none;flex:1;padding:15px;border-radius:12px;font-weight:bold;font-size:1.2em;}}
        .btn-checkout{{background:#28a745;color:white;border:none;flex:1;padding:15px;border-radius:12px;font-weight:bold;font-size:1.2em;}}
        .modal{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);display:none;z-index:200;justify-content:center;align-items:flex-end;}}
        .modal-c{{background:white;width:100%;padding:25px;border-radius:25px 25px 0 0;max-height:85vh;overflow-y:auto;box-sizing:border-box;}}
        .opt-tag{{border:1px solid #ccc;padding:8px 15px;border-radius:20px;margin:5px;display:inline-block;font-size:1.1em;}}
        .opt-tag.sel{{background:#e3f2fd;border-color:#2196f3;color:#2196f3;font-weight:bold;}}
        .cat-header {{padding:12px 15px;font-weight:bold;font-size:1.3em;color:#333;background:#eee;margin-top:15px; scroll-margin-top: 160px;}}
        .qty-ctrl{{display:flex;align-items:center;gap:15px;justify-content:center;margin:20px 0;}}
        .qty-ctrl button{{width:50px;height:50px;border-radius:25px;border:1px solid #ddd;background:white;font-size:1.8em;}}
        .qty-input{{width:70px;text-align:center;font-size:1.4em;border:1px solid #ddd;padding:8px;border-radius:8px;}}
        .cart-item-row{{border-bottom:1px solid #eee;padding:15px 0;}}
        .cart-item-main{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}}
        .cart-qty-sub{{display:flex;align-items:center;justify-content:flex-end;gap:15px;}}
        .btn-edit-opt {{ background: #e3f2fd; color: #2196f3; border: 1px solid #2196f3; padding: 6px 12px; border-radius: 8px; font-size: 0.9em; font-weight: bold; cursor: pointer; }}
    </style></head><body>
    <div class="header">
        {edit_notice}
        <h2 style="margin:0 0 10px 0;">{t['welcome']}</h2>
        <input type="text" id="visible_table" value="{default_table}" placeholder="{t['table_placeholder']}" 
               style="padding:12px;width:100%;box-sizing:border-box;border:2px solid #ddd;border-radius:8px;font-size:1.2em;margin-bottom:8px;">
        <div class="cat-bar" id="cat-nav"></div>
    </div>
    <div id="list"></div>
    <form id="order-form" method="POST" action="/menu">
        <input type="hidden" name="cart_data" id="cart_input">
        <input type="hidden" name="table_number" id="tbl_input">
        <input type="hidden" name="lang_input" id="lang_final_input" value="{order_lang}">
        {old_oid_input}
        <div class="cart-bar" id="bar">
            <div class="cart-summary">
                <div style="font-weight:bold; font-size:1.3em;">Total: $<span id="tot">0</span> (<span id="cnt">0</span>)</div>
                <label style="font-size:1em;"><input type="checkbox" name="need_receipt" checked> {t['print_receipt_opt']}</label>
            </div>
            <div class="cart-buttons">
                <button type="button" class="btn-view-cart" onclick="showCart()">🛒 {t['cart_detail']}</button>
                <button type="button" class="btn-checkout" onclick="sub()">{t['checkout']}</button>
            </div>
        </div>
    </form>

    <div class="modal" id="opt-m" onclick="closeModalByBg(event, 'opt-m')">
        <div class="modal-c" onclick="event.stopPropagation()">
            <h3 id="m-name" style="font-size:1.5em;margin-top:0;"></h3><div id="m-opts"></div>
            <div class="qty-ctrl">
                <button onclick="cq(-1)">-</button>
                <input type="number" id="m-q" class="qty-input" value="1" min="1" inputmode="numeric">
                <button onclick="cq(1)">+</button>
            </div>
            <button id="m-confirm-btn" onclick="addC()" style="width:100%;background:#28a745;color:white;padding:18px;border:none;border-radius:15px;margin-top:10px;font-size:1.3em;font-weight:bold;">{t['modal_add_cart']}</button>
            <button onclick="document.getElementById('opt-m').style.display='none'" style="width:100%;background:white;padding:12px;border:none;margin-top:10px;font-size:1.1em;color:#666;">{t['modal_cancel']}</button>
        </div>
    </div>
    <div class="modal" id="cart-m" onclick="closeModalByBg(event, 'cart-m')">
        <div class="modal-c" onclick="event.stopPropagation()">
            <h2 style="margin-top:0;">{t['cart_title']}</h2>
            <div id="c-list"></div>
            <button onclick="document.getElementById('cart-m').style.display='none'" style="width:100%;padding:15px;margin-top:20px;border:1px solid #ddd;border-radius:12px;background:#f8f9fa;font-size:1.1em;">{t['close']}</button>
        </div>
    </div>

    <script>
    const P={p_json}, T={t_json}, EDIT_OID="{edit_oid}", PRELOAD_CART={preload_cart}, CUR_LANG="{display_lang}", ORDER_LANG="{order_lang}";
    let C=[], cur=null, selectedOptIndices=[], addP=0, editIndex=-1;

    function saveCache() {{ 
        if(!EDIT_OID) localStorage.setItem('cart_cache', JSON.stringify(C)); 
    }}

    function initCart() {{
        // 如果是編輯模式，從預載入資料讀取；否則從快取讀取
        if(EDIT_OID && PRELOAD_CART) {{
            C = PRELOAD_CART;
        }} else {{
            let cached = localStorage.getItem('cart_cache');
            C = cached ? JSON.parse(cached) : [];
        }}
        upd();
    }}

    // --- 【修正重點：處理 bfcache】 ---
    window.addEventListener('pageshow', function(event) {{
        // event.persisted 為 true 代表是點擊「返回」按鈕回來的
        // 或是檢測到 localStorage 已被清空，但記憶體變數 C 還有值，則強制同步
        if (event.persisted || (C.length > 0 && !localStorage.getItem('cart_cache') && !EDIT_OID)) {{
            initCart();
        }}
    }});

    // 渲染 UI 部分
    let h="", lastCatKey="", cats=[];
    P.forEach(p=>{{
        let currentCatName = p['category_' + CUR_LANG] || p.category_zh;
        let catId = "cat-" + p.category_zh; 
        if(p.category_zh != lastCatKey) {{ 
            h+=`<div class="cat-header" id="${{catId}}">${{currentCatName}}</div>`; 
            lastCatKey=p.category_zh; 
            cats.push({{ id: catId, name: currentCatName }});
        }}
        let isAvail = p.is_available;
        let d_name = p['name_' + CUR_LANG] || p.name_zh;
        h+=`<div class="menu-item ${{isAvail ? '' : 'sold-out'}}">
            ${{isAvail ? '' : `<div class="sold-out-badge">${{T.sold_out}}</div>`}}
            ${{p.image_url ? `<img src="${{p.image_url}}" class="menu-img">` : ''}}
            <div class="menu-info">
                <div><b>${{d_name}}</b><div style="color:#e91e63; font-weight:bold; font-size:1.1em;">$${{p.price}}</div></div>
                <button class="add-btn" onclick="openOpt(${{p.id}})" ${{isAvail ? '' : 'disabled'}}>${{isAvail ? T.add : T.sold_out}}</button>
            </div>
        </div>`;
    }});
    document.getElementById('list').innerHTML=h;
    let navH = ""; cats.forEach(c => {{ navH += `<div class="cat-btn" onclick="scrollToCat('${{c.id}}', this)">${{c.name}}</div>`; }});
    document.getElementById('cat-nav').innerHTML = navH;

    function scrollToCat(catId, btn) {{
        const el = document.getElementById(catId);
        if(el) {{
            el.scrollIntoView({{ behavior: 'smooth' }});
            document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        }}
    }}

    function closeModalByBg(e, id) {{ document.getElementById(id).style.display = 'none'; }}

    function openOpt(productId, cartIndex = -1){{
        cur = P.find(x=>x.id==productId); editIndex = cartIndex;
        selectedOptIndices = []; addP = 0;
        document.getElementById('m-name').innerText = (editIndex > -1 ? "✏️ " : "") + (cur['name_' + CUR_LANG] || cur.name_zh);
        document.getElementById('m-confirm-btn').innerText = editIndex > -1 ? (T.save_changes || "💾 儲存修改") : T.modal_add_cart;
        let area = document.getElementById('m-opts'); area.innerHTML = "";
        let opts = cur['custom_options_' + CUR_LANG] || cur.custom_options_zh;
        let existingOpts = editIndex > -1 ? C[editIndex].options_zh : [];
        opts.forEach((o, index)=>{{
            let parts = o.split(/[+]/); let n = parts[0].trim(), p = parts.length>1 ? parseInt(parts[1]) : 0;
            let d = document.createElement('div'); d.className='opt-tag';
            d.innerText = n + (p?` (+$${{p}})`:'');
            if(editIndex > -1 && existingOpts.includes(cur.custom_options_zh[index])) {{
                selectedOptIndices.push(index); addP += p; d.classList.add('sel');
            }}
            d.onclick=()=>{{
                if(selectedOptIndices.includes(index)){{
                    selectedOptIndices = selectedOptIndices.filter(i=>i!=index); addP-=p; d.classList.remove('sel');
                }} else {{
                    selectedOptIndices.push(index); addP+=p; d.classList.add('sel');
                }}
            }};
            area.appendChild(d);
        }});
        document.getElementById('m-q').value = editIndex > -1 ? C[editIndex].qty : 1;
        document.getElementById('opt-m').style.display = 'flex';
        document.getElementById('cart-m').style.display = 'none';
    }}

    function cq(n){{
        let input = document.getElementById('m-q'); let val = parseInt(input.value) || 1;
        if(val + n >= 1) input.value = val + n;
    }}

    function addC(){{
        let q = parseInt(document.getElementById('m-q').value) || 1;
        let itemData = {{ 
            id: cur.id, name_zh: cur.name_zh, name_en: cur.name_en, name_jp: cur.name_jp, name_kr: cur.name_kr, 
            unit_price: cur.price + addP, qty: q, 
            options_zh: selectedOptIndices.map(idx => cur.custom_options_zh[idx]),
            options_en: selectedOptIndices.map(idx => cur.custom_options_en[idx] || cur.custom_options_zh[idx]),
            options_jp: selectedOptIndices.map(idx => cur.custom_options_jp[idx] || cur.custom_options_zh[idx]),
            options_kr: selectedOptIndices.map(idx => cur.custom_options_kr[idx] || cur.custom_options_zh[idx]),
            category: cur.category_zh, print_category: cur.print_category 
        }};
        if(editIndex > -1) C[editIndex] = itemData; else C.push(itemData);
        document.getElementById('opt-m').style.display='none'; 
        saveCache(); upd(); if(editIndex > -1) showCart();
    }}

    function upd() {{
        if(C.length) {{
            document.getElementById('bar').style.display='flex';
            document.getElementById('tot').innerText = C.reduce((a,b)=>a+b.unit_price*b.qty,0);
            document.getElementById('cnt').innerText = C.reduce((a,b)=>a+b.qty,0);
        }} else {{
            document.getElementById('bar').style.display='none';
        }}
    }}

    function updateCartQty(idx, n){{
        C[idx].qty += n; if(C[idx].qty <= 0) C.splice(idx, 1);
        saveCache(); showCart(); upd();
    }}
    
    function setCartQty(idx, val){{
        let q = parseInt(val) || 1; if(q < 1) q = 1;
        C[idx].qty = q; saveCache(); upd();
    }}

    function showCart(){{
        let h="";
        C.forEach((i,x)=>{{
            let d_name = i['name_' + CUR_LANG] || i.name_zh;
            let opts = i['options_' + CUR_LANG] || i.options_zh || [];
            let opt_str = opts.length ? `<div style="font-size:0.9em;color:#666;margin-top:4px;">(${{opts.join(',')}})</div>` : '';
            h+=`<div class="cart-item-row">
                <div class="cart-item-main">
                    <div style="flex:1;"><b style="font-size:1.15em;">${{d_name}}</b>${{opt_str}}</div>
                    <div style="font-weight:bold;color:#e91e63;font-size:1.1em;margin-left:10px;">$${{i.unit_price * i.qty}}</div>
                </div>
                <div class="cart-qty-sub">
                    <button onclick="if(confirm('Delete?')){{C.splice(${{x}},1);saveCache();upd();showCart();}}" style="border:1px solid #ffcdd2; background:#fff5f5; border-radius:8px; padding:6px 10px; cursor:pointer; margin-right:auto;">🗑️</button>
                    <button class="btn-edit-opt" onclick="openOpt(${{i.id}}, ${{x}})">${{T.edit_options || 'Edit Options'}}</button>
                    <div class="qty-ctrl" style="margin:0; gap:8px;">
                        <button onclick="updateCartQty(${{x}}, -1)" style="width:36px;height:36px;font-size:1.2em;">-</button>
                        <input type="number" class="qty-input" value="${{i.qty}}" onchange="setCartQty(${{x}}, this.value)" inputmode="numeric" style="width:45px;height:30px;font-size:1.1em;padding:2px;">
                        <button onclick="updateCartQty(${{x}}, 1)" style="width:36px;height:36px;font-size:1.2em;">+</button>
                    </div>
                </div>
            </div>`;
        }});
        document.getElementById('c-list').innerHTML=h || `<p style="text-align:center;">${{T.empty_cart}}</p>`;
        document.getElementById('cart-m').style.display='flex';
    }}

    function sub(){{
        let t = document.getElementById('visible_table').value;
        if(!t) return alert(T.table_placeholder);
        if(confirm(T.confirm_order)) {{
            document.getElementById('lang_final_input').value = ORDER_LANG;
            document.getElementById('tbl_input').value = t;
            document.getElementById('cart_input').value = JSON.stringify(C);
            
            // 提交前徹底清空
            localStorage.removeItem('cart_cache');
            C = []; 
            
            document.getElementById('order-form').submit();
        }}
    }}
    
    // 初始載入
    initCart();
    </script></body></html>
    """
    
    
# --- 4. 下單成功 (滿版優化版) ---
@app.route('/order_success')
def order_success():
    oid = request.args.get('order_id')
    lang = request.args.get('lang', 'zh')
    translations = load_translations()
    t = translations.get(lang, translations['zh'])
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT daily_seq, content_json, total_price, created_at FROM orders WHERE id=%s", (oid,))
    row = cur.fetchone(); conn.close()
    if not row: return "Order Not Found"
    
    seq, json_str, total, created_at = row
    tw_time = created_at + timedelta(hours=8)
    time_str = tw_time.strftime('%Y-%m-%d %H:%M:%S')
    items = json.loads(json_str) if json_str else []
    
    items_html = ""
    for i in items:
        # 取得對應語言名稱
        d_name = i.get(f'name_{lang}', i.get('name_zh', i.get('name', 'Product')))
        # 取得客製化選項
        ops = i.get(f'options_{lang}', i.get('options_zh', i.get('options', [])))
        opt_str = f" <br><small style='color:#777; font-size:0.9em;'>└ {', '.join(ops)}</small>" if ops else ""
        
        items_html += f"""
        <div style='display:flex; justify-content:space-between; align-items: flex-start; border-bottom:1px solid #eee; padding:15px 0;'>
            <div style="text-align: left; padding-right: 10px;">
                <div style="font-size:1.1em; font-weight:bold; color:#333;">{d_name} <span style="color:#888; font-weight:normal;">x{i['qty']}</span></div>
                {opt_str}
            </div>
            <div style="font-weight:bold; font-size:1.1em; white-space:nowrap;">${i['unit_price'] * i['qty']}</div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Order Success</title>
        <style>
            body {{ margin: 0; padding: 0; background: #fdfdfd; font-family: 'Microsoft JhengHei', -apple-system, sans-serif; }}
            .container {{ 
                min-height: 100vh; 
                display: flex; 
                flex-direction: column; 
                padding: 20px; 
                box-sizing: border-box; 
            }}
            .card {{ 
                background: #fff; 
                flex-grow: 1; 
                border-radius: 20px; 
                box-shadow: 0 4px 20px rgba(0,0,0,0.08); 
                padding: 30px 20px; 
                text-align: center;
                display: flex;
                flex-direction: column;
            }}
            .success-icon {{ font-size: 60px; margin-bottom: 10px; }}
            .status-title {{ color: #28a745; margin: 0 0 20px 0; font-size: 1.8em; }}
            .seq-box {{ 
                background: #fff5f8; 
                border-radius: 15px; 
                padding: 20px; 
                margin-bottom: 25px; 
                border: 2px solid #ffeef2;
            }}
            .seq-label {{ font-size: 1em; color: #e91e63; font-weight: bold; margin-bottom: 8px; letter-spacing: 1px; }}
            .seq-number {{ font-size: 5em; font-weight: 900; color: #e91e63; line-height: 1; }}
            .notice-box {{ 
                background: #fdf6e3; 
                padding: 18px; 
                border-left: 6px solid #ff9800; 
                border-radius: 8px; 
                margin-bottom: 30px; 
                text-align: left; 
            }}
            .details-area {{ text-align: left; margin-bottom: 30px; }}
            .total-row {{ 
                text-align: right; 
                font-weight: 900; 
                font-size: 1.8em; 
                margin-top: 20px; 
                color: #d32f2f; 
                border-top: 2px solid #333; 
                padding-top: 15px; 
            }}
            .home-btn {{ 
                display: block; 
                padding: 18px; 
                background: #007bff; 
                color: white !important; 
                text-decoration: none; 
                border-radius: 12px; 
                font-weight: bold; 
                font-size: 1.2em; 
                margin-top: auto;
                box-shadow: 0 4px 10px rgba(0,123,255,0.3);
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <div class="success-icon">✅</div>
                <h1 class="status-title">{t['order_success']}</h1>
                
                <div class="seq-box">
                    <div class="seq-label">取餐單號 / ORDER NO.</div>
                    <div class="seq-number">#{seq:03d}</div>
                </div>

                <div class="notice-box">
                    <div style="font-weight:bold; color:#856404; font-size:1.3em; margin-bottom:5px;">⚠️ {t['pay_at_counter']}</div>
                    <div style="color:#856404; font-size:1em; line-height:1.4;">{t['kitchen_prep']}</div>
                </div>

                <div class="details-area">
                    <h3 style="border-bottom:2px solid #eee; padding-bottom:10px; margin-bottom:10px; color:#444;">🧾 {t['order_details']}</h3>
                    {items_html}
                    <div class="total-row">{t['total']}: ${total}</div>
                </div>

                <p style="color:#999; font-size:0.85em; margin: 20px 0;">下單時間: {time_str}</p>
                
                <a href="/?lang={lang}" class="home-btn">回首頁 / Back to Menu</a>
            </div>
        </div>
    </body>
    </html>
    """

# --- 5. 廚房看板 ---
@app.route('/kitchen')
def kitchen_panel():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>👨‍🍳 廚房出單看板</title>
        <link href="https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;700&family=Noto+Sans+TC:wght@400;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-color: #121212;
                --card-bg: #1e1e1e;
                --text-main: #e0e0e0;
                --accent-orange: #ff9800;
                --accent-green: #4caf50;
                --accent-red: #f44336;
                --accent-blue: #2196f3;
            }
            body { background: var(--bg-color); color: var(--text-main); font-family: 'Noto Sans TC', sans-serif; margin: 0; padding-bottom: 50px; }
            
            /* 頂部導航列 */
            .header-container { 
                display: flex; justify-content: space-between; align-items: center; 
                padding: 10px 20px; background: #252526; border-bottom: 2px solid #333;
                position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 10px rgba(0,0,0,0.5);
            }
            h1 { color: var(--accent-orange); margin: 0; font-size: 1.5rem; display: flex; align-items: center; gap: 10px; }
            .header-btns { display: flex; gap: 10px; }
            
            /* 網格佈局 */
            .grid { 
                display: grid; 
                grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); 
                gap: 20px; padding: 20px; 
            }

            /* 卡片設計 */
            .card { 
                background: var(--card-bg); border-radius: 8px; overflow: hidden;
                box-shadow: 0 4px 15px rgba(0,0,0,0.5); 
                display: flex; flex-direction: column;
                border-top: 5px solid var(--accent-orange);
                transition: transform 0.2s, opacity 0.3s;
                position: relative;
            }
            
            .card.completed { border-top-color: var(--accent-green); background: #1b261b; opacity: 0.6; filter: grayscale(0.3); }
            .card.cancelled { border-top-color: var(--accent-red); background: #261b1b; opacity: 0.5; }
            .card.cancelled .items { text-decoration: line-through; opacity: 0.7; }

            .card-header {
                padding: 15px; background: rgba(255,255,255,0.03);
                display: flex; justify-content: space-between; align-items: center;
                border-bottom: 2px dashed #444;
            }
            .seq-num { font-size: 2rem; font-family: 'Roboto Mono', monospace; font-weight: bold; color: var(--accent-orange); }
            .table-num { font-size: 1.4rem; background: #333; padding: 4px 12px; border-radius: 4px; font-weight: bold; }
            .time-stamp { font-size: 0.85rem; color: #888; margin-top: 4px; }

            .items { padding: 15px; flex-grow: 1; font-size: 1.2rem; line-height: 1.5; }
            .item-row { margin-bottom: 12px; border-bottom: 1px solid #333; padding-bottom: 8px; }
            .item-row:last-child { border-bottom: none; }
            .item-name { font-weight: bold; display: flex; justify-content: space-between; }
            .item-qty { color: var(--accent-orange); font-family: 'Roboto Mono'; font-size: 1.3rem; margin-left: 10px; }
            .item-opts { font-size: 0.95rem; color: #aaa; margin-top: 4px; padding-left: 10px; border-left: 2px solid #555; }

            .actions { padding: 10px; background: rgba(0,0,0,0.2); display: flex; flex-direction: column; gap: 8px; }
            .btn-group { display: flex; gap: 8px; }
            .btn { 
                border: none; border-radius: 6px; padding: 10px; 
                color: white; font-weight: bold; cursor: pointer; text-decoration: none;
                text-align: center; display: flex; justify-content: center; align-items: center;
                transition: filter 0.2s; font-size: 1rem;
            }
            .btn:hover { filter: brightness(1.1); }
            .btn:active { transform: scale(0.98); }

            .btn-main { background: var(--accent-green); font-size: 1.2rem; padding: 15px; width: 100%; box-sizing: border-box; }
            .btn-print { background: var(--accent-blue); flex: 1; }
            .btn-edit { background: #607d8b; flex: 1; }
            .btn-void { background: #d32f2f; width: 40px; } 
            .nav-btn { background: #444; color: #fff; padding: 8px 15px; border-radius: 4px; text-decoration: none; font-size: 0.9rem; }

            /* 音效與狀態橫幅 */
            #status-banner { 
                background: #333; color: white; text-align: center; 
                padding: 12px; font-weight: bold; cursor: pointer; 
                position: sticky; top: 60px; z-index: 99;
                display: flex; justify-content: center; align-items: center; gap: 10px;
                border-bottom: 1px solid #000;
            }
            .pulse { animation: pulse 2s infinite; }
            @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.8; } 100% { opacity: 1; } }
            
            /* 隱藏的 iframe 用於列印 */
            #print-frame-container { display: none; }
        </style>
    </head>
    <body>
    
    <div class="header-container">
        <h1>👨‍🍳 廚房出單 <span id="clock" style="font-size:0.8em; color:#888; margin-left:10px;"></span></h1>
        <div class="header-btns">
            <a href="/admin" class="nav-btn">⚙️ 後台</a>
            <a href="/kitchen/report" class="nav-btn" style="background:#673ab7;">📊 報表</a>
        </div>
    </div>

    <div id="status-banner" class="pulse" onclick="enableAudio()" style="background: var(--accent-red);">
        🔊 點擊此處啟用「自動列印」與「音效通知」
    </div>
    
    <div id="order-grid" class="grid">
        <div style="grid-column:1/-1; text-align:center; padding:50px; color:#666;">
            載入中...
        </div>
    </div>

    <audio id="notice-sound" preload="auto">
        <source src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" type="audio/mpeg">
    </audio>

    <div id="print-frame-container"></div>

    <script>
        let lastMaxSeq = 0;
        let isFirstLoad = true;
        let audioUnlocked = false;
        
        // --- 列印排隊系統 ---
        let printQueue = [];
        let isPrinting = false;

        function updateClock() {
            const now = new Date();
            document.getElementById('clock').innerText = now.toLocaleTimeString('zh-TW', {hour12:false, hour:'2-digit', minute:'2-digit'});
        }
        setInterval(updateClock, 1000); updateClock();

        function enableAudio() { 
            audioUnlocked = true; 
            const banner = document.getElementById('status-banner');
            banner.style.background = '#4caf50'; // 轉為綠色
            banner.innerText = '✅ 自動列印與音效已啟用 (請勿關閉此頁)';
            banner.classList.remove('pulse');
            
            // 嘗試播放一次聲音以解鎖瀏覽器限制
            const audio = document.getElementById('notice-sound'); 
            audio.play().then(() => { 
                audio.pause(); 
                audio.currentTime = 0; 
            }).catch(e => console.log("Audio permission still needed"));
        }

        function action(url) { 
            fetch(url).then(() => { refreshOrders(); }); 
        }

        // --- 核心修改：使用隱藏 iframe 進行列印 (繞過彈窗攔截) ---
        function printOrderUrl(url) {
            return new Promise((resolve, reject) => {
                console.log("🖨️ 準備列印:", url);
                
                // 創建一個隱藏的 iframe
                const iframe = document.createElement('iframe');
                iframe.style.position = 'fixed';
                iframe.style.right = '0';
                iframe.style.bottom = '0';
                iframe.style.width = '0px';
                iframe.style.height = '0px';
                iframe.style.border = '0';
                
                // 設定來源
                iframe.src = url;
                
                // 加到 DOM
                document.getElementById('print-frame-container').appendChild(iframe);
                
                // 監聽載入完成
                iframe.onload = function() {
                    try {
                        // 重點：呼叫 iframe 內部的 print
                        iframe.contentWindow.focus(); // 取得焦點
                        iframe.contentWindow.print(); // 執行列印
                        
                        // 給予緩衝時間後移除 iframe
                        setTimeout(() => {
                            if (iframe.parentNode) {
                                iframe.parentNode.removeChild(iframe);
                            }
                            resolve(); // 完成
                        }, 5000); // 5秒後移除，確保訊號傳送到印表機
                    } catch (e) {
                        console.error("列印失敗:", e);
                        resolve(); // 就算失敗也繼續下一個，避免卡死
                    }
                };
            });
        }

        async function processPrintQueue() {
            if (isPrinting || printQueue.length === 0) return;

            isPrinting = true;
            const orderId = printQueue.shift();
            
            // 使用新版列印函數
            await printOrderUrl('/print_order/' + orderId);
            
            // 處理完畢，休息 1 秒再印下一張 (避免印表機緩衝區塞車)
            setTimeout(() => {
                isPrinting = false;
                processPrintQueue(); 
            }, 1000);
        }

        function refreshOrders() {
            fetch('/check_new_orders?current_seq=' + lastMaxSeq)
            .then(res => res.json())
            .then(data => {
                if (data.html) document.getElementById('order-grid').innerHTML = data.html;
                
                if (!isFirstLoad && data.new_ids && data.new_ids.length > 0) {
                    
                    if (audioUnlocked) { 
                        // 1. 播放音效
                        const audio = document.getElementById('notice-sound');
                        audio.currentTime = 0; 
                        audio.play().catch(e => console.log("Audio play failed", e)); 

                        // 2. 加入列印排程
                        console.log("📥 收到新訂單:", data.new_ids);
                        data.new_ids.forEach(id => {
                            if(!printQueue.includes(id)) {
                                printQueue.push(id);
                            }
                        });

                        // 3. 啟動處理
                        processPrintQueue();
                    } else {
                        console.log("⚠️ 收到新訂單但尚未啟用音效/列印權限");
                    }
                }
                
                if (data.max_seq > 0) {
                    lastMaxSeq = data.max_seq; 
                }
                isFirstLoad = false;
            })
            .catch(err => console.error("Polling error:", err));
        }
        
        setInterval(refreshOrders, 5000); 
        refreshOrders();
    </script>
    </body>
    </html>
    """

# --- 5. 廚房看板 API (保持原樣，僅為了完整性提供) ---
@app.route('/check_new_orders')
def check_new_orders():
    current_max = request.args.get('current_seq', 0, type=int)
    
    # 1. 時間範圍計算 (鎖定台灣時間當日)
    utc_now = datetime.utcnow()
    tw_now = utc_now + timedelta(hours=8)
    tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
    utc_start_query = tw_start - timedelta(hours=8)
    utc_end_query = tw_end - timedelta(hours=8)
    time_filter = f"created_at >= '{utc_start_query}' AND created_at <= '{utc_end_query}'"

    # 請確保此處有正確的 db 連線函數
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 2. 抓取訂單
    cur.execute(f"""
        SELECT id, table_number, items, total_price, status, created_at, lang, daily_seq, content_json 
        FROM orders WHERE {time_filter} 
        ORDER BY CASE WHEN status = 'Pending' THEN 0 ELSE 1 END, daily_seq DESC
    """)
    orders = cur.fetchall()
    
    # 3. 抓取當日最大序號
    cur.execute(f"SELECT MAX(daily_seq) FROM orders WHERE {time_filter}")
    res_max = cur.fetchone()
    max_seq_val = res_max[0] if res_max and res_max[0] else 0
    
    # 4. 判斷新訂單
    new_order_ids = []
    if current_max > 0:
        cur.execute(f"SELECT id FROM orders WHERE daily_seq > %s AND {time_filter} ORDER BY daily_seq ASC", (current_max,))
        new_order_ids = [r[0] for r in cur.fetchall()]
        
    conn.close()

    # 5. 生成 HTML (簡化版，邏輯與您原本的一致)
    html_content = ""
    if not orders: 
        html_content = "<div style='grid-column:1/-1;text-align:center;padding:100px;font-size:1.5em;color:#666;'>🍽️ 目前沒有訂單，廚房休息中</div>"
    
    for o in orders:
        oid, table, raw_items, total, status, created, order_lang, seq_num, c_json = o
        status_cls = status.lower()
        seq_str = f"#{seq_num:03d}"
        tw_time = created + timedelta(hours=8)
        time_str = tw_time.strftime('%H:%M')
        
        items_html = ""
        try:
            if c_json:
                cart = json.loads(c_json)
                for item in cart:
                    name = item.get('name_zh', item.get('name', '商品'))
                    qty = item.get('qty', 1)
                    options = item.get('options_zh', item.get('options', []))
                    opts_html = f"<div class='item-opts'>└ {' / '.join(options)}</div>" if options else ""
                    items_html += f"<div class='item-row'><div class='item-name'><span>{name}</span><span class='item-qty'>x{qty}</span></div>{opts_html}</div>"
            else: 
                clean_items = raw_items.replace("+", "<br>").replace("undefined", "")
                items_html = f"<div class='item-row'>{clean_items}</div>"
        except: 
            items_html = f"<div style='color:red'>資料解析錯誤</div>"
            
        buttons_html = ""
        if status == 'Pending': 
            buttons_html += f"""
            <button onclick='action("/kitchen/complete/{oid}")' class='btn btn-main'>✅ 出餐 / 付款</button>
            <div class="btn-group">
                <a href='/print_order/{oid}' target='_blank' class='btn btn-print'>🖨️ 補印</a>
                <a href='/menu?edit_oid={oid}&lang=zh' target='_blank' class='btn btn-edit'>✏️ 修改</a>
                <button onclick='if(confirm("⚠️ 作廢？")) action("/order/cancel/{oid}")' class='btn btn-void'>🗑️</button>
            </div>"""
        else:
            buttons_html += f"<div class='btn-group'><a href='/print_order/{oid}' target='_blank' class='btn btn-print' style='width:100%'>🖨️ 補印單據</a></div>"
        
        html_content += f"""
        <div class="card {status_cls}">
            <div class="card-header">
                <div><div class="seq-num">{seq_str}</div><div class="time-stamp">{time_str} ({order_lang})</div></div>
                <div class="table-num">桌號 {table}</div>
            </div>
            <div class="items">{items_html}</div>
            <div class="actions">{buttons_html}</div>
        </div>"""
        
    return jsonify({'html': html_content, 'max_seq': max_seq_val, 'new_ids': new_order_ids})


# --- 6. 日結報表 (含日期選擇與時區處理) ---
@app.route('/kitchen/report')
def daily_report():
    # 1. 決定要查詢的日期 (接收前端傳來的 ?date=YYYY-MM-DD)
    target_date_str = request.args.get('date')
    
    # 如果沒傳參數，預設為「台灣今天的日期」
    if not target_date_str:
        tw_now = datetime.utcnow() + timedelta(hours=8)
        target_date_str = tw_now.strftime('%Y-%m-%d')
    
    # 2. 轉換為 UTC 時間範圍 (用於 SQL 查詢)
    try:
        target_date_obj = datetime.strptime(target_date_str, '%Y-%m-%d')
        
        # 台灣當天的開始與結束
        tw_start = target_date_obj.replace(hour=0, minute=0, second=0)
        tw_end = target_date_obj.replace(hour=23, minute=59, second=59)
        
        # 轉回 UTC (減 8 小時)
        utc_start = tw_start - timedelta(hours=8)
        utc_end = tw_end - timedelta(hours=8)
        
        # SQL 條件字串
        time_filter = f"created_at >= '{utc_start}' AND created_at <= '{utc_end}'"
        
    except ValueError:
        return "日期格式錯誤，請使用 YYYY-MM-DD"

    # 3. 執行資料庫查詢
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 3-1. 查詢有效單 (排除 Cancelled)
    cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
    valid_count, valid_total = cur.fetchone()
    
    # 獲取詳細內容
    cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'")
    valid_rows = cur.fetchall()
    
    # 3-2. 查詢作廢單
    cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
    void_count, void_total = cur.fetchone()
    
    cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status = 'Cancelled'")
    void_rows = cur.fetchall()
    
    conn.close()

    # ==========================================
    # 4. 統計品項邏輯 (強力偵測版)
    # ==========================================
    def agg_items(rows, label="有效單"):
        stats = {}
        # 用來控制只印出一次 Debug 訊息，避免洗版
        debug_printed = False 

        for r in rows:
            if not r[0]: continue
            
            try:
                # 解析 JSON
                items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                if isinstance(items, dict): items = [items]
                
                # --- [DEBUG] 印出第一筆資料的結構到終端機 ---
                if not debug_printed and len(items) > 0:
                    print(f"\n======== [DEBUG: {label}] JSON 結構檢查 ========")
                    print(f"原始資料: {items[0]}")
                    print(f"包含 Key: {items[0].keys()}")
                    print("================================================\n")
                    debug_printed = True
                # ------------------------------------------------

                for i in items:
                    name = i.get('name_zh', i.get('name', '未知品項'))
                    
                    # 1. 取得數量
                    try:
                        qty = int(float(i.get('qty', 0)))
                    except:
                        qty = 0

                    # 2. 取得金額 (暴力搜尋所有可能的 Key)
                    line_total = 0
                    
                    # 優先找「單價」
                    # 常見 Key: price, unit_price, cost, amount
                    raw_price = i.get('price') or i.get('unit_price') or i.get('cost') or i.get('amount')
                    
                    # 其次找「該行總額」(有些系統直接存總額)
                    # 常見 Key: total, subtotal, item_total
                    raw_line_total = i.get('total') or i.get('subtotal') or i.get('item_total')

                    try:
                        # 邏輯 A: 如果有單價，用 單價 * 數量
                        if raw_price is not None:
                            unit_price = float(raw_price)
                            line_total = int(unit_price * qty)
                        
                        # 邏輯 B: 如果沒單價但有總額，直接用總額
                        elif raw_line_total is not None:
                            line_total = int(float(raw_line_total))
                            
                        # 邏輯 C: 如果都沒有，金額就是 0 (這代表 JSON 裡真的沒存錢)
                        else:
                            line_total = 0
                    except:
                        line_total = 0

                    # 累加統計
                    if name not in stats:
                        stats[name] = {'qty': 0, 'total_amt': 0}
                    
                    stats[name]['qty'] += qty
                    stats[name]['total_amt'] += line_total
                    
            except Exception as e:
                print(f"解析錯誤: {e}")
                pass
                
        return stats

    # 執行統計
    valid_stats = agg_items(valid_rows, "有效單")
    void_stats = agg_items(void_rows, "作廢單")

    # 5. 渲染表格 HTML
    def render_table(stats_dict):
        if not stats_dict: 
            return "<p style='text-align:center; color:#888; padding:10px;'>無銷售資料 (或 JSON 中缺少 price 欄位)</p>"
        
        h = """
        <table style='width:100%; border-collapse:collapse; font-size:14px; margin-top:5px;'>
            <thead>
                <tr style='border-bottom:2px solid #444; background-color: #f0f0f0;'>
                    <th style='text-align:left; width:50%; padding: 6px;'>品項名稱</th>
                    <th style='text-align:right; width:20%; padding: 6px;'>銷售數量</th>
                    <th style='text-align:right; width:30%; padding: 6px;'>銷售金額</th>
                </tr>
            </thead>
            <tbody>
        """
        
        sorted_items = sorted(stats_dict.items(), key=lambda x: x[1]['qty'], reverse=True)
        
        for name, data in sorted_items: 
            fmt_amt = "{:,}".format(data['total_amt'])
            
            # 如果金額是 0，顯示紅色警告，提示使用者檢查
            color_style = "color:red;" if data['total_amt'] == 0 else "font-family:monospace;"
            
            h += f"""
            <tr style='border-bottom: 1px dotted #ccc;'>
                <td style='padding:8px 4px; vertical-align: middle;'>{name}</td>
                <td style='text-align:right; padding:8px 4px; vertical-align: middle;'>{data['qty']}</td>
                <td style='text-align:right; padding:8px 4px; vertical-align: middle; {color_style}'>${fmt_amt}</td>
            </tr>
            """
        
        h += "</tbody></table>"
        return h

    # 6. 回傳完整 HTML
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>日結報表_{target_date_str}</title>
        <style>
            body {{ font-family: 'Microsoft JhengHei', sans-serif; background: #eee; padding: 20px; display: flex; flex-direction: column; align-items: center; color: #333; }} 
            .ticket {{ background: white; width: 80mm; min-height: 100mm; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.15); margin-bottom: 20px; border-radius: 4px; }} 
            h2 {{ text-align: center; margin: 5px 0 10px 0; border-bottom: 2px solid #333; padding-bottom: 10px; }} 
            
            .summary-box {{ margin-bottom: 20px; font-size: 15px; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #28a745; }} 
            .summary-item {{ display: flex; justify-content: space-between; margin-bottom: 5px; }}
            .total-price {{ font-size: 20px; color: #28a745; font-weight: bold; }}
            
            .void-box {{ border-left-color: #dc3545; background: #fff5f5; color: #a00; }}
            .section-title {{ font-weight: bold; font-size: 16px; margin-bottom: 8px; display: block; }}

            .controls {{ margin-bottom: 20px; display: flex; flex-direction: column; gap: 15px; align-items: center; background: white; padding: 15px; border-radius: 8px; }}
            .date-picker {{ padding: 8px; border-radius: 5px; border: 1px solid #ccc; font-size: 16px; outline: none; }}
            .btn-group {{ display: flex; gap: 10px; }}
            .btn {{ padding: 10px 20px; border-radius: 5px; text-decoration: none; color: white; cursor: pointer; border: none; font-size: 14px; transition: opacity 0.2s; }}
            .btn:hover {{ opacity: 0.9; }}
            
            @media print {{ 
                .no-print, .controls {{ display: none !important; }} 
                body {{ background: white; padding: 0; margin: 0; }} 
                .ticket {{ box-shadow: none; border: none; width: 100%; margin: 0; padding: 0; }} 
                table, tr, td, th {{ -webkit-print-color-adjust: exact; }}
            }}
        </style>
    </head>
    <body>
        <div class="controls no-print">
            <form action="/kitchen/report" method="get" style="display:flex; align-items:center; gap:10px;">
                <label style="font-weight:bold;">📅 選擇日期：</label>
                <input type="date" name="date" class="date-picker" value="{target_date_str}" onchange="this.form.submit()">
            </form>
            
            <div class="btn-group">
                <button onclick="window.print()" class="btn" style="background:#28a745;">🖨️ 列印報表</button>
                <a href="/kitchen" class="btn" style="background:#6c757d;">🔙 回廚房看板</a>
            </div>
        </div>

        <div class="ticket">
            <h2>日結營收報表</h2>
            <p style="text-align:center; font-size:14px; margin-bottom: 20px;">
                營業日: <b>{target_date_str}</b>
            </p>
            
            <div class="summary-box">
                <span class="section-title">✅ 有效訂單統計</span>
                <div class="summary-item">
                    <span>總單量:</span>
                    <span>{valid_count or 0} 筆</span>
                </div>
                <div class="summary-item">
                    <span>總營業額 (SQL):</span>
                    <span class="total-price">${"{:,}".format(valid_total or 0)}</span>
                </div>
            </div>
            
            <span class="section-title">[ 商品銷售明細 ]</span>
            {render_table(valid_stats)}
            
            <hr>
            
            <div class="summary-box void-box">
                <span class="section-title">❌ 作廢/取消統計</span>
                <div class="summary-item">
                    <span>取消單量:</span>
                    <span>{void_count or 0} 筆</span>
                </div>
                <div class="summary-item">
                    <span>取消金額:</span>
                    <span>${"{:,}".format(void_total or 0)}</span>
                </div>
            </div>
            {render_table(void_stats)}
            
            <hr>
            <p style="text-align:center; font-size:12px; color:#aaa; margin-top: 20px;">
                製表時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </p>
        </div>
    </body>
    </html>
    """
    
# --- 7. 狀態變更 ---
@app.route('/kitchen/complete/<int:oid>')
def complete_order(oid):
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Completed' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

@app.route('/order/cancel/<int:oid>')
def cancel_order(oid):
    c=get_db_connection(); c.cursor().execute("UPDATE orders SET status='Cancelled' WHERE id=%s",(oid,)); c.commit(); c.close()
    return redirect('/kitchen')

# --- 8. 列印路由 (修正長訂單自動分頁問題，寬度長度全自動) ---
@app.route('/print_order/<int:oid>')
def print_order(oid):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT id, table_number, items, total_price, status, created_at, daily_seq, content_json, lang 
        FROM orders WHERE id=%s
    """, (oid,))
    o = cur.fetchone(); conn.close()
    if not o: return "No Data"

    oid_db, table_num, raw_items, total_val, status, created_at, daily_seq, c_json, order_lang = o
    seq = f"{daily_seq:03d}"
    items = []
    try:
        items = json.loads(c_json) if c_json else []
    except: return "解析失敗"

    is_void = (status == 'Cancelled')
    tw_time = created_at + timedelta(hours=8)
    time_str = tw_time.strftime('%Y-%m-%d %H:%M:%S')
    title = "❌ 作廢單 (VOID)" if is_void else "結帳單 (Receipt)"
    style = "text-decoration: line-through; color:red;" if is_void else ""

    def get_display_name(item):
        n_zh = item.get('name_zh', '商品')
        if order_lang == 'zh': return n_zh
        n_foreign = item.get(f'name_{order_lang}', item.get('name', n_zh))
        return f"{n_foreign}<br><small>({n_zh})</small>"

    def mk_ticket(t_name, item_list, show_total=False, is_kitchen=False):
        if not item_list and not show_total: return ""
        h = f"<div class='ticket' style='{style}'><div class='head'><h2>{t_name}</h2><h1>#{seq}</h1><p>Table: {table_num}</p><small>{time_str}</small></div><hr>"
        for i in item_list:
            qty = i.get('qty', 1); u_p = i.get('unit_price', 0)
            d_name = i.get('name_zh', '商品') if is_kitchen else get_display_name(i)
            ops = i.get('options_zh', []) if is_kitchen else i.get(f'options_{order_lang}', i.get('options', []))
            if isinstance(ops, str): ops = [ops]
            h += f"<div class='row'><span>{qty} x {d_name}</span><span>${u_p * qty}</span></div>"
            if ops: h += f"<div class='opt'>└ {', '.join(ops)}</div>"
        if show_total: h += f"<hr><div style='text-align:right;font-size:1.2em;font-weight:bold;'>Total: ${total_val}</div>"
        return h + "</div><div class='break'></div>"

    body = mk_ticket(title, items, show_total=True)
    if not is_void:
        noodles = [i for i in items if i.get('print_category', 'Noodle') == 'Noodle']
        soups = [i for i in items if i.get('print_category') == 'Soup']
        if noodles: body += mk_ticket("🍜 麵區工單", noodles, is_kitchen=True)
        if soups: body += mk_ticket("🍲 湯區工單", soups, is_kitchen=True)

    return f"""
    <html><head><meta charset="UTF-8">
    <style>
        /* 設定紙張：完全由內容決定大小 (auto) */
        @page {{ 
            size: auto; 
            margin: 0; 
        }}
        
        html, body {{
            margin: 0;
            padding: 0;
            background: #fff;
            font-family: 'Microsoft JhengHei', sans-serif;
            font-size: 14px;
            width: auto; /* 寬度自動 */
        }}

        .ticket {{ 
            padding: 4mm;
            box-sizing: border-box;
            page-break-inside: avoid; /* 防止單張票據內部被切斷 */
            overflow: visible;
        }} 

        .head {{ text-align: center; }} 
        .row {{ display: flex; justify-content: space-between; margin-top: 8px; font-weight: bold; gap: 10px; }} 
        .opt {{ font-size: 12px; color: #444; margin-left: 15px; }} 

        .break {{ 
            page-break-after: always; /* 不同工單之間強制換頁，確保自動切紙觸發 */
        }} 

        h1 {{ margin: 5px 0; font-size: 2.5em; }}
        h2 {{ margin: 5px 0; font-size: 1.5em; }}
        hr {{ border: none; border-top: 1px dashed #000; }}
        
        @media print {{ 
            body {{ width: auto; }} 
            .ticket {{ border: none; }}
        }}
    </style></head>
    <body onload='window.print(); setTimeout(function(){{ window.close(); }}, 1200);'>{body}</body></html>
    """


def async_send_report(app_instance):
    with app_instance.app_context():
        try:
            print("🚀 [背景] 開始發信...")
            send_daily_report() 
            print("✅ [背景] 發信成功")
        except Exception as e:
            print(f"❌ [背景] 發信失敗: {e}")
            
# --- 9. 後台管理核心功能 ---

# 發信邏輯：支援傳入 manual_config (測試用) 與 is_test (測試信內容用)
def send_daily_report(manual_config=None, is_test=False):
    print(">>> 準備執行郵件發送程序...")
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if manual_config:
            print("🔧 使用手動輸入的設定進行測試 (不讀取 DB 設定)")
            api_key = manual_config.get('resend_api_key', '').strip()
            to_email = manual_config.get('report_email', '').strip()
            sender_email = manual_config.get('sender_email', '').strip()
        else:
            print("📂 讀取資料庫設定進行發送")
            cur.execute("SELECT key, value FROM settings")
            rows = cur.fetchall()
            config = dict(rows) if rows else {}
            api_key = config.get('resend_api_key', '').strip()
            to_email = config.get('report_email', '').strip()
            sender_email = config.get('sender_email', '').strip()

        if not sender_email: sender_email = 'onboarding@resend.dev'
        if not api_key:
            print("❌ 發信失敗：未設定 Resend API Key")
            return
        if not to_email:
            print("❌ 發信失敗：未設定收件人 Email")
            return

        # 準備郵件內容
        utc_now = datetime.utcnow()
        tw_now = utc_now + timedelta(hours=8)
        today_str = tw_now.strftime('%Y-%m-%d')
        
        if is_test:
            subject = f"【連線測試】Resend API 設定確認 ({today_str})"
            email_content = "✅ Resend API 連線成功！\n此為測試信件。"
            print("🧪 模式：僅發送測試內容")
        else:
            tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
            tw_end = tw_now.replace(hour=23, minute=59, second=59, microsecond=999999)
            utc_start = tw_start - timedelta(hours=8)
            utc_end = tw_end - timedelta(hours=8)
            time_filter = f"created_at >= '{utc_start}' AND created_at <= '{utc_end}'"

            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            v_res = cur.fetchone()
            v_count = v_res[0] if v_res and v_res[0] else 0
            v_total = v_res[1] if v_res and v_res[1] else 0
            
            cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'")
            x_res = cur.fetchone()
            x_count = x_res[0] if x_res and x_res[0] else 0
            x_total = x_res[1] if x_res and x_res[1] else 0

            cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'")
            valid_rows = cur.fetchall()
            
            item_detail_text = ""
            if valid_rows:
                stats = {}
                for r in valid_rows:
                    if not r[0]: continue
                    try:
                        items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                        if isinstance(items, dict): items = [items]
                        for i in items:
                            name = i.get('name_zh', i.get('name', '未知'))
                            try: qty = int(float(i.get('qty', 0)))
                            except: qty = 0
                            try: price = int(float(i.get('price', 0) or i.get('unit_price', 0) or 0))
                            except: price = 0
                            
                            if name not in stats: stats[name] = {'qty': 0, 'subtotal': 0}
                            stats[name]['qty'] += qty
                            stats[name]['subtotal'] += (qty * price)
                    except: pass
                
                if stats:
                    item_detail_text = "\n【品項銷量統計】\n"
                    for name, data in sorted(stats.items(), key=lambda x: x[1]['qty'], reverse=True):
                        item_detail_text += f"• {name}: {data['qty']} 份 (${data['subtotal']})\n"

            subject = f"【日結單】{today_str} 營業統計報告"
            email_content = f"""
🍴 餐廳日結報表 ({today_str})
---------------------------------
✅ 【有效營收】
單量：{v_count} 筆
總額：${v_total}
{item_detail_text}
---------------------------------
❌ 【作廢統計】
單量：{x_count} 筆
總額：${x_total}
---------------------------------
報告產出時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')} (TW)
"""

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
        with urllib.request.urlopen(req, timeout=20, context=ctx) as res:
            print(f"✅ 發送成功！Status Code: {res.status}")

    except Exception as e:
        print(f"❌ 發送錯誤: {e}")
    finally:
        cur.close()
        conn.close()

def async_send_report(app_instance, manual_config=None, is_test=False):
    with app_instance.app_context():
        send_daily_report(manual_config, is_test)

# --- 路由功能 (匯出/匯入/重置/產品管理) ---

@app.route('/admin/export_menu')
def export_menu():
    """匯出菜單為 Excel"""
    try:
        conn = get_db_connection()
        # 使用 pandas 读取 SQL
        df = pd.read_sql("SELECT * FROM products ORDER BY sort_order ASC", conn)
        conn.close()
        
        output = io.BytesIO()
        # 需要 openpyxl 庫
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        
        return send_file(
            output, 
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            as_attachment=True, 
            download_name="menu_export.xlsx"
        )
    except Exception as e:
         return redirect(url_for('admin_panel', msg=f"❌ 匯出失敗: {e}"))

@app.route('/admin/import_menu', methods=['POST'])
def import_menu():
    """匯入 Excel 菜單"""
    try:
        file = request.files.get('menu_file')
        if not file: return redirect(url_for('admin_panel', msg="❌ 無檔案"))
        
        df = pd.read_excel(file, engine='openpyxl')
        # 將 NaN 轉為 None 以便 SQL 處理
        df = df.where(pd.notnull(df), None)
        conn = get_db_connection()
        cur = conn.cursor()
        
        cnt = 0
        for _, p in df.iterrows():
            if not p.get('name'): continue
            # 這裡就是關鍵的 INSERT 語法，必須包在 execute 字串內
            cur.execute("""
                INSERT INTO products (
                    name, price, category, print_category, sort_order, is_available, image_url,
                    name_en, name_jp, name_kr, category_en, category_jp, category_kr,
                    custom_options, custom_options_en, custom_options_jp, custom_options_kr
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                str(p.get('name')), p.get('price', 0), p.get('category'), p.get('print_category','Noodle'), 
                p.get('sort_order', 99), bool(p.get('is_available', True)), p.get('image_url'),
                p.get('name_en'), p.get('name_jp'), p.get('name_kr'),
                p.get('category_en'), p.get('category_jp'), p.get('category_kr'),
                p.get('custom_options'), p.get('custom_options_en'), p.get('custom_options_jp'), p.get('custom_options_kr')
            ))
            cnt += 1
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('admin_panel', msg=f"✅ 匯入 {cnt} 筆成功"))
    except Exception as e:
        return redirect(url_for('admin_panel', msg=f"❌ 匯入失敗: {e}"))

@app.route('/admin/reset_menu')
def reset_menu():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE products RESTART IDENTITY CASCADE")
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/admin/reset_orders')
def reset_orders():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE orders RESTART IDENTITY CASCADE")
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/admin/toggle_product/<int:pid>', methods=['POST'])
def toggle_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_available FROM products WHERE id = %s", (pid,))
    row = cur.fetchone()
    if row:
        new_s = not row[0]
        # 注意：這裡的 SQL 必須在引號內
        cur.execute("UPDATE products SET is_available = %s WHERE id = %s", (new_s, pid))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'is_available': new_s})
    conn.close()
    return jsonify({'status': 'error'}), 404

@app.route('/admin/delete_product/<int:pid>')
def delete_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel', msg=f"🗑️ 產品 ID:{pid} 已刪除"))

@app.route('/admin/reorder_products', methods=['POST'])
def reorder_products():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    order_list = data.get('order', [])
    for idx, pid in enumerate(order_list):
        # 注意：這裡的 SQL 必須在引號內
        cur.execute("UPDATE products SET sort_order = %s WHERE id = %s", (idx, pid))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/admin/edit_product/<int:pid>', methods=['GET', 'POST'])
def edit_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        cur.execute("""
            UPDATE products SET 
            name=%s, price=%s, category=%s, print_category=%s, image_url=%s,
            name_en=%s, name_jp=%s, name_kr=%s,
            category_en=%s, category_jp=%s, category_kr=%s,
            custom_options=%s, custom_options_en=%s, custom_options_jp=%s, custom_options_kr=%s
            WHERE id=%s
        """, (
            request.form.get('name'), request.form.get('price'), request.form.get('category'), 
            request.form.get('print_category'), request.form.get('image_url'),
            request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'),
            request.form.get('category_en'), request.form.get('category_jp'), request.form.get('category_kr'),
            request.form.get('custom_options'), request.form.get('custom_options_en'), 
            request.form.get('custom_options_jp'), request.form.get('custom_options_kr'),
            pid
        ))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel', msg="✅ 產品已更新"))

    cur.execute("SELECT * FROM products WHERE id = %s", (pid,))
    # 使用 DictCursor 比較方便，這裡手動轉 dict
    columns = [desc[0] for desc in cur.description]
    row = cur.fetchone()
    conn.close()
    
    if not row: return "Product not found"
    p = dict(zip(columns, row))
    
    # 簡單的編輯表單 HTML
    return f"""
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    <style>.container {{ max-width: 800px; padding: 20px; }}</style></head>
    <body><div class="container">
        <h3>✏️ 編輯產品: {p.get('name')}</h3>
        <form method="POST">
            <div class="row"><div class="column"><label>名稱</label><input type="text" name="name" value="{p.get('name','')}"></div>
            <div class="column"><label>價格</label><input type="number" name="price" value="{p.get('price','')}"></div></div>
            <div class="row"><div class="column"><label>分類</label><input type="text" name="category" value="{p.get('category','')}"></div>
            <div class="column"><label>出單區</label><select name="print_category">
                <option value="Noodle" {'selected' if p.get('print_category')=='Noodle' else ''}>🍜 麵台</option>
                <option value="Soup" {'selected' if p.get('print_category')=='Soup' else ''}>🍲 湯台</option>
            </select></div></div>
            <label>圖片網址</label><input type="text" name="image_url" value="{p.get('image_url','') or ''}">
            
            <details open><summary>多國語言與選項</summary>
                <div class="row"><div class="column"><input type="text" name="name_en" value="{p.get('name_en','') or ''}" placeholder="Name EN"></div>
                <div class="column"><input type="text" name="name_jp" value="{p.get('name_jp','') or ''}" placeholder="Name JP"></div></div>
                <label>選項 (中文)</label><input type="text" name="custom_options" value="{p.get('custom_options','') or ''}">
            </details>
            <br>
            <button type="submit">💾 儲存更新</button> <a href="/admin" class="button button-outline">取消</a>
        </form>
    </div></body></html>
    """

# --- Admin Panel 主頁面 ---

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    conn = get_db_connection()
    cur = conn.cursor()
    msg = request.args.get('msg', '') 
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save_settings':
            # 使用 Upsert (PostgreSQL 特有語法: ON CONFLICT)
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ('report_email', request.form.get('report_email')))
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ('sender_email', request.form.get('sender_email')))
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", ('resend_api_key', request.form.get('resend_api_key')))
            conn.commit()
            conn.close()
            return redirect(url_for('admin_panel', msg="✅ 設定已儲存"))
        elif action == 'test_email':
            temp_config = {
                'report_email': request.form.get('report_email'),
                'sender_email': request.form.get('sender_email'),
                'resend_api_key': request.form.get('resend_api_key')
            }
            conn.close()
            threading.Thread(target=async_send_report, args=(current_app._get_current_object(), temp_config, True)).start()
            return redirect(url_for('admin_panel', msg="🧪 測試信發送中"))
        elif action == 'send_report_now':
            conn.close()
            threading.Thread(target=async_send_report, args=(current_app._get_current_object(), None, False)).start()
            return redirect(url_for('admin_panel', msg="📊 報表發送中"))
        elif action == 'add_product':
            cur.execute("""INSERT INTO products (name, price, category, print_category, image_url, name_en, name_jp, name_kr, category_en, category_jp, category_kr, custom_options, custom_options_en, custom_options_jp, custom_options_kr) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", 
                       (request.form.get('name'), request.form.get('price'), request.form.get('category'), request.form.get('print_category'), request.form.get('image_url'),
                        request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'), request.form.get('category_en'), request.form.get('category_jp'), request.form.get('category_kr'),
                        request.form.get('custom_options'), request.form.get('custom_options_en'), request.form.get('custom_options_jp'), request.form.get('custom_options_kr')))
            conn.commit()
            conn.close()
            return redirect(url_for('admin_panel', msg="✅ 新增成功"))

    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    cur.execute("SELECT id, name, price, category, is_available, print_category, sort_order, image_url FROM products ORDER BY sort_order ASC, id DESC")
    prods = cur.fetchall()
    conn.close()

    rows = ""
    for p in prods:
        status_cls = "status-on" if p[4] else "status-off"
        status_txt = "上架中" if p[4] else "已下架"
        img_icon = "🖼️" if p[7] else ""
        rows += f"""<tr data-id='{p[0]}' class='product-row'>
            <td class='handle'>☰</td><td data-label="ID">{p[0]}</td>
            <td data-label="品名" class='search-key'><div class="prod-name">{p[1]} {img_icon}</div><div class="prod-cat">{p[3]} / {p[5]}</div></td>
            <td data-label="價格"><b>${p[2]}</b></td>
            <td data-label="狀態"><button onclick='toggleProduct({p[0]}, this)' class='btn-sm {status_cls}'>{status_txt}</button></td>
            <td data-label="操作" class="actions">
                <a href='/admin/edit_product/{p[0]}' class="btn-icon edit">✎</a>
                <a href='/admin/delete_product/{p[0]}' class="btn-icon del" onclick='return confirm("確定刪除?")'>✖</a>
            </td></tr>"""

    return f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>後台</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.14.0/Sortable.min.js"></script>
    <style>
        :root {{ --primary: #9b4dca; }}
        body {{ background: #f4f5f7; padding-bottom: 50px; }}
        .container {{ max-width: 1000px; margin: 0 auto; padding: 20px; }}
        .card {{ background: #fff; border-radius: 12px; padding: 25px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); margin-bottom: 25px; }}
        .btn-full {{ width: 100%; }} .btn-sm {{ padding: 0 10px; height: 28px; line-height: 26px; }}
        .status-on {{ background: #e6ffed; color: #28a745; border-color: #b7eb8f; }}
        .status-off {{ background: #fff1f0; color: #f5222d; border-color: #ffa39e; }}
        .alert {{ padding: 12px; background: #e6f7ff; color: #0050b3; display: none; margin-bottom: 20px; border-radius: 6px; }}
        table {{ width: 100%; margin-top: 10px; }}
        @media (max-width: 600px) {{ 
            table, thead, tbody, th, td, tr {{ display: block; }} thead tr {{ position: absolute; top: -9999px; }}
            tr {{ margin-bottom: 15px; padding: 15px; background: #fff; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
            td {{ border: none; position: relative; padding-left: 0; }}
        }}
    </style>
    </head><body>
    <div class="container">
        <h2>🍴 餐廳後台</h2><a href="/kitchen" class="button">👨‍🍳 廚房看板</a>
        <div id="status-msg" class="alert">{msg}</div>
        <div class="card">
            <h4>⚙️ 設定</h4>
            <form method="POST">
                <input type="email" name="report_email" value="{config.get('report_email','')}" placeholder="To Email">
                <input type="email" name="sender_email" value="{config.get('sender_email','')}" placeholder="From Email">
                <input type="password" name="resend_api_key" value="{config.get('resend_api_key','')}" placeholder="API Key">
                <button type="submit" name="action" value="save_settings">💾 儲存</button>
                <button type="submit" name="action" value="test_email" class="button button-outline">🧪 測試</button>
            </form>
            <hr>
            <form method="POST"><button type="submit" name="action" value="send_report_now">📊 發送報表</button></form>
        </div>
        <div class="card">
            <h4>➕ 新增產品</h4>
            <form method="POST">
                <input type="hidden" name="action" value="add_product">
                <div class="row"><div class="column"><input type="text" name="name" required placeholder="名稱"></div><div class="column"><input type="number" name="price" required placeholder="價格"></div></div>
                <input type="text" name="category" placeholder="分類">
                <input type="text" name="image_url" placeholder="圖片網址">
                <details><summary>多國語言 / 選項</summary>
                    <input type="text" name="name_en" placeholder="Name EN"><input type="text" name="name_jp" placeholder="Name JP">
                    <input type="text" name="custom_options" placeholder="選項 (如: 加辣)">
                </details>
                <button type="submit" class="button btn-full">🚀 新增</button>
            </form>
        </div>
        <div class="card">
            <h4>📋 菜單管理</h4>
            <input type="text" id="productSearch" placeholder="搜尋...">
            <table>
                <thead><tr><th width="50">排序</th><th width="50">ID</th><th>品項</th><th>價格</th><th>狀態</th><th>操作</th></tr></thead>
                <tbody id="menu-list">{rows}</tbody>
            </table>
            <hr>
            <a href="/admin/export_menu" class="button button-outline">📤 匯出</a>
            <form action="/admin/import_menu" method="POST" enctype="multipart/form-data" style="display:inline;">
                <input type="file" name="menu_file" required> <button class="button button-outline">📥 匯入</button>
            </form>
            <div style="float:right;">
                <a href="/admin/reset_menu" class="button" style="background:red;" onclick="return confirm('確定清空菜單?')">🗑️ 清菜單</a>
                <a href="/admin/reset_orders" class="button" style="background:red;" onclick="return confirm('確定清空訂單?')">💥 清訂單</a>
            </div>
        </div>
    </div>
    <script>
        const msg = document.getElementById('status-msg');
        if(msg.innerText) {{ msg.style.display='block'; setTimeout(()=>msg.style.display='none',3000); }}
        
        function toggleProduct(pid, btn) {{
            fetch('/admin/toggle_product/'+pid, {{method:'POST'}}).then(r=>r.json()).then(d=>{{
                if(d.status==='success') {{ btn.className = d.is_available?'btn-sm status-on':'btn-sm status-off'; btn.innerText = d.is_available?'上架中':'已下架'; }}
            }});
        }}
        document.getElementById('productSearch').addEventListener('input', e=>{{
            let v = e.target.value.toLowerCase();
            document.querySelectorAll('.product-row').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(v)?'':'none');
        }});
        Sortable.create(document.getElementById('menu-list'), {{
            handle: '.handle', onEnd: function() {{
                let order = Array.from(document.querySelectorAll('#menu-list tr')).map(r=>r.getAttribute('data-id'));
                fetch('/admin/reorder_products', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{order:order}})}});
            }}
        }});
    </script></body></html>
    """

@app.route('/')
def index():
    return "系統運作中。<a href='/admin'>進入後台</a>"

    
# --- 編輯產品頁面 (強制欄位順序版) ---
@app.route('/admin/edit_product/<int:pid>', methods=['GET','POST'])
def edit_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        try:
            # POST 部分維持不變，因為這裡是指定欄位名稱寫入的，不會有順序問題
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
            return redirect('/admin')
        except Exception as e:
            conn.rollback()
            return f"Update Error: {e}"
        finally:
            conn.close()

    # --- 修正重點：明確指定 SELECT 的欄位順序 ---
    # 不要用 SELECT *，改用明確列出，這樣我們就絕對知道第幾個是誰
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
    
    if not row:
        return "找不到該產品", 404

    # --- 建立絕對對應表 (根據上方的 SELECT 順序) ---
    # 因為 SQL 是我們手寫的，順序絕對固定，不會再錯位
    idx = {
        'id': 0, 'name': 1, 'price': 2, 'category': 3, 'image_url': 4,
        'custom_options': 5, 'sort_order': 6,
        'name_en': 7, 'name_jp': 8, 'name_kr': 9,
        'custom_options_en': 10, 'custom_options_jp': 11, 'custom_options_kr': 12,
        'print_category': 13,
        'category_en': 14, 'category_jp': 15, 'category_kr': 16
    }

    # 取值函式
    def v(key):
        try:
            val = row[idx[key]]
            return val if val is not None else ""
        except IndexError:
            return ""

    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>編輯產品</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    <style>
        body {{ padding: 20px; background: #f4f7f6; }}
        .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        h5 {{ background: #9b4dca; color: white; padding: 5px 10px; border-radius: 4px; margin-top: 20px; }}
        hr {{ margin: 30px 0; }}
        .button-outline {{ margin-left: 10px; }}
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
                    <div class="column"><label>排序 (小到大)</label><input type="number" name="sort_order" value="{v('sort_order')}"></div>
                </div>

                <h5>2. 分類與區域</h5>
                <div class="row">
                    <div class="column"><label>分類 (中文)</label><input type="text" name="category" value="{v('category')}"></div>
                    <div class="column"><label>分類 (EN)</label><input type="text" name="category_en" value="{v('category_en')}"></div>
                    <div class="column"><label>分類 (JP)</label><input type="text" name="category_jp" value="{v('category_jp')}"></div>
                    <div class="column"><label>分類 (KR)</label><input type="text" name="category_kr" value="{v('category_kr')}"></div>
                </div>
                <div class="row">
                    <div class="column">
                        <label>出單區域</label>
                        <select name="print_category">
                            <option value="Noodle" {'selected' if v('print_category')=='Noodle' else ''}>麵區</option>
                            <option value="Soup" {'selected' if v('print_category')=='Soup' else ''}>湯區</option>
                        </select>
                    </div>
                    <div class="column"><label>圖片 URL</label><input type="text" name="image_url" value="{v('image_url')}"></div>
                </div>

                <hr>

                <h5>🌐 品名多國語言</h5>
                <div class="row">
                    <div class="column"><label>English Name</label><input type="text" name="name_en" value="{v('name_en')}"></div>
                    <div class="column"><label>日本語 名称</label><input type="text" name="name_jp" value="{v('name_jp')}"></div>
                    <div class="column"><label>한국어 이름</label><input type="text" name="name_kr" value="{v('name_kr')}"></div>
                </div>

                <hr>

                <h5>🛠️ 客製化選項翻譯 (以逗號分隔)</h5>
                <label>中文選項 (例如: 加麵, 去蔥)</label>
                <input type="text" name="custom_options" value="{v('custom_options')}">
                <div class="row">
                    <div class="column"><label>English Options</label><input type="text" name="custom_options_en" value="{v('custom_options_en')}"></div>
                    <div class="column"><label>日本語オプション</label><input type="text" name="custom_options_jp" value="{v('custom_options_jp')}"></div>
                    <div class="column"><label>한국어 옵션</label><input type="text" name="custom_options_kr" value="{v('custom_options_kr')}"></div>
                </div>

                <div style="margin-top:30px; text-align: right;">
                    <a href="/admin" class="button button-outline">❌ 取消</a>
                    <button type="submit">💾 儲存變更</button>
                </div>
            </form>
        </div>
    </body></html>"""
    
    

    
# --- 背景維護工作 (防休眠 + 監控日誌) ---
def run_maintenance_tasks():
    print("🚀 背景維護執行緒已啟動 (Maintenance Thread Started)")
    
    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S") # 取得當前時間
        
        # 1. 防止 Render 休眠 (Ping 網址)
        try:
            # 設定 timeout 避免卡住
            urllib.request.urlopen("https://qr-mbdv.onrender.com", timeout=10)
            print(f"[{now}] ✅ Web Ping 成功 (Render is alive)")
        except Exception as e:
            print(f"[{now}] ❌ Web Ping 失敗: {e}")

        # 2. 防止 Aiven 資料庫休眠 (Ping 資料庫)
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1") # 最輕量的查詢
            cur.close()
            conn.close()
            print(f"[{now}] 💓 DB Heartbeat 成功 (Aiven is alive)")
        except Exception as e:
            print(f"[{now}] ⚠️ DB 連線失敗: {e}")

        # 為了避免 Log 太多太亂，可以把這行分隔線拿掉，看個人喜好
        print("-" * 30)

        # 休息 5 分鐘 (300秒)
        time.sleep(300)

# 啟動背景執行緒
threading.Thread(target=run_maintenance_tasks, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
