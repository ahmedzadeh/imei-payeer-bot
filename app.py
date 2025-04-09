import requests
from flask import Flask, request, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import hashlib
import uuid
import os
from urllib.parse import urlencode
import base64
import logging
import asyncio
import traceback

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")]
)
logger = logging.getLogger(__name__)

# Configuration
TOKEN = os.getenv("TOKEN")
IMEI_API_KEY = os.getenv("IMEI_API_KEY")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID")
PAYEER_SECRET_KEY = "11%=2;}-|0@.{QVVXdw~"
BASE_URL = os.getenv("BASE_URL")

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()
user_states = {}

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        update = Update.de_json(update_json, application.bot)
        application.create_task(application.process_update(update))

        return "OK"
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error", 500

def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=reply_markup)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("ℹ️ Use the 'Check IMEI' button and follow instructions to proceed.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text

        if text == "🔍 Check IMEI":
            user_states[user_id] = "awaiting_imei"
            await update.message.reply_text("🔢 Please enter your 15-digit IMEI number.")
        elif text == "❓ Help":
            await update.message.reply_text("ℹ️ Use the 'Check IMEI' button and follow instructions to proceed.")
        elif user_states.get(user_id) == "awaiting_imei":
            imei = text.strip()
            if not imei.isdigit() or len(imei) != 15:
                await update.message.reply_text("❌ Invalid IMEI. It must be 15 digits.")
                return

            desc = f"IMEI Check for {imei}"
            m_desc = base64.b64encode(desc.encode()).decode()
            order_id = str(uuid.uuid4())
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
                "m_success_url": f"{BASE_URL}/success?m_orderid={order_id}",
                "m_fail_url": f"{BASE_URL}/fail"
            }

            payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay $0.32 USD", url=payment_url)]])

            await update.message.reply_text(
                f"📱 IMEI: {imei}\nTo receive your result, please complete payment:",
                reply_markup=keyboard
            )
            user_states[user_id] = None
        else:
            await update.message.reply_text("❗ Please use the menu or /start to begin.")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

register_handlers()

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    return "OK"  # No DB used

@app.route("/success")
def success():
    return render_template("success.html")

@app.route("/fail")
def fail():
    return render_template("fail.html")

def send_imei_result(user_id, imei):
    try:
        for checker in ["simlock2", "simlock3"]:
            params = {"api_key": IMEI_API_KEY, "checker": checker, "number": imei}
            res = requests.get(IMEI_API_URL, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if data.get("IMEI"):
                    break
        else:
            asyncio.run(application.bot.send_message(chat_id=user_id, text="❌ IMEI not found."))
            return

        msg = "✅ Payment successful!\n\n"
        msg += f"IMEI: {data.get('IMEI', 'N/A')}\n"
        msg += f"IMEI2: {data.get('IMEI2', 'N/A')}\n"
        msg += f"MEID: {data.get('MEID', 'N/A')}\n"
        msg += f"Serial: {data.get('Serial Number', 'N/A')}\n"
        msg += f"Desc: {data.get('Description', 'N/A')}\n"
        msg += f"Purchase: {data.get('Date of purchase', 'N/A')}\n"
        msg += f"Coverage: {data.get('Repairs & Service Coverage', 'N/A')}\n"
        msg += f"Replaced: {data.get('is replaced', 'N/A')}\n"
        msg += f"SIM Lock: {data.get('SIM Lock', 'N/A')}"

        asyncio.run(application.bot.send_message(chat_id=user_id, text=msg))
    except Exception as e:
        logger.error(f"Sending result error: {str(e)}")

async def set_webhook_async():
    try:
        await application.initialize()
        webhook_url = f"{BASE_URL}/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        logger.error(traceback.format_exc())

def set_webhook():
    asyncio.run(set_webhook_async())

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=8080)
