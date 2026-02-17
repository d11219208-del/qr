# routes/admin_routes.py
import io
import json
import threading
import traceback
import pandas as pd
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app, session

# 從資料庫模組匯入連線函式 (PostgreSQL)
from database import get_db_connection
# 從 utils 匯入發信功能 (假設已支援 store_id)
from utils import send_daily_report

admin_bp = Blueprint('admin', __name__)

# --- 輔助函式：取得當前店鋪 ID ---
def get_current_store_id():
    """
    從 Session 取得 store_id。
    若未登入或無 store_id，預設回傳 1 (總店)，確保系統不會崩潰。
    """
    return session.get('store_id', 1)

# --- 輔助函式：設定檔 Upsert (針對多店鋪優化) ---
def upsert_setting(cur, key, value, store_id):
    """
    更新或新增設定值。
    由於多店鋪架構下 key 不再是全域唯一，需檢查 (key + store_id)。
    """
    # 1. 嘗試更新
    cur.execute(
        "UPDATE settings SET value = %s WHERE key = %s AND store_id = %s",
        (str(value), key, store_id)
    )
    # 2. 如果沒有更新到任何資料 (表示不存在)，則新增
    if cur.rowcount == 0:
        cur.execute(
            "INSERT INTO settings (key, value, store_id) VALUES (%s, %s, %s)",
            (key, str(value), store_id)
        )

