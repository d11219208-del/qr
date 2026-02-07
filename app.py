import os
from flask import Flask
from database import init_db
# 注意：下一步我們會將 delivery_bp 加入 routes.py，這裡先引用
from routes import menu_bp, kitchen_bp, admin_bp, delivery_bp 
from utils import start_background_tasks

def create_app():
    app = Flask(__name__)

    # --- 關鍵修改 1：設定 Secret Key (Session 必要) ---
    # 如果環境變數沒設定，就使用後面的預設字串 (開發用)
    # 在正式上線環境 (Render) 建議在環境變數設定 SECRET_KEY
    app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key_change_this_123")

    # 1. 初始化資料庫 (確保啟動時資料表都已建立)
    with app.app_context():
        init_db()

    # 2. 註冊路由藍圖 (Blueprints)
    # 前台點餐 (根目錄 /)
    app.register_blueprint(menu_bp)
    
    # 廚房看板 (路徑 /kitchen)
    app.register_blueprint(kitchen_bp, url_prefix='/kitchen')
    
    # 後台管理 (路徑 /admin)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # --- 關鍵修改 2：註冊外送藍圖 ---
    # 這會處理 /delivery/setup, /delivery/check_address 等請求
    app.register_blueprint(delivery_bp, url_prefix='/delivery')

    # 3. 啟動背景任務 (排程發信、防休眠 Ping)
    start_background_tasks(app)

    return app

app = create_app()

if __name__ == '__main__':
    # 這裡的設定適合 Render 部署與本地測試
    app.run(host='0.0.0.0', port=10000, debug=False)
