import os  # 匯入作業系統模組，用於讀取環境變數
import psycopg2  # 匯入 PostgreSQL 資料庫驅動模組
from urllib.parse import urlparse  # 匯入網址解析工具

# --- 資料庫基礎連線 --- 
def get_db_connection():
    """建立並回傳資料庫連線物件"""
    # 從作業系統環境變數中取得 DATABASE_URL（包含資料庫主機、帳密等資訊）
    db_uri = os.environ.get("DATABASE_URL")
    if not db_uri:
        # 如果找不到連線資訊，拋出錯誤訊息
        raise ValueError("錯誤：找不到環境變數 DATABASE_URL")
    # 使用 psycopg2 套件建立與 PostgreSQL 的連線
    return psycopg2.connect(db_uri)

# --- 資料庫初始化 ---
def init_db():
    """
    建立所有必要的資料表與預設設定 (含多店鋪支援)。
    回傳 True 表示成功，False 表示失敗。
    """
    conn = None # 預設連線變數為空
    cur = None  # 預設遊標（Cursor）變數為空
    try:
        conn = get_db_connection() # 取得資料庫連線
        conn.autocommit = True     # 設定為「自動提交」，每執行一個 SQL 指令即生效
        cur = conn.cursor()        # 開啟遊標以執行 SQL 指令

        # ==========================================
        # 1. 核心架構：建立店鋪表 (stores) [新增]
        # ==========================================
        cur.execute('''
            CREATE TABLE IF NOT EXISTS stores (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                address VARCHAR(255),
                phone VARCHAR(20),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # 確保至少有一家店 (預設總店 ID=1)
        cur.execute("INSERT INTO stores (id, name) VALUES (1, '預設總店') ON CONFLICT (id) DO NOTHING;")

        # ==========================================
        # 2. 核心架構：建立使用者表 (users) [新增]
        # ==========================================
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                store_id INTEGER DEFAULT 1,  -- 綁定店鋪
                role VARCHAR(20) DEFAULT 'admin', -- admin, staff, super_admin
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')

        # ==========================================
        # 3. 建立產品表 (products) - 加入 store_id
        # ==========================================
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,            -- 自動遞增的主鍵 ID
                store_id INTEGER DEFAULT 1,       -- [新增] 所屬店鋪 ID
                name VARCHAR(100) NOT NULL,       -- 產品名稱（必填）
                price INTEGER NOT NULL,           -- 價格（必填）
                category VARCHAR(50),             -- 分類名稱
                image_url TEXT,                   -- 圖片網址
                is_available BOOLEAN DEFAULT TRUE,-- 是否上架（預設為是）
                custom_options TEXT,              -- 自定義選項（如：辣度、冰塊）
                sort_order INTEGER DEFAULT 100,   -- 排序序號
                
                -- 多語系欄位
                name_en VARCHAR(100),             -- 英文品名
                name_jp VARCHAR(100),             -- 日文品名
                name_kr VARCHAR(100),             -- 韓文品名
                custom_options_en TEXT,           -- 英文自定義選項
                custom_options_jp TEXT,           -- 日文自定義選項
                custom_options_kr TEXT,           -- 韓文自定義選項
                
                print_category VARCHAR(20) DEFAULT 'Noodle', -- 出單分類（用於廚房出單）
                category_en VARCHAR(50),          -- 英文分類名
                category_jp VARCHAR(50),          -- 日文分類名
                category_kr VARCHAR(50)           -- 韓文分類名
            );
        ''')
        
        # ==========================================
        # 4. 建立訂單表 (orders) - 加入 store_id
        # ==========================================
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,            -- 訂單 ID
                store_id INTEGER DEFAULT 1,       -- [新增] 所屬店鋪 ID
                table_number VARCHAR(10),         -- 桌號
                items TEXT NOT NULL,              -- 訂單項目內容（文字描述）
                total_price INTEGER NOT NULL,     -- 總金額
                status VARCHAR(20) DEFAULT 'Pending', -- 訂單狀態（預設為待處理）
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 建立時間
                daily_seq INTEGER DEFAULT 0,      -- 當日流水號 (需搭配 store_id 計算)
                content_json TEXT,                -- 以 JSON 格式存儲的訂單明細
                need_receipt BOOLEAN DEFAULT FALSE, -- 是否需要收據/統編
                lang VARCHAR(10) DEFAULT 'zh',    -- 下單時使用的語系
                
                -- 外送相關欄位
                order_type VARCHAR(50) DEFAULT 'dine_in', -- 訂單類型（內用/外送/自取）
                delivery_info TEXT,                -- 綜合外送資訊
                customer_name TEXT,                -- 客戶姓名
                customer_phone TEXT,               -- 客戶電話
                customer_address TEXT,             -- 客戶地址
                scheduled_for TEXT,                -- 預約送達時間
                delivery_fee INTEGER DEFAULT 0    -- 外送費
            );
        ''')
        
        # ==========================================
        # 5. 建立系統設定表 (settings) - 加入 store_id
        # ==========================================
        cur.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT, 
                value TEXT, 
                store_id INTEGER DEFAULT 1
            );
        ''')
        
        # 6. 插入預設設定 (針對 Store 1)
        default_settings = [
            ('sender_email', 'onboarding@resend.dev'),
            ('shop_open', '1'),                        
            ('delivery_enabled', '1'),                 
            ('enable_delivery', '1'),                  
            ('delivery_min_price', '500'),             
            ('delivery_fee_base', '0'),                
            ('delivery_max_km', '5'),                  
            ('delivery_fee_per_km', '10')              
        ]
        
        for k, v in default_settings:
            # 簡單檢查：如果該店沒有這個設定才插入
            cur.execute("""
                INSERT INTO settings (key, value, store_id) 
                SELECT %s, %s, 1 
                WHERE NOT EXISTS (SELECT 1 FROM settings WHERE key=%s AND store_id=1)
            """, (k, v, k))

        # ==========================================
        # 7. 【關鍵】欄位自動補全 (Migration)
        # ==========================================
        alters = [
            # --- 多店鋪欄位補全 (Store ID) ---
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS store_id INTEGER DEFAULT 1;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS store_id INTEGER DEFAULT 1;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS store_id INTEGER DEFAULT 1;",
            "ALTER TABLE settings ADD COLUMN IF NOT EXISTS store_id INTEGER DEFAULT 1;",

            # --- Orders 表格補全 ---
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_type VARCHAR(50) DEFAULT 'dine_in';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_info TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_phone TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_address TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_for TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee INTEGER DEFAULT 0;",
            
            # --- Products 表格補全 (防止舊資料庫缺少多語系欄位) ---
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 100;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS print_category VARCHAR(20) DEFAULT 'Noodle';",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_en VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_jp VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_kr VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS category_en VARCHAR(50);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS category_jp VARCHAR(50);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS category_kr VARCHAR(50);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_en TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_jp TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_kr TEXT;"
        ]
        
        print("🔄 正在檢查資料庫欄位結構...")
        for cmd in alters:
            try:
                cur.execute(cmd) # 執行增加欄位的指令
            except Exception as e:
                # 攔截錯誤，忽略「重複欄位」或「已存在」的報錯
                if 'duplicate' not in str(e).lower() and 'exists' not in str(e).lower():
                    print(f"⚠️ Warning during migration: {e}")

        # 建立 store_id 索引以優化查詢速度 (如果索引已存在會報錯，所以用 try 包起來)
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_orders_store_id ON orders(store_id);",
            "CREATE INDEX IF NOT EXISTS idx_products_store_id ON products(store_id);"
        ]
        for idx in indices:
            try:
                cur.execute(idx)
            except Exception:
                pass

        print("✅ 資料庫初始化檢查完成 (已啟用多店鋪架構)")
        return True

    except Exception as e:
        # 捕獲初始化過程中的任何重大錯誤
        print(f"❌ 資料庫初始化錯誤: {e}")
        return False
    
    finally:
        # 無論成功或失敗，最後都必須關閉遊標與連線，釋放資源
        if cur:
            cur.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    # 當直接執行此 .py 檔案時，啟動初始化程序
    init_db()
