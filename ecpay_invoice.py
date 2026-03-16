# ecpay_invoice.py
import os
import urllib.parse
import hashlib
import requests
import time
from dotenv import load_dotenv

load_dotenv()

# 讀取環境變數
MERCHANT_ID = os.environ.get("ECPAY_INVOICE_MERCHANT_ID")
HASH_KEY = os.environ.get("ECPAY_INVOICE_HASH_KEY")
HASH_IV = os.environ.get("ECPAY_INVOICE_HASH_IV")
INVOICE_URL = os.environ.get("ECPAY_INVOICE_URL")

def generate_check_mac_value(params):
    """
    產生綠界電子發票需要的 CheckMacValue (MD5 加密)
    """
    # 1. 將參數依照字典字母順序排序
    sorted_params = sorted(params.items())
    
    # 2. 串接字串
    query_string = f"HashKey={HASH_KEY}&" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&HashIV={HASH_IV}"
    
    # 3. URL Encode
    encoded_string = urllib.parse.quote_plus(query_string, safe='')
    
    # 4. 轉小寫並處理綠界特殊的字元替換 (為了與 C# 的 UrlEncode 一致)
    encoded_string = encoded_string.lower()
    encoded_string = encoded_string.replace('%2d', '-').replace('%5f', '_').replace('%2e', '.').replace('%21', '!')
    encoded_string = encoded_string.replace('%2a', '*').replace('%28', '(').replace('%29', ')')
    
    # 5. MD5 雜湊並轉大寫
    mac_value = hashlib.md5(encoded_string.encode('utf-8')).hexdigest().upper()
    return mac_value

def issue_invoice(order_id, amount, items, customer_email="", customer_phone="", tax_id="", carrier_type="", carrier_num=""):
    """
    發送開立發票請求給綠界
    items 範例: [{"name": "排骨飯", "count": 1, "price": 100, "word": "個"}]
    """
    # 組合商品字串 (綠界格式: 項目1|項目2|項目3)
    item_names = "|".join([item["name"] for item in items])
    item_counts = "|".join([str(item["count"]) for item in items])
    item_words = "|".join([item["word"] for item in items])
    item_prices = "|".join([str(item["price"]) for item in items])
    item_amounts = "|".join([str(item["count"] * item["price"]) for item in items])

    # 基礎參數
    params = {
        "MerchantID": MERCHANT_ID,
        "RelateNumber": f"{order_id}{int(time.time())}", # 必須唯一，建議加上時間戳記
        "CustomerID": "",
        "CustomerIdentifier": tax_id, # 統編 (若有)
        "CustomerName": "客戶",
        "CustomerAddr": "台北市", # 預設地址
        "CustomerPhone": customer_phone,
        "CustomerEmail": customer_email,
        "ClearanceMark": "",
        "Print": "1" if tax_id else "0", # 有統編一定要印出 (1)，沒統編預設不印 (0)
        "Donation": "0",
        "LoveCode": "",
        "CarruerType": carrier_type, # 載具類別 (1: 綠界, 2: 自然人, 3: 手機條碼)
        "CarruerNum": carrier_num,
        "TaxType": "1", # 1: 應稅
        "SalesAmount": str(amount),
        "InvoiceItemName": item_names,
        "InvoiceItemCount": item_counts,
        "InvoiceItemWord": item_words,
        "InvoiceItemPrice": item_prices,
        "InvoiceItemTaxType": "1", # 每個品項的稅別 (應稅)
        "InvType": "07", # 07: 一般稅額
        "DelayFlag": "0", # 0: 立即開立
        "TimeStamp": str(int(time.time()))
    }

    # 依據有無載具調整 Print 參數
    if carrier_type:
        params["Print"] = "0"

    # 產生檢查碼並加入參數
    params["CheckMacValue"] = generate_check_mac_value(params)

    # 發送 POST 請求
    response = requests.post(INVOICE_URL, data=params)
    return response.text # 綠界會回傳 JSON 格式的字串，包含發票號碼
