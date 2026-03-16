# ecpay_invoice.py
import os
import urllib.parse
import hashlib
import requests
import time
from datetime import datetime

# 取得環境變數
MERCHANT_ID = os.environ.get("ECPAY_MERCHANT_ID")
HASH_KEY = os.environ.get("ECPAY_HASH_KEY")
HASH_IV = os.environ.get("ECPAY_HASH_IV")
INVOICE_URL = os.environ.get("ECPAY_INVOICE_URL")

def generate_check_mac_value(params: dict) -> str:
    """計算綠界的 CheckMacValue (加密檢查碼)"""
    # 1. 參數依照字母 a-z 排序
    sorted_params = sorted(params.items())
    
    # 2. 組合字串 (HashKey + 參數 + HashIV)
    query_str = f"HashKey={HASH_KEY}&" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&HashIV={HASH_IV}"
    
    # 3. URL Encode (綠界專用的轉換規則)
    url_encoded = urllib.parse.quote_plus(query_str).lower()
    url_encoded = url_encoded.replace('%2d', '-').replace('%5f', '_').replace('%2e', '.').replace('%21', '!')
    url_encoded = url_encoded.replace('%2a', '*').replace('%28', '(').replace('%29', ')')
    
    # 4. MD5 加密並轉大寫 (電子發票 API 主要是用 MD5)
    return hashlib.md5(url_encoded.encode('utf-8')).hexdigest().upper()

def issue_invoice(order_id: str, amount: int, items: list, customer_email: str):
    """
    開立發票的主要函式
    items 格式範例: [{"name": "牛肉麵", "count": 1, "price": 150}]
    """
    # 組合商品字串 (綠界格式: 商品名稱1|商品名稱2)
    item_names = "|".join([item['name'] for item in items])
    item_counts = "|".join([str(item['count']) for item in items])
    item_words = "|".join(["份"] * len(items)) # 單位
    item_prices = "|".join([str(item['price']) for item in items])
    item_amounts = "|".join([str(item['price'] * item['count']) for item in items])

    # 基礎必填參數
    params = {
        "MerchantID": MERCHANT_ID,
        "RelateNumber": f"{order_id}INV{int(time.time())}", # 訂單編號 (不可重複)
        "CustomerID": "", 
        "CustomerIdentifier": "", # 統編 (若有需要可由前端傳入)
        "CustomerName": "線上點餐顧客", 
        "CustomerAddr": "", 
        "CustomerPhone": "", 
        "CustomerEmail": customer_email, # 用於寄送發票開立通知
        "ClearanceMark": "", 
        "Print": "0", # 0=不列印
        "Donation": "0", # 0=不捐贈
        "LoveCode": "", 
        "CarruerType": "", # 載具類別 (若是手機條碼則填 3)
        "CarruerNum": "", # 載具編號
        "TaxType": "1", # 1=應稅
        "SalesAmount": str(amount), 
        "InvoiceRemark": "線上點餐系統開立", 
        "ItemName": item_names,
        "ItemCount": item_counts,
        "ItemWord": item_words,
        "ItemPrice": item_prices,
        "ItemTaxType": "|".join(["1"] * len(items)),
        "ItemAmount": item_amounts,
        "InvType": "07" # 07=一般稅額
    }

    # 加入加密檢查碼
    params["CheckMacValue"] = generate_check_mac_value(params)

    # 發送 API 請求給綠界
    response = requests.post(INVOICE_URL, data=params)
    
    # 解析回傳結果
    # 綠界成功時會回傳類似: 1|發票號碼... 或 1|OK...
    if response.status_code == 200 and response.text.startswith("1|"):
        print("發票開立成功:", response.text)
        return {"success": True, "message": response.text}
    else:
        print("發票開立失敗:", response.text)
        return {"success": False, "message": response.text}
