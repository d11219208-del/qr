from flask import Blueprint, render_template
from database import get_db_connection
import psycopg2

try_bp = Blueprint('try_debug', __name__)

# 建立資料庫欄位的中英對照表 (依照 database.py 的註解)
# 這樣前端顯示時，除了英文欄位名，還能知道該欄位的用途
COLUMN_MAP = {
    # --- Products (產品表) ---
    'products': {
        'id': '自動遞增 ID',
        'name': '產品名稱 (必填)',
        'price': '價格 (必填)',
        'category': '分類名稱',
        'image_url': '圖片網址',
        'is_available': '是否上架',
        'custom_options': '自定義選項 (中文)',
        'sort_order': '排序序號',
        'name_en': '英文品名',
        'name_jp': '日文品名',
        'name_kr': '韓文品名',
        'custom_options_en': '英文自定義選項',
        'custom_options_jp': '日文自定義選項',
        'custom_options_kr': '韓文自定義選項',
        'print_category': '出單分類 (廚房用)',
        'category_en': '英文分類',
        'category_jp': '日文分類',
        'category_kr': '韓文分類'
    },
    # --- Orders (訂單表) ---
    'orders': {
        'id': '訂單 ID',
        'table_number': '桌號',
        'items': '訂單內容 (簡述)',
        'total_price': '總金額',
        'status': '訂單狀態 (Pending/Completed)',
        'created_at': '建立時間',
        'daily_seq': '當日流水號 (No.001)',
        'content_json': '完整訂單明細 (JSON)',
        'need_receipt': '是否需要收據/統編',
        'lang': '下單語系',
        'order_type': '訂單類型 (內用/外送)',
        'delivery_info': '外送綜合資訊',
        'customer_name': '客戶姓名',
        'customer_phone': '客戶電話',
        'customer_address': '客戶地址',
        'scheduled_for': '預約送達時間',
        'delivery_fee': '外送費'
    },
    # --- Settings (系統設定表) ---
    'settings': {
        'key': '設定鍵名 (如: shop_open)',
        'value': '設定值'
    }
}

@try_bp.route('/try')
def show_db_structure():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 抓取所有資料表名稱 (public schema)
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_type = 'BASE TABLE';
    """)
    tables = [row[0] for row in cur.fetchall()]
    
    db_info = {}
    
    for table in tables:
        # 2. 針對每個表，抓取欄位詳細資訊
        cur.execute(f"""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = '{table}'
            ORDER BY ordinal_position;
        """)
        raw_columns = cur.fetchall()
        
        # 處理欄位資訊，加入中文說明
        columns_info = []
        for col in raw_columns:
            col_name = col[0]
            col_type = col[1]
            
            # 從對照表中尋找中文說明，若找不到則顯示 "未定義說明"
            description = COLUMN_MAP.get(table, {}).get(col_name, '-')
            
            # 將資料組合成字典方便前端讀取
            columns_info.append({
                'name': col_name,
                'type': col_type,
                'nullable': col[2],
                'default': col[3],
                'desc': description  # 新增這個屬性傳給前端
            })
        
        # 3. 抓取該表的所有資料 (這裡限制 50 筆以防頁面太長，可視需求改為不限制)
        cur.execute(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 50")
        sample_rows = cur.fetchall()
        
        db_info[table] = {
            'schema': columns_info, # 包含中文說明的欄位結構
            'data': sample_rows     # 資料內容
        }

    cur.close()
    conn.close()
    
    return render_template('try.html', db_info=db_info)
