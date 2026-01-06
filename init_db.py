import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def init_db():
    url = os.getenv("DATABASE_URL")
    # 連線到資料庫
    conn = psycopg2.connect(url)
    cur = conn.cursor()

    print("正在建立資料庫表格...")

    # 1. 建立菜單表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS menu (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            price INT NOT NULL,
            category VARCHAR(50),
            image_url TEXT,
            is_available BOOLEAN DEFAULT TRUE
        );
    """)

    # 2. 建立訂單表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            table_number VARCHAR(20),
            order_type VARCHAR(20), -- 'DINE_IN' or 'TAKEOUT'
            total_amount INT DEFAULT 0,
            note TEXT,
            status VARCHAR(20) DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 3. 建立訂單明細表 (JSONB 格式比較適合簡單系統，這裡用關聯表)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id SERIAL PRIMARY KEY,
            order_id INT REFERENCES orders(id),
            menu_name VARCHAR(100), -- 直接存名稱，方便歷史查詢
            quantity INT NOT NULL,
            price INT NOT NULL
        );
    """)

    # 4. 插入測試菜單
    cur.execute("SELECT count(*) FROM menu;")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO menu (name, price, category, image_url) VALUES 
            ('招牌牛肉麵', 180, '麵食', 'https://placehold.co/200x200?text=Beef+Noodle'),
            ('鮮蝦水餃', 100, '麵食', 'https://placehold.co/200x200?text=Dumplings'),
            ('燙青菜', 40, '小菜', 'https://placehold.co/200x200?text=Vegetables');
        """)
        print("已插入測試菜單數據")

    conn.commit()
    cur.close()
    conn.close()
    print("資料庫初始化成功！")

if __name__ == "__main__":
    init_db()
