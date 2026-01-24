import os
import psycopg2

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
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
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
                lang VARCHAR(10) DEFAULT 'zh'
            );
        ''')
        
        # 3. 建立系統設定表
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);''')
        
        # 4. 插入預設設定 (Email 報表相關)
        default_settings = [
            ('report_email', ''), 
            ('resend_api_key', ''), 
            ('sender_email', 'onboarding@resend.dev')
        ]
        for k, v in default_settings:
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))

        # 5. 欄位安全性更新 (若已存在資料表則補上缺少的欄位)
        alters = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;"
        ]
        for cmd in alters:
            try:
                cur.execute(cmd)
            except Exception:
                pass # 忽略已存在欄位的錯誤

        print("✅ 資料庫初始化檢查完成")
        return True
    except Exception as e:
        print(f"❌ 資料庫初始化錯誤: {e}")
        return False
    finally:
        cur.close()
        conn.close()