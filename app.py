import requests
import hashlib
import uuid
import asyncio
import base64
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from urllib.parse import urlencode

# ==== CONFIGURATION ====
TOKEN = "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw"
IMEI_API_KEY = "PKZ-HK5K6HMRFAXE5VZLCNW6L"
PAYEER_MERCHANT_ID = "2210021863"
PAYEER_SECRET_KEY = "123"
ADMIN_CHAT_IDS = ["6927331058"]
BASE_URL = "https://api.imeichecks.online"
WEBSITE_URL = "https://imeichecks.online"
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"

# ==== LOGGER SETUP ====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==== GLOBAL ORDER STORE ====
pending_orders = {}

# ==== FLASK APP SETUP ====
app = Flask(__name__)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

@app.route("/")
def index():
    return "Bot is running via Flask webhook."

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update_json = request.get_json(force=True)
    logger.info("Webhook called with payload: %s", update_json)
    try:
        update = Update.de_json(update_json, bot)
        async def handle(): await application.process_update(update)
        loop.run_until_complete(handle())
    except Exception as e:
        logger.error("Error processing update: %s", str(e))
    return "OK"

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    data = request.form
    logger.info("Received Payeer callback data: %s", data)

    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
        return "Invalid callback data", 400

    m_orderid = data['m_orderid']
    m_sign = data['m_sign']
    m_status = data['m_status']

    # Generate expected sign
    sign_string = ":".join([
        data['m_operation_id'],
        data.get('m_operation_ps', ''),
        data.get('m_operation_date', ''),
        data.get('m_operation_pay_date', ''),
        PAYEER_MERCHANT_ID,
        m_orderid,
        data['m_amount'],
        data['m_curr'],
        m_status,
        PAYEER_SECRET_KEY
    ])
    expected_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    if m_sign == expected_sign and m_status == "success":
        order = pending_orders.get(m_orderid)
        if order and not order["paid"]:
            order["paid"] = True
            loop.create_task(send_results(order["user_id"], order["imei"]))
            del pending_orders[m_orderid]
        return "OK"
    else:
        logger.warning("❌ Signature mismatch. Expected: %s, Received: %s", expected_sign, m_sign)
        return "Payment not verified", 400

@app.route("/success")
def success():
    return "<b>Payment successful! Check Telegram for your results.</b>"

@app.route("/fail")
def fail():
    return "<b>Payment failed. Try again in Telegram.</b>"

# ==== TELEGRAM BOT ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to the IMEI Checker Bot!\n"
        "Send /check followed by a 15-digit IMEI number.\n"
        "Example: `/check 013440001737488`\n"
        f"Payment of ${PRICE} via Payeer is required.\n"
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

    user_id = update.message.from_user.id
    order_id = str(uuid.uuid4())

    # Store order in memory
    pending_orders[order_id] = {"user_id": user_id, "imei": imei, "paid": False}

    desc = f"IMEI Check for {imei}"
    m_desc = base64.b64encode(desc.encode()).decode().strip()
    sign_string = f"{PAYEER_MERCHANT_ID}:{order_id}:{PRICE}:USD:{m_desc}:{PAYEER_SECRET_KEY}"
    m_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    payment_data = {
        "m_shop": PAYEER_MERCHANT_ID,
        "m_orderid": order_id,
        "m_amount": PRICE,
        "m_curr": "USD",
        "m_desc": m_desc,
        "m_sign": m_sign,
        "m_status_url": f"{BASE_URL}/payeer",
        "m_success_url": f"{BASE_URL}/success",
        "m_fail_url": f"{BASE_URL}/fail",
        "lang": "en"
    }

    payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"💳 Pay ${PRICE}", url=payment_url)]])
    await update.message.reply_text("💳 Please pay to receive your IMEI result:", reply_markup=reply_markup)

async def send_results(user_id: int, imei: str):
    try:
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        res = requests.get(IMEI_API_URL, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()

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

# ==== APP INIT ====
application = Application.builder().token(TOKEN).build()
bot = application.bot
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("check", check_imei))
application.add_handler(MessageHandler(filters.ALL, lambda u, c: logger.info("Unmatched message from user %s", u.effective_user.id)))
loop.run_until_complete(application.initialize())

if __name__ == '__main__':
    logger.info("Starting Flask app on port 8080")
    app.run(host="0.0.0.0", port=8080)
