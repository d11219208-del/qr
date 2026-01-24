from flask import Flask
from database import init_db
from routes import menu_bp, kitchen_bp, admin_bp
from utils import start_background_tasks

def create_app():
    app = Flask(__name__)

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

    # 3. 啟動背景任務 (排程發信、防休眠 Ping)
    start_background_tasks()

    return app

app = create_app()

if __name__ == '__main__':
    # 這裡的設定適合 Render 部署與本地測試
    # port 10000 是 Render 的預設連接埠
    app.run(host='0.0.0.0', port=10000, debug=False)