# ==========================================
# 核心路由：後台主面板
# ==========================================
@admin_bp.route('/', methods=['GET', 'POST'])
def admin_panel():
    conn = get_db_connection()
    cur = conn.cursor()
    msg = request.args.get('msg', '')
    
    # 取得當前操作的店鋪 ID
    store_id = get_current_store_id()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        # --- 功能 1: 儲存一般設定 & 測試連線 ---
        if action == 'save_settings' or action == 'test_email':
            try:
                # 1. 取得表單資料
                new_config = {
                    'report_email': request.form.get('report_email'),
                    'resend_api_key': request.form.get('resend_api_key'),
                    'sender_email': request.form.get('sender_email') or 'onboarding@resend.dev'
                }

                # 2. 寫入資料庫 (使用 store_id 隔離)
                for k, v in new_config.items():
                    upsert_setting(cur, k, v, store_id)
                conn.commit()
                
                # 3. 判斷是否執行測試
                should_test = (request.form.get('test_connection') == 'on') or (action == 'test_email')

                if should_test:
                    try:
                        app_obj = current_app._get_current_object()
                        # 注意：send_daily_report 內部邏輯也需支援 store_id
                        result_msg = send_daily_report(app_obj, manual_config=new_config, is_test=True, store_id=store_id)
                        
                        if "✅" in result_msg:
                            msg = f"✅ 設定已儲存 / {result_msg}"
                        else:
                            msg = f"⚠️ 設定已存，但連線測試失敗: {result_msg}"
                            
                    except Exception as e:
                        traceback.print_exc()
                        msg = f"✅ 設定已儲存 / ❌ 測試失敗: {str(e)}"
                else:
                    msg = "✅ 設定已儲存"
                    
            except Exception as e:
                conn.rollback()
                msg = f"❌ 儲存失敗: {e}"
            finally:
                cur.close(); conn.close()
            
            return redirect(url_for('admin.admin_panel', msg=msg))

        # --- 功能 2: 手動觸發日結報表 ---
        elif action == 'send_report_now':
            try:
                app_obj = current_app._get_current_object()
                # 傳遞 store_id 以發送該店報表
                threading.Thread(target=send_daily_report, args=(app_obj,), kwargs={'is_test': False, 'store_id': store_id}).start()
                msg = "🚀 報表正在背景發送中"
            except Exception as e:
                msg = f"❌ 無法啟動背景任務: {e}"
            
            cur.close(); conn.close()
            return redirect(url_for('admin.admin_panel', msg=msg))

        # --- 功能 3: 新增產品 (多店鋪) ---
        elif action == 'add_product':
            try:
                cur.execute("""
                    INSERT INTO products (
                        store_id, 
                        name, price, category, print_category, image_url, sort_order,
                        name_en, name_jp, name_kr,
                        custom_options, custom_options_en, custom_options_jp, custom_options_kr,
                        category_en, category_jp, category_kr
                    ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    store_id, # <--- 關鍵：寫入店鋪 ID
                    request.form.get('name'), request.form.get('price'), request.form.get('category'), 
                    request.form.get('print_category'), request.form.get('image_url'),
                    request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'),
                    request.form.get('custom_options'), request.form.get('custom_options_en'), request.form.get('custom_options_jp'), request.form.get('custom_options_kr'),
                    request.form.get('category_en'), request.form.get('category_jp'), request.form.get('category_kr')
                ))
                conn.commit()
                msg = "✅ 品項已新增"
            except Exception as e:
                conn.rollback()
                msg = f"❌ 新增失敗: {e}"
            finally:
                cur.close(); conn.close()
            return redirect(url_for('admin.admin_panel', msg=msg))

    # --- GET: 讀取資料顯示頁面 ---
    try:
        # 1. 讀取該店設定檔
        cur.execute("SELECT key, value FROM settings WHERE store_id = %s", (store_id,))
        settings_rows = cur.fetchall()
        config = {row[0]: row[1] for row in settings_rows} 
        
        # 資料型態轉換
        toggle_keys = ['shop_open', 'enable_delivery', 'delivery_enabled']
        for key in toggle_keys:
            val = config.get(key, '0')
            config[key] = 1 if val == '1' else 0

        if 'enable_delivery' not in config:
            config['enable_delivery'] = config.get('delivery_enabled', 0)
        
        config.setdefault('delivery_min_price', '0')
        config.setdefault('delivery_fee_base', '0')
        config.setdefault('delivery_max_km', '5')
        config.setdefault('delivery_fee_per_km', '10')

        # 2. 讀取該店產品
        cur.execute("""
            SELECT id, name, price, category, is_available, print_category, sort_order, image_url, 
                   name_en, name_jp, name_kr 
            FROM products 
            WHERE store_id = %s 
            ORDER BY sort_order ASC, id DESC
        """, (store_id,))
        prods = cur.fetchall()
    finally:
        cur.close(); conn.close()
    
    return render_template('admin.html', config=config, prods=prods, msg=msg)


# ==========================================
# 外送詳細設定 (表單提交)
# ==========================================
@admin_bp.route('/settings/delivery', methods=['POST'])
def update_delivery_settings():
    conn = get_db_connection()
    cur = conn.cursor()
    store_id = get_current_store_id()
    
    try:
        is_enabled = '1' if request.form.get('delivery_enabled') else '0'

        settings_to_update = {
            'delivery_enabled': is_enabled,
            'enable_delivery': is_enabled, 
            'delivery_min_price': request.form.get('delivery_min_price') or '0',
            'delivery_fee_base': request.form.get('delivery_fee_base') or '0',
            'delivery_max_km': request.form.get('delivery_max_km') or '5',
            'delivery_fee_per_km': request.form.get('delivery_fee_per_km') or '10'
        }

        for key, val in settings_to_update.items():
            upsert_setting(cur, key, val, store_id)
        
        conn.commit()
        msg = "✅ 外送設定已更新"
    except Exception as e:
        conn.rollback()
        msg = f"❌ 設定更新失敗: {e}"
        traceback.print_exc()
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin.admin_panel', msg=msg))


# ==========================================
# 通用設定切換路由 (AJAX)
# ==========================================
@admin_bp.route('/toggle_config', methods=['POST'])
def toggle_config():
    conn = get_db_connection()
    cur = conn.cursor()
    store_id = get_current_store_id()

    try:
        data = request.get_json()
        key = data.get('key')
        
        allowed_keys = ['shop_open', 'enable_delivery', 'delivery_enabled']
        if key not in allowed_keys:
            return jsonify({'status': 'error', 'message': '不允許的設定項目'}), 400

        # 1. 檢查目前設定值 (針對該店)
        cur.execute("SELECT value FROM settings WHERE key = %s AND store_id = %s", (key, store_id))
        row = cur.fetchone()

        current_val = row[0] if row else '0'
        new_val = '0' if current_val == '1' else '1'
        
        keys_to_update = [key]
        if key in ['enable_delivery', 'delivery_enabled']:
            keys_to_update = ['enable_delivery', 'delivery_enabled']

        # 2. 寫入資料庫
        for k in keys_to_update:
            upsert_setting(cur, k, new_val, store_id)

        conn.commit()
        return jsonify({'status': 'success', 'new_value': (new_val == '1')})

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        cur.close(); conn.close()


# ==========================================
# 編輯產品 (獨立頁面)
# ==========================================
@admin_bp.route('/edit_product/<int:pid>', methods=['GET','POST'])
def edit_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    store_id = get_current_store_id()
    
    if request.method == 'POST':
        try:
            # 確保只能更新自己店鋪的產品
            cur.execute("""
                UPDATE products SET 
                name=%s, price=%s, category=%s, image_url=%s, custom_options=%s,
                name_en=%s, name_jp=%s, name_kr=%s,
                custom_options_en=%s, custom_options_jp=%s, custom_options_kr=%s,
                print_category=%s, sort_order=%s,
                category_en=%s, category_jp=%s, category_kr=%s
                WHERE id=%s AND store_id=%s 
            """, (
                request.form.get('name'), request.form.get('price'), request.form.get('category'),
                request.form.get('image_url'), request.form.get('custom_options'),
                request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'),
                request.form.get('custom_options_en'), request.form.get('custom_options_jp'), request.form.get('custom_options_kr'),
                request.form.get('print_category'), request.form.get('sort_order'),
                request.form.get('category_en'), request.form.get('category_jp'), request.form.get('category_kr'),
                pid, store_id
            ))
            
            if cur.rowcount == 0:
                conn.rollback()
                return "權限錯誤或產品不存在", 403

            conn.commit()
            return redirect(url_for('admin.admin_panel', msg="✅ 產品已更新"))
        except Exception as e:
            conn.rollback()
            return f"Update Error: {e}"
        finally:
            cur.close(); conn.close()

    # 讀取現有資料 (增加 store_id 驗證)
    cur.execute("SELECT * FROM products WHERE id=%s AND store_id=%s", (pid, store_id))
    if cur.description:
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()
    else:
        row = None
        
    cur.close(); conn.close()
    
    if not row: return "找不到該產品或無權限編輯", 404

    p = dict(zip(columns, row))
    def v(key): return p.get(key) if p.get(key) is not None else ""

    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>編輯產品</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    <style>
        body {{ padding: 20px; background: #f4f7f6; font-family: sans-serif; }}
        .container {{ background: white; padding: 30px; border-radius: 10px; max-width: 900px; margin: auto; }}
        h5 {{ background: #9b4dca; color: white; padding: 5px 10px; border-radius: 4px; margin-top: 25px; }}
        label {{ font-weight: bold; margin-top: 10px; }}
        .row {{ margin-bottom: 1rem; }}
    </style>
    </head>
    <body>
        <div class="container">
            <h3>📝 編輯產品 #{v('id')}</h3>
            <form method="POST">
                <h5>1. 基本資料</h5>
                <div class="row">
                    <div class="column column-40"><label>名稱 (中文)</label><input type="text" name="name" value="{v('name')}" required></div>
                    <div class="column"><label>價格</label><input type="number" name="price" value="{v('price')}" required></div>
                    <div class="column"><label>排序</label><input type="number" name="sort_order" value="{v('sort_order')}"></div>
                </div>
                <div class="row">
                    <div class="column">
                        <label>出單區域</label>
                        <select name="print_category">
                            <option value="Noodle" {'selected' if v('print_category')=='Noodle' else ''}>🍜 麵區</option>
                            <option value="Soup" {'selected' if v('print_category')=='Soup' else ''}>🍲 湯區</option>
                        </select>
                    </div>
                    <div class="column column-67"><label>圖片 URL</label><input type="text" name="image_url" value="{v('image_url')}"></div>
                </div>
                <h5>2. 分類 (Category)</h5>
                <div class="row">
                    <div class="column"><label>中文</label><input type="text" name="category" value="{v('category')}"></div>
                    <div class="column"><label>English</label><input type="text" name="category_en" value="{v('category_en')}"></div>
                    <div class="column"><label>日本語</label><input type="text" name="category_jp" value="{v('category_jp')}"></div>
                    <div class="column"><label>한국어</label><input type="text" name="category_kr" value="{v('category_kr')}"></div>
                </div>
                <h5>3. 多語品名 (Name)</h5>
                <div class="row">
                    <div class="column"><label>English</label><input type="text" name="name_en" value="{v('name_en')}"></div>
                    <div class="column"><label>日本語</label><input type="text" name="name_jp" value="{v('name_jp')}"></div>
                    <div class="column"><label>한국어</label><input type="text" name="name_kr" value="{v('name_kr')}"></div>
                </div>
                <h5>4. 客製化選項 (Options)</h5>
                <label>中文選項 (逗號分隔)</label>
                <input type="text" name="custom_options" value="{v('custom_options')}">
                <div class="row">
                    <div class="column"><label>English Options</label><input type="text" name="custom_options_en" value="{v('custom_options_en')}"></div>
                    <div class="column"><label>日本語 Options</label><input type="text" name="custom_options_jp" value="{v('custom_options_jp')}"></div>
                    <div class="column"><label>한국어 Options</label><input type="text" name="custom_options_kr" value="{v('custom_options_kr')}"></div>
                </div>
                <div style="margin-top:30px; text-align: right;">
                    <a href="{url_for('admin.admin_panel')}" class="button button-outline">❌ 取消</a>
                    <button type="submit">💾 儲存變更</button>
                </div>
            </form>
        </div>
    </body></html>"""

# ==========================================
# 匯入 / 匯出 / 重置 (需隔離)
# ==========================================

@admin_bp.route('/export_menu')
def export_menu():
    try:
        store_id = get_current_store_id()
        conn = get_db_connection()
        # 只匯出該店的菜單
        df = pd.read_sql("SELECT * FROM products WHERE store_id = %s ORDER BY sort_order ASC", conn, params=(store_id,))
        conn.close()
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        
        return send_file(
            output, 
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            as_attachment=True, 
            download_name=f"menu_export_store{store_id}_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx"
        )
    except Exception as e:
         return redirect(url_for('admin.admin_panel', msg=f"❌ 匯出失敗: {e}"))

@admin_bp.route('/import_menu', methods=['POST'])
def import_menu():
    try:
        file = request.files.get('menu_file')
        if not file: return redirect(url_for('admin.admin_panel', msg="❌ 無檔案"))
        
        store_id = get_current_store_id()
        df = pd.read_excel(file, engine='openpyxl')
        df = df.where(pd.notnull(df), None)
        
        conn = get_db_connection(); cur = conn.cursor()
        cnt = 0
        for _, p in df.iterrows():
            if not p.get('name'): continue
            
            is_avail = True
            if p.get('is_available') is not None:
                val = str(p.get('is_available')).lower()
                is_avail = val in ['1', 'true', 'yes', 't']

            sql = """
                INSERT INTO products (
                    store_id, name, price, category, image_url, is_available, custom_options, sort_order,
                    name_en, name_jp, name_kr,
                    custom_options_en, custom_options_jp, custom_options_kr,
                    print_category,
                    category_en, category_jp, category_kr
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            params = (
                store_id, str(p.get('name')), p.get('price', 0), p.get('category'),
                p.get('image_url'), is_avail, p.get('custom_options'), p.get('sort_order', 0),
                p.get('name_en'), p.get('name_jp'), p.get('name_kr'),
                p.get('custom_options_en'), p.get('custom_options_jp'), p.get('custom_options_kr'),
                p.get('print_category', 'Noodle'),
                p.get('category_en'), p.get('category_jp'), p.get('category_kr')
            )
            cur.execute(sql, params)
            cnt += 1
            
        conn.commit()
        cur.close(); conn.close()
        return redirect(url_for('admin.admin_panel', msg=f"✅ 成功匯入 {cnt} 筆資料 (Store {store_id})"))
        
    except Exception as e:
        traceback.print_exc()
        return redirect(url_for('admin.admin_panel', msg=f"❌ 匯入失敗: {e}"))

@admin_bp.route('/reset_menu')
def reset_menu():
    conn = get_db_connection(); cur = conn.cursor()
    store_id = get_current_store_id()
    # 絕不使用 TRUNCATE，只刪除該店資料
    cur.execute("DELETE FROM products WHERE store_id = %s", (store_id,))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 本店菜單已清空"))

@admin_bp.route('/reset_orders', methods=['POST'])
def reset_orders():
    conn = get_db_connection(); cur = conn.cursor()
    store_id = get_current_store_id()
    
    try:
        delete_mode = request.form.get('delete_mode')
        if delete_mode == 'all':
            cur.execute("DELETE FROM orders WHERE store_id = %s", (store_id,))
            msg = "💥 已清空本店所有歷史訂單！"
        elif delete_mode == 'range':
            start_date = request.form.get('start_date')
            end_date = request.form.get('end_date')
            if not start_date or not end_date:
                return redirect(url_for('admin.admin_panel', msg="❌ 請選擇完整日期"))
            
            start_ts = f"{start_date} 00:00:00"
            end_ts = f"{end_date} 23:59:59"
            cur.execute("""
                DELETE FROM orders 
                WHERE store_id = %s
                  AND (created_at + interval '8 hours') >= %s 
                  AND (created_at + interval '8 hours') <= %s
            """, (store_id, start_ts, end_ts))
            msg = f"🗑️ 已刪除指定期間訂單，共 {cur.rowcount} 筆。"
        else:
            msg = "❌ 無效的操作"
        conn.commit()
    except Exception as e:
        conn.rollback()
        msg = f"❌ 刪除失敗: {str(e)}"
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin.admin_panel', msg=msg))

@admin_bp.route('/toggle_product/<int:pid>', methods=['POST'])
def toggle_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    store_id = get_current_store_id()
    try:
        cur.execute("SELECT is_available FROM products WHERE id = %s AND store_id = %s", (pid, store_id))
        row = cur.fetchone()
        if row:
            new_s = not row[0]
            cur.execute("UPDATE products SET is_available = %s WHERE id = %s AND store_id = %s", (new_s, pid, store_id))
            conn.commit()
            return jsonify({'status': 'success', 'is_available': new_s})
        return jsonify({'status': 'error', 'message': 'Access denied'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        cur.close(); conn.close()

@admin_bp.route('/delete_product/<int:pid>')
def delete_product(pid):
    conn = get_db_connection(); cur = conn.cursor()
    store_id = get_current_store_id()
    cur.execute("DELETE FROM products WHERE id = %s AND store_id = %s", (pid, store_id))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 產品已刪除"))

@admin_bp.route('/reorder_products', methods=['POST'])
def reorder_products():
    data = request.json
    conn = get_db_connection(); cur = conn.cursor()
    store_id = get_current_store_id()
    try:
        for idx, pid in enumerate(data.get('order', [])):
            cur.execute("UPDATE products SET sort_order = %s WHERE id = %s AND store_id = %s", (idx, pid, store_id))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        cur.close(); conn.close()
