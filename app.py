import requests
import sqlite3
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import hashlib
import uuid
import asyncio
import os
from urllib.parse import quote_plus
import base64
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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
    logger.info("Webhook called with payload: %s", update_json)

    try:
        update = Update.de_json(update_json, bot)

        async def handle():
            await application.process_update(update)

        loop.run_until_complete(handle())
    except Exception as e:
        logger.error("Error processing update: %s", str(e))

    return "OK"

@app.route('/payeer', methods=['POST'])
def payeer_callback():
    data = request.form.to_dict()
    logger.info("Received Payeer callback data: %s", data)

    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
        logger.error("Invalid callback data: %s", data)
        return "Invalid callback data", 400

    m_operation_id = data['m_operation_id']
    m_sign = data['m_sign']
    m_orderid = data['m_orderid']
    m_amount = data['m_amount']
    m_curr = data['m_curr']
    m_status = data['m_status']

    sign_string = f"{m_operation_id}:{data.get('m_operation_ps', '')}:{data.get('m_operation_date', '')}:{data.get('m_operation_pay_date', '')}:{PAYEER_MERCHANT_ID}:{m_orderid}:{m_amount}:{m_curr}:{m_status}:{PAYEER_SECRET_KEY}"
    expected_sign = hashlib.sha256(sign_string.encode()).hexdigest()

    if m_sign != expected_sign:
        logger.warning("❌ Signature mismatch!\nExpected: %s\nReceived: %s", expected_sign, m_sign)
    if m_status != "success":
        logger.warning("❌ Payment status is not 'success': %s", m_status)

    if m_sign == expected_sign and m_status == "success":
        conn = sqlite3.connect("payments.db")
        c = conn.cursor()
        c.execute("SELECT user_id, imei FROM payments WHERE order_id = ? AND paid = 0", (m_orderid,))
        result = c.fetchone()
        logger.info("Database lookup result: %s", result)
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
    logger.info("/start handler triggered by user %s", update.effective_user.id)
    try:
        await update.message.reply_text(
            "👋 Welcome to the IMEI Checker Bot!\n"
            "Send /check followed by a 15-digit IMEI number.\n"
            "Example: `/check 013440001737488`\n"
            "Payment of $0.32 USD via Payeer is required.\n"
            f"Visit our website: {WEBSITE_URL}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Error in /start: %s", str(e))

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

    desc = f"IMEI Check for {imei}"
    m_desc = base64.b64encode(desc.encode()).decode().strip()
    sign_string = f"{PAYEER_MERCHANT_ID}:{order_id}:{amount}:USD:{m_desc}:{PAYEER_SECRET_KEY}"
    m_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    payment_url = (
        f"{PAYEER_PAYMENT_URL}?"
        f"m_shop={PAYEER_MERCHANT_ID}"
        f"&m_orderid={order_id}"
        f"&m_amount={amount}"
        f"&m_curr=USD"
        f"&m_desc={m_desc}"
        f"&m_sign={m_sign}"
        f"&m_status_url={BASE_URL}/payeer"
        f"&m_success_url={BASE_URL}/success"
        f"&m_fail_url={BASE_URL}/fail"
        f"&lang=en"
    )

    logger.info("Generated Payeer payment URL: %s", payment_url)

    button = [[InlineKeyboardButton("💳 Pay $0.32 via Payeer", url=payment_url)]]
    markup = InlineKeyboardMarkup(button)

    await update.message.reply_text(
        "Click the button below to complete the payment:",
        reply_markup=markup
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

# Telegram App Init
application = Application.builder().token(TOKEN).build()
bot = application.bot
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("check", check_imei))
application.add_handler(MessageHandler(filters.ALL, lambda u, c: logger.info("Caught unmatched update from user: %s", u.effective_user.id)))
loop.run_until_complete(application.initialize())

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting Flask app on port %s", port)
    app.run(host="0.0.0.0", port=port)
