# ecpay_invoice.py
import os
import time
import json
import urllib.parse
import requests
import base64 
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from dotenv import load_dotenv

load_dotenv()

# 讀取環境變數 (加上測試環境的預設值防呆)
MERCHANT_ID = os.environ.get("ECPAY_INVOICE_MERCHANT_ID", "2000132")
HASH_KEY = os.environ.get("ECPAY_INVOICE_HASH_KEY", "ejCk326UnaZWKisg")
HASH_IV = os.environ.get("ECPAY_INVOICE_HASH_IV", "q9jcZX8Ib9LM8wYk")

# 開立發票 URL (新版 B2C API 路徑)
ISSUE_URL = os.environ.get("ECPAY_INVOICE_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Issue")
# 作廢發票 URL (新版 B2C API 路徑)
INVALID_URL = os.environ.get("ECPAY_INVOICE_INVALID_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Invalid")

def aes_encrypt(data_dict, key, iv):
    """
    綠界新版電子發票專用的 AES 加密
    1. 將 Dict 轉為 JSON 字串
    2. 進行 URL Encode (綠界特殊規則)
    3. 進行 AES CBC 加密
    4. 轉為 Base64
    """
    # 1. 轉 JSON
    json_str = json.dumps(data_dict, ensure_ascii=False, separators=(',', ':'))
    
    # 2. URL Encode (仿照 C# HttpUtility.UrlEncode)
    url_encoded = urllib.parse.quote(json_str, safe='')
    
    # 3. AES 加密 (CBC 模式，PKCS7 Padding)
    cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
    padded_data = pad(url_encoded.encode('utf-8'), AES.block_size)
    encrypted_bytes = cipher.encrypt(padded_data)
    
    # 4. 轉 Base64
    encrypted_base64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    return encrypted_base64

def aes_decrypt(encrypted_base64, key, iv):
    """
    綠界新版電子發票專用的 AES 解密 (用來讀取回傳的發票號碼)
    """
    try:
        # 1. Base64 解碼
        encrypted_bytes = base64.b64decode(encrypted_base64)
        
        # 2. AES 解密
        cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        
        # 3. URL Decode 並轉為 JSON
        url_encoded_str = decrypted_bytes.decode('utf-8')
        json_str = urllib.parse.unquote(url_encoded_str)
        return json.loads(json_str)
    except Exception as e:
        print(f"AES Decrypt Error: {e}")
        return {}

def issue_ecpay_invoice(order):
    """
    發送開立發票請求給綠界 (供 kitchen_routes 呼叫)
    order: 從資料庫撈出的訂單字典 (dict)
    """
    # 🟢 修正：對齊資料庫中正確的欄位名稱
    order_id = order.get('id', '')
    amount = int(order.get('total_price', 0))        # 修正：total_amount -> total_price
    customer_phone = order.get('customer_phone', '') # 修正：phone -> customer_phone
    customer_name = order.get('customer_name', '') or "門市顧客"
    customer_address = order.get('customer_address', '') or "台北市內湖區"
    
    tax_id = order.get('tax_id', '') or ''
    carrier_type = order.get('carrier_type', '') or ''
    carrier_num = order.get('carrier_num', '') or ''

    is_company = bool(tax_id and len(str(tax_id)) == 8)

    # 如果有載具，強制不列印；如果是公司戶(有統編)，強制列印
    print_flag = "0"
    if is_company:
        print_flag = "1"
    if carrier_type:
        print_flag = "0"

    # 1. 準備 Data 裡面的資料 (這是要被加密的內容)
    data = {
        "MerchantID": MERCHANT_ID,
        "RelateNumber": f"ORDER{order_id}T{int(time.time())}", # 確保編號唯一
        "CustomerID": "",
        "CustomerIdentifier": tax_id if is_company else "",
        "CustomerName": customer_name,
        "CustomerAddr": customer_address,
        "CustomerPhone": customer_phone,
        "CustomerEmail": "",
        "ClearanceMark": "", # 應稅通常為空字串
        "Print": print_flag,
        "Donation": "0",
        "LoveCode": "",
        "TaxType": "1", # 1: 應稅
        "SalesAmount": amount,
        "InvoiceRemark": "餐飲服務",
        "Items": [
            {
                "ItemName": "餐飲費用",
                "ItemCount": 1,
                "ItemWord": "式",
                "ItemPrice": amount,
                "ItemTaxType": "1",
                "ItemAmount": amount,
                "ItemRemark": ""
            }
        ],
        "InvType": "07", # 07: 一般稅額
        "vat": "1",      # 1: 含稅
    }
    
    # 處理載具欄位 (綠界規定：若無載具，不能送空字串，必須完全移除該欄位)
    if carrier_type and carrier_num:
        data["CarrierType"] = carrier_type
        data["CarrierNum"] = carrier_num
    
    if not is_company:
        data.pop("CustomerIdentifier", None)

    # 2. 將 Data 進行 AES 加密
    encrypted_data = aes_encrypt(data, HASH_KEY, HASH_IV)

    # 3. 組合最終要 POST 出去的 Payload
    payload = {
        "MerchantID": MERCHANT_ID,
        "RqHeader": {
            "Timestamp": int(time.time()),
            "Revision": "3.0.0" # 綠界文件要求的 API 版本號
        },
        "Data": encrypted_data
    }

    try:
        # 4. 發送請求 (新版要求 Content-Type 必須是 application/json)
        headers = {'Content-Type': 'application/json'}
        response = requests.post(ISSUE_URL, json=payload, headers=headers)
        result_json = response.json()
        
        # 5. 判斷結果 (TransCode == 1 代表綠界系統成功接收並處理)
        if result_json.get("TransCode") == 1:
            # 將綠界回傳的加密 Data 解密，取得真實的發票號碼 InvoiceNo
            response_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
            invoice_no = response_data.get("InvoiceNo", f"SUCCESS_{order_id}")
            
            return {"success": True, "invoice_no": invoice_no, "message": "OK"}
        else:
            return {"success": False, "message": str(result_json)}

    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}


def invalid_ecpay_invoice(invoice_no, reason="訂單取消"):
    """
    發送作廢發票請求給綠界 (供 kitchen_routes 呼叫)
    """
    data = {
        "MerchantID": MERCHANT_ID,
        "InvoiceNo": invoice_no,
        "InvoiceDate": time.strftime("%Y-%m-%d"), # 測試環境通常以當天日期作廢
        "Reason": reason[:20] # 綠界規定作廢原因長度不能超過 20 字
    }
    
    # 進行 AES 加密
    encrypted_data = aes_encrypt(data, HASH_KEY, HASH_IV)
    
    payload = {
        "MerchantID": MERCHANT_ID,
        "RqHeader": {
            "Timestamp": int(time.time()),
            "Revision": "3.0.0"
        },
        "Data": encrypted_data
    }

    try:
        headers = {'Content-Type': 'application/json'}
        response = requests.post(INVALID_URL, json=payload, headers=headers)
        result_json = response.json()
        
        if result_json.get("TransCode") == 1:
            return {"success": True, "message": "作廢成功"}
        else:
            # 如果失敗，嘗試解密錯誤訊息看看詳細原因
            err_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
            msg = err_data.get("RtnMsg") or str(result_json)
            return {"success": False, "message": msg}
            
    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}
