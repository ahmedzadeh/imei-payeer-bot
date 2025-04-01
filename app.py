import requests
import sqlite3
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import hashlib
import uuid
import asyncio
import threading
from urllib.parse import urlencode
import os

app = Flask(__name__)

# Configuration via environment variables
TOKEN = os.getenv("TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5-K6H-MRF-AXE-5VZ-LCN-W6L")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2210021863")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
ADMIN_CHAT_IDS = [os.getenv("ADMIN_CHAT_ID", "6927331058")]  # Replace with your and your partner's chat IDs
BASE_URL = "https://imei-payeer-bot.onrender.com"  # Your Render subdomain
WEBSITE_URL = "https://imeichecks.online"  # Your GoDaddy domain

# Constants
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
bot = Bot(TOKEN)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect("payments.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        order_id TEXT PRIMARY KEY,
        user_id INTEGER,
        imei TEXT,
        paid BOOLEAN
    )""")
    conn.commit()
    conn.close()

init_db()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"👋 Welcome to the IMEI Checker Bot!\n"
        f"Send /check followed by a 15-digit IMEI number.\n"
        f"Example: `/check 013440001737488`\n"
        f"Payment of $0.32 USD via Payeer is required.\n"
        f"Visit our website: {WEBSITE_URL}",
        parse_mode="Markdown"
    )

async def check_imei(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "📌 Please enter a 15-digit IMEI number.\nExample: `/check 013440001737488`",
            parse_mode="Markdown"
        )
        return

    imei = context.args[0]
    if not (imei.isdigit() and len(imei) == 15):
        await update.message.reply_text(
            "❌ Please provide a valid 15-digit IMEI number.",
            parse_mode="Markdown"
        )
        return

    order_id = str(uuid.uuid4())
    user_id = update.message.from_user.id
    amount = "0.32"

    # Store payment in SQLite
    conn = sqlite3.connect("payments.db")
    c = conn.cursor()
    c.execute("INSERT INTO payments (order_id, user_id, imei, paid) VALUES (?, ?, ?, ?)",
              (order_id, user_id, imei, False))
    conn.commit()
    conn.close()

    payment_data = {
        "m_shop": PAYEER_MERCHANT_ID,
        "m_orderid": order_id,
        "m_amount": amount,
        "m_curr": "USD",
        "m_desc": f"IMEI Check for {imei}",
        "m_sign": hashlib.sha256(
            f"{PAYEER_MERCHANT_ID}:{order_id}:{amount}:USD:{PAYEER_SECRET_KEY}".encode()
        ).hexdigest(),
        "m_status_url": f"{BASE_URL}/payeer",
        "m_success_url": f"{BASE_URL}/success",
        "m_fail_url": f"{BASE_URL}/fail"
    }
    payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"

    await update.message.reply_text(
        f"💳 Please pay {amount} USD here:\n{payment_url}\n"
        "Results will be sent automatically after payment.",
        parse_mode="Markdown"
    )

async def report_error(error_message):
    for chat_id in ADMIN_CHAT_IDS:
        await bot.send_message(chat_id=chat_id, text=f"⚠️ Error: {error_message}", parse_mode="Markdown")

async def send_results(user_id: int, imei: str):
    params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
    try:
        response = requests.get(IMEI_API_URL, params=params, timeout=10)
        response.raise_for_status()
        imei_data = response.json()

        if not imei_data or "IMEI" not in imei_data:
            await bot.send_message(chat_id=user_id, text="⚠️ No valid data found for this IMEI.", parse_mode="Markdown")
            return

        imei_1 = imei_data.get('IMEI', 'N/A')
        imei_2 = imei_data.get('IMEI2', 'N/A')
        meid = imei_data.get('MEID', 'N/A')
        serial_number = imei_data.get('Serial Number', 'N/A')
        description = imei_data.get('Description', 'N/A')
        date_of_purchase = imei_data.get('Date of purchase', 'N/A')
        repairs_service_coverage = imei_data.get('Repairs & Service Coverage', 'N/A')
        is_replaced = imei_data.get('is replaced', 'N/A')
        sim_lock = imei_data.get('SIM Lock', 'N/A')

        message = (
            f"📱 **IMEI Information:**\n\n"
            f"🔹 **IMEI 1:** {imei_1}\n"
            f"🔹 **IMEI 2:** {imei_2}\n"
            f"🔹 **MEID:** {meid}\n"
            f"🔹 **Serial Number:** {serial_number}\n"
            f"🔹 **Description:** {description}\n"
            f"🔹 **Date of Purchase:** {date_of_purchase}\n"
            f"🔹 **Repairs & Service Coverage:** {repairs_service_coverage}\n"
            f"🔹 **Is Replaced:** {is_replaced}\n"
            f"🔹 **SIM Lock:** {sim_lock}"
        )
        await bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
    except requests.RequestException as e:
        error_msg = f"IMEI check failed for user {user_id}, IMEI {imei}: {str(e)}"
        await bot.send_message(chat_id=user_id, text=f"❌ IMEI check failed: {str(e)}. Try again later.", parse_mode="Markdown")
        await report_error(error_msg)

@app.route('/payeer', methods=['POST'])
def payeer_callback():
    data = request.form
    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
        error_msg = "Invalid callback data from Payeer"
        asyncio.run_coroutine_threadsafe(report_error(error_msg), loop)
        return "Invalid callback data", 400

    m_operation_id = data['m_operation_id']
    m_sign = data['m_sign']
    m_orderid = data['m_orderid']
    m_amount = data['m_amount']
    m_curr = data['m_curr']
    m_status = data['m_status']

    sign_string = f"{m_operation_id}:{data.get('m_operation_ps', '')}:{data.get('m_operation_date', '')}:{data.get('m_operation_pay_date', '')}:{PAYEER_MERCHANT_ID}:{m_orderid}:{m_amount}:{m_curr}:{m_status}:{PAYEER_SECRET_KEY}"
    expected_sign = hashlib.sha256(sign_string.encode()).hexdigest()

    if m_sign == expected_sign and m_status == "success":
        conn = sqlite3.connect("payments.db")
        c = conn.cursor()
        c.execute("SELECT user_id, imei FROM payments WHERE order_id = ? AND paid = 0", (m_orderid,))
        result = c.fetchone()
        if result:
            user_id, imei = result
            c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (m_orderid,))
            conn.commit()
            asyncio.run_coroutine_threadsafe(send_results(user_id, imei), loop)
            c.execute("DELETE FROM payments WHERE order_id = ?", (m_orderid,))
            conn.commit()
            conn.close()
            return "OK"
        else:
            error_msg = f"Payment callback for order {m_orderid} failed: Order not found or already processed"
            asyncio.run_coroutine_threadsafe(report_error(error_msg), loop)
        conn.close()
    else:
        error_msg = f"Payment verification failed for order {m_orderid}: Invalid signature or status"
        asyncio.run_coroutine_threadsafe(report_error(error_msg), loop)
    return "Payment not verified", 400

@app.route('/success')
def success():
    return "Payment successful! Check Telegram for your results."

@app.route('/fail')
def fail():
    return "Payment failed. Try again in Telegram."

def run_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_imei))
    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))  # Render default
    app.run(host="0.0.0.0", port=port)
