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

        # 1. 建立產品表
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
        # 注意：這裡已經加入了 order_type 和 delivery_info
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
                
                -- 外送相關欄位
                order_type VARCHAR(50) DEFAULT 'dine_in',
                delivery_info TEXT,
                customer_name TEXT,
                customer_phone TEXT,
                customer_address TEXT,
                scheduled_for TEXT,
                delivery_fee INTEGER DEFAULT 0
            );
        ''')
        
        # 3. 建立系統設定表
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);''')
        
        # 4. 插入預設設定
        default_settings = [
            ('sender_email', 'onboarding@resend.dev'),
            ('delivery_enabled', '1'),
            ('delivery_min_price', '500'),
            ('delivery_fee_base', '60')
        ]
        
        for k, v in default_settings:
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))

        # 5. 【關鍵】欄位自動補全 (Migration)
        # 這裡會檢查現有的 orders 表，如果缺少欄位會自動補上，解決 "column does not exist" 錯誤
        alters = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            
            # 補上缺少的欄位
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_type VARCHAR(50) DEFAULT 'dine_in';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_info TEXT;",
            
            # 外送詳細欄位
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_phone TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_address TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_for TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee INTEGER DEFAULT 0;"
        ]
        
        print("🔄 正在檢查資料庫欄位結構...")
        for cmd in alters:
            try:
                cur.execute(cmd)
            except Exception as e:
                # 忽略 "duplicate column" 錯誤，其他錯誤則印出
                if 'duplicate' not in str(e).lower() and 'exists' not in str(e).lower():
                    print(f"⚠️ Warning during migration: {e}")

        print("✅ 資料庫初始化檢查完成 (含 order_type 與 delivery_info)")
        return True

    except Exception as e:
        print(f"❌ 資料庫初始化錯誤: {e}")
        return False
    
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    # 讓這個檔案可以直接被執行以初始化資料庫
    init_db()
