from flask import Flask, request, abort
import hashlib
import os
import requests
import json

app = Flask(__name__)

# Payeer credentials
MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "YOUR_MERCHANT_ID")
SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "YOUR_SECRET_KEY")

# Telegram Bot
BOT_TOKEN = "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

PAYMENTS_FILE = "payments.json"

# Load or initialize payments database
def load_payments():
    if not os.path.exists(PAYMENTS_FILE):
        return {}
    with open(PAYMENTS_FILE, "r") as f:
        return json.load(f)

def save_payment(tg_id, imei):
    data = load_payments()
    data[tg_id] = data.get(tg_id, [])
    if imei not in data[tg_id]:
        data[tg_id].append(imei)
    with open(PAYMENTS_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/payeer", methods=["POST"])
def payeer_webhook():
    data = request.form.to_dict()
    print("Received IPN:", data)

    if not all(k in data for k in ("m_operation_id", "m_sign")):
        return "Missing fields", 400

    sign_string = ":".join([
        data.get("m_operation_id", ""),
        data.get("m_operation_ps", ""),
        data.get("m_operation_date", ""),
        data.get("m_operation_pay_date", ""),
        data.get("m_shop", ""),
        data.get("m_orderid", ""),
        data.get("m_amount", ""),
        data.get("m_curr", ""),
        data.get("m_desc", ""),
        data.get("m_status", ""),
        SECRET_KEY
    ])

    local_sign = hashlib.sha256(sign_string.encode("utf-8")).hexdigest().upper()
    if local_sign != data.get("m_sign"):
        return "Invalid signature", 403

    if data.get("m_status") == "success":
        order_id = data.get("m_orderid")
        print(f"✅ Payment confirmed for order ID: {order_id}")

        # Extract Telegram user_id from order_id (expected format: tg123456789_imei123456789012345)
        if order_id.startswith("tg"):
            try:
                tg_id = order_id.split("_")[0][2:]
                imei = order_id.split("_imei")[-1]

                # Save payment info
                save_payment(tg_id, imei)

                # Notify user
                message = f"✅ Payment received for IMEI: `{imei}`\nYou can now check your result."
                payload = {
                    "chat_id": tg_id,
                    "text": message,
                    "parse_mode": "Markdown"
                }
                requests.post(TELEGRAM_API, data=payload)
            except Exception as e:
                print("Error parsing order ID or notifying user:", e)

        return data.get("m_orderid", ""), 200

    else:
        print("❌ Payment failed or not complete.")
        return "Payment failed", 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
