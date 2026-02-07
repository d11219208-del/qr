import os
import psycopg2
from urllib.parse import urlparse

# --- 資料庫基礎連線 --- 
def get_db_connection():
    """建立並回傳資料庫連線物件"""
    db_uri = os.environ.get("DATABASE_URL")
    if not db_uri:
        raise ValueError("錯誤：找不到環境變數 DATABASE_URL")
    return psycopg2.connect(db_uri)

# --- 資料庫初始化 ---
def init_db():
    """
    建立所有必要的資料表與預設設定。
    回傳 True 表示成功，False 表示失敗。
    """
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cur = conn.cursor()

        # 1. 建立產品表 (包含多國語系與排序)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY, 
                name VARCHAR(100) NOT NULL, 
                price INTEGER NOT NULL,
                category VARCHAR(50), 
                image_url TEXT, 
                is_available BOOLEAN DEFAULT TRUE,
                custom_options TEXT, 
                sort_order INTEGER DEFAULT 100,
                name_en VARCHAR(100), 
                name_jp VARCHAR(100), 
                name_kr VARCHAR(100),
                custom_options_en TEXT, 
                custom_options_jp TEXT, 
                custom_options_kr TEXT,
                print_category VARCHAR(20) DEFAULT 'Noodle',
                category_en VARCHAR(50), 
                category_jp VARCHAR(50), 
                category_kr VARCHAR(50)
            );
        ''')
        
        # 2. 建立訂單表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY, 
                table_number VARCHAR(10), 
                items TEXT NOT NULL, 
                total_price INTEGER NOT NULL, 
                status VARCHAR(20) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                daily_seq INTEGER DEFAULT 0,
                content_json TEXT, 
                need_receipt BOOLEAN DEFAULT FALSE, 
                lang VARCHAR(10) DEFAULT 'zh',
                is_delivery BOOLEAN DEFAULT FALSE,
                customer_name TEXT,
                customer_phone TEXT,
                customer_address TEXT,
                scheduled_for TEXT,
                delivery_fee INTEGER DEFAULT 0
            );
        ''')
        
        # 3. 建立系統設定表
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);''')
        
        # 4. 插入預設設定 (Email 與 外送相關)
        default_settings = [
            # Email 相關
            ('report_email', ''), 
            ('resend_api_key', ''), 
            ('sender_email', 'onboarding@resend.dev'),
            
            # --- 新增：外送設定預設值 ---
            ('delivery_enabled', '1'),      # 外送開關 (1開 0關)
            ('delivery_min_price', '500'),  # 最低起送金額 (購物車滿多少錢)
            ('delivery_max_km', '5'),       # 最大外送距離 (公里)
            ('delivery_base_fee', '30'),    # 基礎運費
            ('delivery_fee_per_km', '10')   # 每公里加收費用
        ]
        
        for k, v in default_settings:
            # ON CONFLICT DO NOTHING 確保不會覆蓋使用者已修改的設定
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))

        # 5. 欄位安全性更新 (若已存在資料表則補上缺少的欄位)
        # 這裡包含舊系統升級到新系統時必要的 ALTER 指令
        alters = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            
            # --- 新增：外送功能所需的欄位 ---
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_delivery BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_phone TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_address TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_for TEXT;", # 儲存預約日期字串 (YYYY-MM-DD)
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee INTEGER DEFAULT 0;"
        ]
        
        for cmd in alters:
            try:
                cur.execute(cmd)
            except Exception as e:
                # 忽略欄位已存在的錯誤，但印出其他錯誤以便除錯
                if 'duplicate column' not in str(e):
                    print(f"⚠️ Warning during migration: {e}")

        print("✅ 資料庫初始化檢查完成 (含外送模組)")
        return True

    except Exception as e:
        print(f"❌ 資料庫初始化錯誤: {e}")
        return False
    
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
