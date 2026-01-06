import os
import psycopg2
from flask import Flask, jsonify

app = Flask(__name__)

def get_db_connection():
    db_uri = os.environ.get("DATABASE_URL")
    return psycopg2.connect(db_uri)

@app.route('/')
def index():
    """首頁：顯示目前 users 表格內的所有資料"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 查詢所有使用者
        cur.execute('SELECT * FROM users;')
        users = cur.fetchall()
        
        cur.close()
        
        # 將資料轉成文字顯示在網頁上
        if not users:
            return "目前資料庫連線正常，但在 'users' 表格中沒有資料。<br>請嘗試訪問 <a href='/add_user'>/add_user</a> 來新增資料。"
            
        result = "<h1>使用者列表：</h1><ul>"
        for user in users:
            result += f"<li>ID: {user[0]}, Name: {user[1]}, Email: {user[2]}</li>"
        result += "</ul>"
        return result

    except psycopg2.errors.UndefinedTable:
        return "錯誤：找不到 'users' 表格。<br>請先訪問 <a href='/create_table'>/create_table</a> 來建立表格。"
    except Exception as e:
        return f"讀取失敗：{str(e)}"
    finally:
        if conn: conn.close()

@app.route('/create_table')
def create_table():
    """建立測試用的 users 表格"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 建立表格 SQL 指令
        create_table_query = '''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL
        );
        '''
        cur.execute(create_table_query)
        conn.commit() # 記得 commit 才會儲存變更
        cur.close()
        return "成功！'users' 表格已建立。<br><a href='/'>回首頁</a>"
    except Exception as e:
        return f"建立表格失敗：{str(e)}"
    finally:
        if conn: conn.close()

@app.route('/add_user')
def add_user():
    """新增一筆隨機資料"""
    import random
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 產生隨機名稱以避免重複
        rand_num = random.randint(1, 1000)
        name = f"User_{rand_num}"
        email = f"user{rand_num}@example.com"
        
        # 插入資料 SQL 指令
        insert_query = "INSERT INTO users (name, email) VALUES (%s, %s)"
        cur.execute(insert_query, (name, email))
        
        conn.commit()
        cur.close()
        return f"成功！已新增使用者：{name} ({email})<br><a href='/'>回首頁查看</a>"
    except Exception as e:
        return f"新增資料失敗：{str(e)}"
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
