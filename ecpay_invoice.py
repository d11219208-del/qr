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
PRINT_URL = os.environ.get("ECPAY_INVOICE_PRINT_URL", "https://einvoice-stage.ecpay.com.tw/B2CInvoice/InvoicePrint")

def aes_encrypt(data_dict, key, iv):
    """綠界新版電子發票專用的 AES 加密"""
    json_str = json.dumps(data_dict, ensure_ascii=False, separators=(',', ':'))
    url_encoded = urllib.parse.quote(json_str, safe='')
    cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
    padded_data = pad(url_encoded.encode('utf-8'), AES.block_size)
    encrypted_bytes = cipher.encrypt(padded_data)
    encrypted_base64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    return encrypted_base64

def aes_decrypt(encrypted_base64, key, iv):
    """綠界新版電子發票專用的 AES 解密"""
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
    修正點：強化 content_json 解析與金額轉型，避免金額為 0
    """
    order_id = order.get('id', '')
    
    # 1. 解析商品明細 (從 database.py 的 content_json 欄位)
    c_json = order.get('content_json')
    ecpay_items = []
    calculated_total = 0
    
    try:
        # 確保 c_json 是 list 格式
        if isinstance(c_json, str):
            cart = json.loads(c_json)
        elif isinstance(c_json, list):
            cart = c_json
        else:
            cart = []

        for item in cart:
            # 抓取名稱，優先順序：中文名 > 原名 > 預設
            name = item.get('name_zh') or item.get('name') or "商品"
            
            # 強制轉換數量與單價為數值，若失敗則給予預設值
            try:
                qty = int(float(item.get('qty', 1)))
                price = int(float(item.get('price', 0)))
            except (ValueError, TypeError):
                qty = 1
                price = 0
            
            item_sum = price * qty
            
            # 處理規格 (options_zh)，附加在名稱後
            options = item.get('options_zh') or item.get('options', [])
            if options and isinstance(options, list):
                name += f" ({'/'.join(options)})"

            ecpay_items.append({
                "ItemName": name[:30], 
                "ItemCount": qty,
                "ItemWord": "份",
                "ItemPrice": price,
                "ItemTaxType": "1",
                "ItemAmount": item_sum,
                "ItemRemark": ""
            })
            calculated_total += item_sum
            
    except Exception as e:
        print(f"❌ 明細解析失敗: {e}")

    # --- 防呆機制：如果明細加總為 0，嘗試使用 order 裡的總金額 ---
    if calculated_total == 0:
        db_total = order.get('total_price', 0)
        try:
            calculated_total = int(float(db_total))
        except:
            calculated_total = 0
            
        ecpay_items = [{
            "ItemName": "餐飲費用",
            "ItemCount": 1,
            "ItemWord": "式",
            "ItemPrice": calculated_total,
            "ItemTaxType": "1",
            "ItemAmount": calculated_total,
            "ItemRemark": ""
        }]

    # 2. 客戶資訊與發票類型判斷
    customer_phone = str(order.get('customer_phone') or "").strip()
    customer_email = str(order.get('customer_email') or "").strip()
    if not customer_phone and not customer_email:
        customer_email = "no-reply@test.com"
        
    customer_name = order.get('customer_name') or "門市顧客"
    customer_address = order.get('customer_address') or "台北市"
    
    tax_id = str(order.get('tax_id') or "").strip()
    carrier_type = str(order.get('carrier_type') or "").strip()
    carrier_num = str(order.get('carrier_num') or "").strip()

    is_company = bool(tax_id and len(tax_id) == 8)

    # 判斷是否需要列印
    print_flag = "0"
    if is_company:
        print_flag = "1" # 有統編一定要列印
    if carrier_type:
        print_flag = "0" # 有載具就不列印

    # 3. 組裝 Data 物件
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
        "SalesAmount": calculated_total, # 最終總金額
        "InvoiceRemark": f"Order ID: {order_id}",
        "Items": ecpay_items,
        "InvType": "07", 
        "vat": "1",      
    }
    
    if carrier_type and carrier_num:
        data["CarrierType"] = carrier_type
        data["CarrierNum"] = carrier_num
    
    if not is_company:
        data.pop("CustomerIdentifier", None)

    # 4. 加密與 API 發送
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
                return {
                    "success": True, 
                    "invoice_no": response_data.get("InvoiceNo", ""), 
                    "random_number": response_data.get("RandomNumber", ""),
                    "message": "OK"
                }
            else:
                return {"success": False, "message": f"綠界退件: {rtn_msg} (代碼: {rtn_code})"}
        else:
            return {"success": False, "message": f"API 通訊失敗: {result_json.get('TransMsg')}"}
    except Exception as e:
        return {"success": False, "message": f"Request failed: {str(e)}"}

def invalid_ecpay_invoice(invoice_no, reason="訂單取消"):
    """發送作廢發票請求"""
    data = {
        "MerchantID": MERCHANT_ID,
        "InvoiceNo": invoice_no,
        "InvoiceDate": time.strftime("%Y-%m-%d"), 
        "Reason": reason[:20] 
    }
    
    encrypted_data = aes_encrypt(data, HASH_KEY, HASH_IV)
    payload = {
        "MerchantID": MERCHANT_ID,
        "RqHeader": { "Timestamp": int(time.time()), "Revision": "3.0.0" },
        "Data": encrypted_data
    }

    try:
        response = requests.post(INVALID_URL, json=payload)
        result_json = response.json()
        if result_json.get("TransCode") == 1:
            response_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
            if response_data.get("RtnCode") == 1:
                return {"success": True, "message": "作廢成功"}
        return {"success": False, "message": "作廢失敗"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def print_ecpay_invoice(invoice_no):
    """發送列印發票請求並取得 HTML"""
    data = {
        "MerchantID": MERCHANT_ID,
        "InvoiceNo": invoice_no,
        "PrintStyle": "1", 
        "IsPrint": "1"
    }
    
    encrypted_data = aes_encrypt(data, HASH_KEY, HASH_IV)
    payload = {
        "MerchantID": MERCHANT_ID,
        "RqHeader": { "Timestamp": int(time.time()), "Revision": "3.0.0" },
        "Data": encrypted_data
    }

    try:
        response = requests.post(PRINT_URL, json=payload)
        try:
            result_json = response.json()
            if result_json.get("TransCode") == 1:
                res_data = aes_decrypt(result_json.get("Data", ""), HASH_KEY, HASH_IV)
                if res_data.get("RtnCode") == 1:
                    return {"success": True, "html": res_data.get("InvoiceHtml", "")}
            return {"success": False, "message": "取得列印資料失敗"}
        except:
            if response.text.strip().startswith("<"):
                return {"success": True, "html": response.text}
            return {"success": False, "message": "格式錯誤"}
    except Exception as e:
        return {"success": False, "message": str(e)}
