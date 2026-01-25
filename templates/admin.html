<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>餐廳後台管理系統</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.14.0/Sortable.min.js"></script>
    <style>
        :root { --primary: #9b4dca; --bg: #f4f5f7; }
        body { background: var(--bg); padding-bottom: 50px; font-family: "PingFang TC", "Microsoft JhengHei", sans-serif; }
        .container { max-width: 1100px; margin: 0 auto; padding: 20px; }
        .card { background: #fff; border-radius: 12px; padding: 25px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); margin-bottom: 25px; }
        
        /* 置頂搜尋欄位 */
        .sticky-header {
            position: sticky;
            top: 0;
            z-index: 1000;
            background: var(--bg);
            padding: 10px 0;
            margin-bottom: 10px;
        }
        .sticky-header input { 
            background: #fff !important; 
            box-shadow: 0 4px 10px rgba(0,0,0,0.1); 
            border: 2px solid var(--primary);
            height: 4.5rem;
            font-size: 1.6rem;
        }

        /* 狀態與按鈕 */
        .btn-sm { padding: 0 10px; height: 28px; line-height: 26px; font-size: 1.2rem; }
        .status-on { background: #e6ffed; color: #28a745; border-color: #b7eb8f; }
        .status-off { background: #fff1f0; color: #f5222d; border-color: #ffa39e; }
        .alert { padding: 12px; background: #e6f7ff; color: #0050b3; border-radius: 6px; margin-bottom: 20px; border-left: 5px solid #1890ff; }
        
        /* 表格與標籤 */
        .handle { cursor: grab; color: #ccc; font-size: 20px; padding: 0 10px; }
        .lang-tag { font-size: 0.7em; background: #eee; padding: 2px 4px; border-radius: 3px; margin-right: 2px; color: #666; font-weight: bold; }
        .prod-info { font-size: 0.85em; color: #777; margin-top: 4px; }
        .prod-name { font-size: 1.1em; font-weight: bold; color: #333; }
        
        @media (max-width: 600px) {
            table, thead, tbody, th, td, tr { display: block; }
            thead tr { position: absolute; top: -9999px; }
            tr { margin-bottom: 15px; padding: 10px; border: 1px solid #eee; border-radius: 8px; background: #fff; }
            td { border: none; padding: 5px 0; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <h2>🍴 餐廳系統後台</h2>
            <a href="/kitchen" class="button button-outline">👨‍🍳 廚房看板</a>
        </div>

        {% if msg %}
        <div id="status-msg" class="alert" style="display:block;">{{ msg }}</div>
        {% endif %}

        <div class="card">
            <h4>⚙️ 系統與通知設定</h4>
            <form method="POST">
                <div class="row">
                    <div class="column"><label>收件 Email</label><input type="email" name="report_email" value="{{ config.get('report_email','') }}" placeholder="接收日結單"></div>
                    <div class="column"><label>發件人 Email</label><input type="email" name="sender_email" value="{{ config.get('sender_email','') }}" placeholder="需與 Resend 驗證網域一致"></div>
                    <div class="column"><label>Resend API Key</label><input type="password" name="resend_api_key" value="{{ config.get('resend_api_key','') }}" placeholder="re_..."></div>
                </div>
                <button type="submit" name="action" value="save_settings">💾 儲存設定</button>
                <button type="submit" name="action" value="test_email" class="button button-outline">🧪 測試連線</button>
                <button type="submit" name="action" value="send_report_now" class="button button-outline" style="float: right;">📊 立即補發今日報表</button>
            </form>
        </div>

        <div class="card">
            <h4>➕ 新增單一品項</h4>
            <form method="POST">
                <input type="hidden" name="action" value="add_product">
                <div class="row">
                    <div class="column column-40"><label>品名 (中)</label><input type="text" name="name" required></div>
                    <div class="column"><label>價格</label><input type="number" name="price" required></div>
                    <div class="column"><label>出單區域</label>
                        <select name="print_category">
                            <option value="Noodle">🍜 麵區</option>
                            <option value="Soup">🍲 湯區</option>
                        </select>
                    </div>
                </div>
                <div class="row">
                    <div class="column"><label>分類 (中)</label><input type="text" name="category" placeholder="主食"></div>
                    <div class="column"><label>圖片 URL</label><input type="text" name="image_url"></div>
                </div>
                <details>
                    <summary style="cursor: pointer; color: var(--primary); margin-bottom: 10px;">🌐 展開多國語言與客製化選項設定</summary>
                    <div class="row">
                        <div class="column"><input type="text" name="name_en" placeholder="Name (EN)"></div>
                        <div class="column"><input type="text" name="name_jp" placeholder="名稱 (JP)"></div>
                        <div class="column"><input type="text" name="name_kr" placeholder="名稱 (KR)"></div>
                    </div>
                    <div class="row">
                        <div class="column"><input type="text" name="category_en" placeholder="Cat (EN)"></div>
                        <div class="column"><input type="text" name="category_jp" placeholder="Cat (JP)"></div>
                        <div class="column"><input type="text" name="category_kr" placeholder="Cat (KR)"></div>
                    </div>
                    <label>客製化選項 (中文，逗號隔開)</label>
                    <input type="text" name="custom_options" placeholder="例：加麵,去蔥,不加辣">
                    <div class="row">
                        <div class="column"><input type="text" name="custom_options_en" placeholder="Options (EN)"></div>
                        <div class="column"><input type="text" name="custom_options_jp" placeholder="Options (JP)"></div>
                        <div class="column"><input type="text" name="custom_options_kr" placeholder="Options (KR)"></div>
                    </div>
                </details>
                <button type="submit" class="button btn-full">🚀 提交新增</button>
            </form>
        </div>

        <div class="sticky-header">
            <input type="text" id="productSearch" placeholder="🔍 搜尋品項名稱、分類或翻譯內容...">
        </div>

        <div class="card">
            <h4>📋 菜單管理</h4>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th width="40">排序</th>
                            <th>品項與多語翻譯</th>
                            <th width="80">價格</th>
                            <th width="100">狀態</th>
                            <th width="120">操作</th>
                        </tr>
                    </thead>
                    <tbody id="menu-list">
                        {% for p in prods %}
                        <tr data-id="{{ p[0] }}" class="product-row">
                            <td class="handle">☰</td>
                            <td>
                                <div class="prod-name">{{ p[1] }} {% if p[7] %}🖼️{% endif %}</div>
                                <div class="prod-info">
                                    <span class="lang-tag">EN</span>{{ p[8] or '-' }} | 
                                    <span class="lang-tag">JP</span>{{ p[9] or '-' }} | 
                                    <span class="lang-tag">KR</span>{{ p[10] or '-' }}
                                </div>
                                <div class="prod-info">分類：{{ p[3] }} | 區域：{{ p[5] }}</div>
                            </td>
                            <td><b>${{ p[2] }}</b></td>
                            <td>
                                <button onclick="toggleProduct({{ p[0] }}, this)" 
                                        class="btn-sm {{ 'status-on' if p[4] else 'status-off' }}">
                                    {{ '上架中' if p[4] else '已下架' }}
                                </button>
                            </td>
                            <td>
                                <a href="/admin/edit_product/{{ p[0] }}" class="button button-clear">✎ 編輯</a>
                                <a href="/admin/delete_product/{{ p[0] }}" class="button button-clear" style="color:red;" onclick="return confirm('確定刪除?')">✖</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            
            <hr>
            <div class="row">
                <div class="column">
                    <a href="/admin/export_menu" class="button button-outline">📤 匯出 Excel</a>
                    <form action="/admin/import_menu" method="POST" enctype="multipart/form-data" style="display:inline;">
                        <input type="file" name="menu_file" accept=".xlsx" required style="display:none;" id="fileInput" onchange="this.form.submit()">
                        <button type="button" class="button button-outline" onclick="document.getElementById('fileInput').click()">📥 匯入 Excel</button>
                    </form>
                </div>
                <div class="column" style="text-align: right;">
                    <a href="/admin/reset_menu" class="button" style="background:#ff4d4f; border-color:#ff4d4f;" onclick="return confirm('⚠️ 將清空所有產品且無法復原，確定？')">🗑️ 清空菜單</a>
                    <a href="/admin/reset_orders" class="button" style="background:#ff4d4f; border-color:#ff4d4f;" onclick="return confirm('⚠️ 將清空所有歷史訂單，確定？')">💥 清空訂單</a>
                </div>
            </div>
        </div>
    </div>

    <script>
        // 訊息自動隱藏
        const msg = document.getElementById('status-msg');
        if(msg && msg.innerText.trim() !== "") {
            setTimeout(() => { msg.style.display = 'none'; }, 3000);
        }

        // 上下架切換
        function toggleProduct(pid, btn) {
            fetch('/admin/toggle_product/' + pid, { method: 'POST' })
            .then(r => r.json()).then(d => {
                if(d.status === 'success') {
                    btn.className = d.is_available ? 'btn-sm status-on' : 'btn-sm status-off';
                    btn.innerText = d.is_available ? '上架中' : '已下架';
                }
            });
        }

        // 搜尋功能
        document.getElementById('productSearch').addEventListener('input', e => {
            let v = e.target.value.toLowerCase();
            document.querySelectorAll('.product-row').forEach(r => {
                r.style.display = r.innerText.toLowerCase().includes(v) ? '' : 'none';
            });
        });

        // 拖曳排序
        Sortable.create(document.getElementById('menu-list'), {
            handle: '.handle',
            animation: 150,
            onEnd: function() {
                let order = Array.from(document.querySelectorAll('#menu-list tr')).map(r => r.getAttribute('data-id'));
                fetch('/admin/reorder_products', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order: order })
                });
            }
        });
    </script>
</body>
</html>
