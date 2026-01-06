import os
import psycopg2
from flask import Flask

app = Flask(__name__)

def get_db_version():
    # 從 Render 的環境變數讀取連線字串
    db_uri = os.environ.get("DATABASE_URL")

    if not db_uri:
        return "錯誤：找不到 DATABASE_URL 環境變數"

    conn = None
    try:
        # 建立連線 (Render 內部通常不需要強制 SSL 設定，但在外部需要)
        conn = psycopg2.connect(db_uri)
        cur = conn.cursor()
        cur.execute('SELECT version()')
        db_version = cur.fetchone()
        cur.close()
        return f"連線成功！資料庫版本為：{db_version[0]}"
    except Exception as e:
        return f"連線失敗：{str(e)}"
    finally:
        if conn is not None:
            conn.close()

@app.route('/')
def index():
    return get_db_version()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
