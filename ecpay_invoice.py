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

# 綠界新版 B2C API 路徑
ISSUE_URL = os.environ.get("ECPAY_INVOICE_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Issue")
INVALID_URL = os.environ.get("ECPAY_INVOICE_INVALID_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Invalid")
# 👇 新增：列印發票 API 路徑
PRINT_URL = os.environ.get("ECPAY_INVOICE_PRINT_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/InvoicePrint")

def aes_encrypt(data_dict, key, iv):
    """
    綠界新版電子發票專用的 AES 加密
    """
    json_str = json.dumps(data_dict, ensure_ascii=False, separators=(',', ':'))
    url_encoded = urllib.parse.quote(json_str, safe='')
    cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
    padded_data = pad(url_encoded.encode('utf-8'), AES.block_size)
    encrypted_bytes = cipher.encrypt(padded_data)
    encrypted_base64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    return encrypted_base64

def aes_decrypt(encrypted_base64, key, iv):
    """
    綠界新版電子發票專用的 AES 解密
    """
    try:
        encrypted_bytes = base64.b64decode(encrypted_base64)
        cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
        decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
        url_encoded_str = decrypted_bytes.decode('utf-8')
        json_str = urllib.parse.unquote(url_encoded_str)
        return json.loads(json_str)
    except Exception as e:
        print(f"AES Decrypt Error: {e}")
        return {}

def issue_ecpay_invoice(order):
    """
    發送開立發票請求給綠界
    """
    order_id = order.get('id', '')
    amount = int(order.get('total_price', 0))
    
    # 手機與信箱防呆邏輯
    customer_phone = str(order.get('customer_phone', '') or "").strip()
    customer_email = ""
    if not customer_phone:
        customer_email = "no-reply@test.com"
        
    customer_name = order.get('customer_name', '') or "門市顧客"
    customer_address = order.get('customer_address', '') or "台北市內湖區"
    
    tax_id = order.get('tax_id', '') or ''
    carrier_type = order.get('carrier_type', '') or ''
    carrier_num = order.get('carrier_num', '') or ''

    is_company = bool(tax_id and len(str(tax_id)) == 8)

    print_flag = "0"
    if is_company:
        print_flag = "1"
    if carrier_type:
        print_flag = "0"

    data = {
        "MerchantID": MERCHANT_ID,
        "RelateNumber": f"ORDER{order_id}T{int(time.time())}", 
        "CustomerID": "",
        "CustomerIdentifier": tax_id if is_company else "",
        "CustomerName": customer_name,
        "CustomerAddr": customer_address,
        "CustomerPhone": customer_phone,
        "CustomerEmail": customer_email, 
        "ClearanceMark": "", 
        "Print": print_flag,
        "Donation": "0",
        "LoveCode": "",
        "TaxType": "1", 
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
        "InvType": "07", 
        "vat": "1",      
    }
    
    if carrier_type and carrier_num:
        data["CarrierType"] = carrier_type
        data["CarrierNum"] = carrier_num
    
    if not is_company:
        data.pop("CustomerIdentifier", None)

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
        response = requests.post(ISSUE_URL, json=payload, headers=headers)
        result_json = response.json()
        
        if result_json.get("TransCode") == 1:
            response_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
            rtn_code = response_data.get("RtnCode")
            rtn_msg = response_data.get("RtnMsg", "無錯誤訊息")
            
            if rtn_code == 1:
                invoice_no = response_data.get("InvoiceNo", "")
                return {"success": True, "invoice_no": invoice_no, "message": "OK"}
            else:
                print(f"❌ 綠界拒絕開立發票: {rtn_msg} (代碼: {rtn_code})")
                return {"success": False, "message": f"綠界退件: {rtn_msg} (代碼: {rtn_code})"}
        else:
            return {"success": False, "message": f"API 通訊失敗: {result_json.get('TransMsg')}"}

    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}


def invalid_ecpay_invoice(invoice_no, reason="訂單取消"):
    """
    發送作廢發票請求給綠界
    """
    data = {
        "MerchantID": MERCHANT_ID,
        "InvoiceNo": invoice_no,
        "InvoiceDate": time.strftime("%Y-%m-%d"), 
        "Reason": reason[:20] 
    }
    
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
            response_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
            if response_data.get("RtnCode") == 1:
                return {"success": True, "message": "作廢成功"}
            else:
                msg = response_data.get("RtnMsg", str(response_data))
                return {"success": False, "message": f"綠界退件: {msg}"}
        else:
            err_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
            msg = err_data.get("RtnMsg") or str(result_json)
            return {"success": False, "message": msg}
            
    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}


# 👇 新增：列印發票函數
def print_ecpay_invoice(invoice_no):
    """
    發送列印發票請求給綠界
    回傳：包含發票排版的 HTML 字串 (供前端列印使用)
    """
    data = {
        "MerchantID": MERCHANT_ID,
        "InvoiceNo": invoice_no,
        "PrintStyle": "1", # 1: 證明聯 (預設), 2: 明細, 3: 證明聯+明細
        "IsPrint": "1"     # 1: 執行列印
    }
    
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
        response = requests.post(PRINT_URL, json=payload, headers=headers)
        
        # 嘗試解析 JSON (綠界 V3 API 通常回傳 JSON)
        try:
            result_json = response.json()
            if result_json.get("TransCode") == 1:
                response_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
                if response_data.get("RtnCode") == 1:
                    # 取得 HTML 內容
                    invoice_html = response_data.get("InvoiceHtml", "")
                    return {"success": True, "html": invoice_html}
                else:
                    return {"success": False, "message": f"列印失敗: {response_data.get('RtnMsg')}"}
            else:
                return {"success": False, "message": f"API 通訊失敗: {result_json.get('TransMsg')}"}
        except json.JSONDecodeError:
            # 防呆：如果綠界直接回傳 HTML 網頁而不是 JSON，就直接抓取
            if response.text.strip().startswith("<"):
                return {"success": True, "html": response.text}
            else:
                return {"success": False, "message": "收到未知的非 JSON 格式"}
            
    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}
