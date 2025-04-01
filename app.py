import requests
import sqlite3
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import hashlib
import uuid
import asyncio
import os
from urllib.parse import urlencode

# Configuration
TOKEN = os.getenv("TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2210021863")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
ADMIN_CHAT_IDS = [os.getenv("ADMIN_CHAT_ID", "6927331058")]
BASE_URL = "https://api.imeichecks.online"
WEBSITE_URL = "https://imeichecks.online"

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"

app = Flask(__name__)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", lambda u, c: loop.create_task(start(u, c))))
application.add_handler(CommandHandler("check", lambda u, c: loop.create_task(check_imei(u, c))))
bot = application.bot

# Init DB
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

@app.route("/")
def index():
    return "Bot is running via Flask webhook."

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update_json = request.get_json(force=True)
    print("✅ Webhook called")
    print("📦 Payload received:", update_json)

    try:
        update = Update.de_json(update_json, bot)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.process_update(update))
    except Exception as e:
        print("❌ Error processing update:", str(e))

    return "OK"




@app.route('/payeer', methods=['POST'])
def payeer_callback():
    data = request.form
    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
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
            loop.create_task(send_results(user_id, imei))
            c.execute("DELETE FROM payments WHERE order_id = ?", (m_orderid,))
            conn.commit()
        conn.close()
        return "OK"
    return "Payment not verified", 400

@app.route('/success')
def success():
    return "Payment successful! Check Telegram for your results."

@app.route('/fail')
def fail():
    return "Payment failed. Try again in Telegram."

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Welcome to the IMEI Checker Bot!\n"
        f"Send /check followed by a 15-digit IMEI number.\n"
        f"Example: `/check 013440001737488`\n"
        f"Payment of $0.32 USD via Payeer is required.\n"
        f"Visit our website: {WEBSITE_URL}",
        parse_mode="Markdown"
    )

async def check_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please provide a 15-digit IMEI.", parse_mode="Markdown")
        return

    imei = context.args[0]
    if not imei.isdigit() or len(imei) != 15:
        await update.message.reply_text("Invalid IMEI. Please provide a 15-digit number.", parse_mode="Markdown")
        return

    order_id = str(uuid.uuid4())
    user_id = update.message.from_user.id
    amount = "0.32"

    conn = sqlite3.connect("payments.db")
    c = conn.cursor()
    c.execute("INSERT INTO payments (order_id, user_id, imei, paid) VALUES (?, ?, ?, ?)", (order_id, user_id, imei, False))
    conn.commit()
    conn.close()

    payment_data = {
        "m_shop": PAYEER_MERCHANT_ID,
        "m_orderid": order_id,
        "m_amount": amount,
        "m_curr": "USD",
        "m_desc": f"IMEI Check for {imei}",
        "m_sign": hashlib.sha256(f"{PAYEER_MERCHANT_ID}:{order_id}:{amount}:USD:{PAYEER_SECRET_KEY}".encode()).hexdigest(),
        "m_status_url": f"{BASE_URL}/payeer",
        "m_success_url": f"{BASE_URL}/success",
        "m_fail_url": f"{BASE_URL}/fail"
    }

    payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"

    await update.message.reply_text(
        f"💳 Please pay {amount} USD here:\n{payment_url}\nResults will be sent automatically after payment.",
        parse_mode="Markdown"
    )

async def send_results(user_id: int, imei: str):
    params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
    try:
        response = requests.get(IMEI_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        msg = "\n".join([
            "📱 *IMEI Info:*",
            f"🔹 *IMEI 1:* {data.get('IMEI', 'N/A')}",
            f"🔹 *IMEI 2:* {data.get('IMEI2', 'N/A')}",
            f"🔹 *MEID:* {data.get('MEID', 'N/A')}",
            f"🔹 *Serial Number:* {data.get('Serial Number', 'N/A')}",
            f"🔹 *Description:* {data.get('Description', 'N/A')}",
            f"🔹 *Purchase Date:* {data.get('Date of purchase', 'N/A')}",
            f"🔹 *Coverage:* {data.get('Repairs & Service Coverage', 'N/A')}",
            f"🔹 *Is Replaced:* {data.get('is replaced', 'N/A')}",
            f"🔹 *SIM Lock:* {data.get('SIM Lock', 'N/A')}",
        ])

        await bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
    except Exception as e:
        await bot.send_message(chat_id=user_id, text=f"❌ Failed to fetch IMEI data: {e}")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
