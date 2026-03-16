# ecpay_invoice.py
import os
import urllib.parse
import hashlib
import requests
import time
import json
import re
from dotenv import load_dotenv

load_dotenv()

# 讀取環境變數 (加上測試環境的預設值防呆)
MERCHANT_ID = os.environ.get("ECPAY_INVOICE_MERCHANT_ID", "2000132")
HASH_KEY = os.environ.get("ECPAY_INVOICE_HASH_KEY", "ejCk326UnaZWKisg")
HASH_IV = os.environ.get("ECPAY_INVOICE_HASH_IV", "q9jcZX8Ib9LM8wYk")

# 開立發票 URL
ISSUE_URL = os.environ.get("ECPAY_INVOICE_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Issue")
# 作廢發票 URL
INVALID_URL = os.environ.get("ECPAY_INVOICE_INVALID_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Invalid")

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
    
    # 5. MD5 雜湊並轉大寫 (註：綠界發票通常使用 MD5，若文件要求 SHA256 可改為 hashlib.sha256)
    mac_value = hashlib.md5(encoded_string.encode('utf-8')).hexdigest().upper()
    return mac_value


def issue_ecpay_invoice(order):
    """
    發送開立發票請求給綠界 (供 kitchen_routes 呼叫)
    order: 從資料庫撈出的訂單字典 (dict)
    """
    # 取得訂單資訊
    order_id = order.get('id', '')
    amount = order.get('total_amount', 0)
    customer_phone = order.get('phone', '')
    tax_id = order.get('tax_id', '') or ''
    carrier_type = order.get('carrier_type', '') or ''
    carrier_num = order.get('carrier_num', '') or ''

    is_company = bool(tax_id and len(str(tax_id)) == 8)

    # 基礎參數組合 (使用統一品項 "餐飲費用" 避免明細字串過長出錯)
    params = {
        "MerchantID": MERCHANT_ID,
        "RelateNumber": f"ORDER{order_id}T{int(time.time())}", # 確保編號唯一
        "CustomerID": "",
        "CustomerIdentifier": tax_id if is_company else "",
        "CustomerName": "門市顧客",
        "CustomerAddr": "台北市內湖區", # 綠界必填預設地址
        "CustomerPhone": customer_phone,
        "CustomerEmail": "",
        "ClearanceMark": "",
        "Print": "1" if is_company else "0",
        "Donation": "0",
        "LoveCode": "",
        "CarruerType": carrier_type,
        "CarruerNum": carrier_num,
        "TaxType": "1", # 1: 應稅
        "SalesAmount": str(amount),
        "ItemName": "餐飲費用",
        "ItemCount": "1",
        "ItemWord": "式",
        "ItemPrice": str(amount),
        "ItemTaxType": "1",
        "ItemAmount": str(amount),
        "InvType": "07", # 07: 一般稅額
        "DelayFlag": "0",
        "TimeStamp": str(int(time.time()))
    }

    # 如果有載具，強制不列印
    if carrier_type:
        params["Print"] = "0"

    # 產生檢查碼並加入參數
    params["CheckMacValue"] = generate_check_mac_value(params)

    try:
        # 發送 POST 請求
        response = requests.post(ISSUE_URL, data=params)
        result_text = response.text
        
        # 解析回傳結果
        if "RtnCode" in result_text or "InvoiceNo" in result_text:
            # 嘗試抓取 InvoiceNumber (支援 JSON 或 URL-encoded 格式)
            match = re.search(r'(?:InvoiceNo(?:umber)?(?:\"|\:|\=)?\s*\"?)([A-Z]{2}\d{8})', result_text)
            
            # 若 RtnCode 為 1 (成功) 且有抓到發票號碼
            if ('"RtnCode":1' in result_text or 'RtnCode=1' in result_text) and match:
                return {"success": True, "invoice_no": match.group(1), "message": "OK"}
                
        # 失敗狀態
        return {"success": False, "message": result_text}

    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}


def invalid_ecpay_invoice(invoice_no, reason="訂單取消"):
    """
    發送作廢發票請求給綠界 (供 kitchen_routes 呼叫)
    """
    params = {
        "MerchantID": MERCHANT_ID,
        "InvoiceNumber": invoice_no,
        "Reason": reason[:20] # 綠界規定作廢原因長度不能超過 20 字
    }
    
    params["CheckMacValue"] = generate_check_mac_value(params)
    
    try:
        response = requests.post(INVALID_URL, data=params)
        result_text = response.text
        
        # 判斷回傳的 RtnCode 是否為 1 (成功)
        if '"RtnCode":1' in result_text or 'RtnCode=1' in result_text:
            return {"success": True, "message": "作廢成功"}
        else:
            return {"success": False, "message": result_text}
            
    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}
