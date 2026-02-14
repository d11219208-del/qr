from flask import Blueprint, render_template
from database import get_db_connection
import psycopg2

try_bp = Blueprint('try_debug', __name__)

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
        # 2. 針對每個表，抓取欄位詳細資訊 (欄位名, 類型, 是否可為空, 預設值)
        cur.execute(f"""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = '{table}'
            ORDER BY ordinal_position;
        """)
        columns_info = cur.fetchall()
        
        # 3. 抓取該表的前 3 筆資料 (用來預覽)
        # 注意：這裡使用 SQL 拼接是因為 table 名稱來自系統資訊，相對安全，
        # 但在一般應用中請避免將變數直接拼接到 SQL 字串。
        cur.execute(f"SELECT * FROM {table} LIMIT 3")
        sample_rows = cur.fetchall()
        
        db_info[table] = {
            'schema': columns_info,
            'data': sample_rows
        }

    cur.close()
    conn.close()
    
    return render_template('try.html', db_info=db_info)
