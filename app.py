from flask import Flask, request
import hashlib
import json
import os
import requests
from telegram import Bot

app = Flask(__name__)

MERCHANT_ID = "2209595647"
SECRET_KEY = "123"
BOT_TOKEN = "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw"
bot = Bot(token=BOT_TOKEN)

API_KEY = "PKZ-HK5-K6H-MRF-AXE-5VZ-LCN-W6L"
API_URL = "https://proimei.info/en/prepaid/api"

PAYMENTS_FILE = "payments.json"

def save_payment(user_id, imei):
    if not os.path.exists(PAYMENTS_FILE):
        with open(PAYMENTS_FILE, "w") as f:
            json.dump({}, f)

    with open(PAYMENTS_FILE, "r") as f:
        data = json.load(f)

    data.setdefault(str(user_id), []).append(imei)

    with open(PAYMENTS_FILE, "w") as f:
        json.dump(data, f)

@app.route('/payment', methods=['POST'])
def payment():
    data = request.form

    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_desc', 'm_status']
    if not all(field in data for field in required_fields):
        return "Missing fields", 400

    # Step 1: Verify signature
    sign_string = ":".join([
        data['m_operation_id'],
        data['m_operation_ps'],
        data['m_operation_date'],
        data['m_operation_pay_date'],
        data['m_shop'],
        data['m_orderid'],
        data['m_amount'],
        data['m_curr'],
        data['m_desc'],
        data['m_status'],
        SECRET_KEY
    ])
    sign_hash = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    if sign_hash != data['m_sign']:
        return "Invalid signature", 400

    if data['m_status'] != "success":
        return "Payment not successful", 400

    # Step 2: Extract info from m_orderid
    try:
        order_parts = data['m_orderid'].split("_imei")
        user_id = int(order_parts[0].replace("tg", ""))
        imei = order_parts[1]
    except:
        return "Invalid order format", 400

    # Step 3: Save payment
    save_payment(user_id, imei)

    # Step 4: Send IMEI result
    imei_api_url = f"{API_URL}?api_key={API_KEY}&checker=simlock2&number={imei}"
    try:
        response = requests.get(imei_api_url)
        if response.status_code == 200:
            imei_data = response.json()
            message = (
                f"📱 *IMEI Information:*\n\n"
                f"🔹 *IMEI 1:* {imei_data.get('IMEI', 'No data')}\n"
                f"🔹 *IMEI 2:* {imei_data.get('IMEI2', 'No data')}\n"
                f"🔹 *MEID:* {imei_data.get('MEID', 'No data')}\n"
                f"🔹 *Serial Number:* {imei_data.get('Serial Number', 'No data')}\n"
                f"🔹 *Description:* {imei_data.get('Description', 'No data')}\n"
                f"🔹 *Date of Purchase:* {imei_data.get('Date of purchase', 'No data')}\n"
                f"🔹 *Repairs & Service Coverage:* {imei_data.get('Repairs & Service Coverage', 'No data')}\n"
                f"🔹 *Is Replaced:* {imei_data.get('is replaced', 'No data')}\n"
                f"🔹 *SIM Lock:* {imei_data.get('SIM Lock', 'No data')}"
            )
            bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
        else:
            bot.send_message(chat_id=user_id, text="❌ Payment was received, but IMEI check failed.")
    except Exception as e:
        bot.send_message(chat_id=user_id, text=f"⚠️ Error after payment: {str(e)}")

    return "OK"
