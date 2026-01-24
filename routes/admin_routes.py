from flask import Blueprint, render_template, request, redirect, url_for, Response, send_file
from database import get_db_connection, init_db
from utils import send_daily_report
import pandas as pd
import io

admin_bp = Blueprint('admin', __name__)

# --- 後台主頁 (產品列表) ---
@admin_bp.route('/')
def admin_panel():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products ORDER BY sort_order ASC, id ASC")
    products = cur.fetchall()
    cur.close(); conn.close()
    # 這裡會對應 templates/admin.html
    return render_template('admin.html', products=products)

# --- 產品編輯頁面 ---
@admin_bp.route('/edit/<int:product_id>')
def edit_product_page(product_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
    product = cur.fetchone()
    cur.close(); conn.close()
    # 這裡會對應 templates/edit_product.html
    return render_template('edit_product.html', p=product)

# --- 更新產品邏輯 ---
@admin_bp.route('/update/<int:product_id>', methods=['POST'])
def update_product(product_id):
    f = request.form
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE products SET 
        name=%s, price=%s, category=%s, image_url=%s, is_available=%s, 
        custom_options=%s, sort_order=%s, name_en=%s, name_jp=%s, name_kr=%s,
        custom_options_en=%s, custom_options_jp=%s, custom_options_kr=%s,
        print_category=%s, category_en=%s, category_jp=%s, category_kr=%s
        WHERE id=%s
    """, (
        f['name'], f['price'], f['category'], f['image_url'], 'is_available' in f,
        f['custom_options'], f['sort_order'], f['name_en'], f['name_jp'], f['name_kr'],
        f['custom_options_en'], f['custom_options_jp'], f['custom_options_kr'],
        f['print_category'], f['category_en'], f['category_jp'], f['category_kr'],
        product_id
    ))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('admin.admin_panel'))

# --- 下載訂單 CSV (報表) ---
@admin_bp.route('/download_orders')
def download_orders():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM orders ORDER BY created_at DESC", conn)
    conn.close()
    
    output = io.BytesIO()
    # 使用 Excel 格式 (xlsx) 通常對中文相容性較好
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Orders')
    output.seek(0)
    
    return send_file(output, 
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, 
                     download_name=f"orders_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx")

# --- 系統設定頁面 ---
@admin_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        for key in ['report_email', 'resend_api_key', 'sender_email']:
            val = request.form.get(key, '')
            cur.execute("UPDATE settings SET value=%s WHERE key=%s", (val, key))
        conn.commit()
        return redirect(url_for('admin.settings'))
    
    cur.execute("SELECT key, value FROM settings")
    config = dict(cur.fetchall())
    cur.close(); conn.close()
    return render_template('settings.html', config=config)

# --- 手動觸發報表與資料庫初始化 ---
@admin_bp.route('/run_report')
def run_report():
    msg = send_daily_report()
    return f"結果: {msg} <br><a href='/admin/settings'>回設定頁</a>"

@admin_bp.route('/init_db')
def db_init():
    success = init_db()
    return "✅ 資料庫初始化成功" if success else "❌ 初始化失敗"