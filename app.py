import requests
import logging
import asyncio
import os
import traceback
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")]
)
logger = logging.getLogger(__name__)

# Configuration
TOKEN = os.getenv("TOKEN", "8018027330:AAE6Se5mieBz4YzRESLJRj-5p3M1KHAQ6Go")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
BASE_URL = os.getenv("BASE_URL", "https://api.imeichecks.online")

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()
user_states = {}

def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[KeyboardButton("🔍 Check IMEI")]]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("👋 Welcome! Tap below to check IMEI.", reply_markup=markup)

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()

        if text == "🔍 Check IMEI":
            user_states[user_id] = "awaiting_imei"
            await update.message.reply_text("📱 Please enter your 15-digit IMEI number.")
        elif user_states.get(user_id) == "awaiting_imei":
            if not text.isdigit() or len(text) != 15:
                await update.message.reply_text("❌ IMEI must be 15 digits.")
                return
            await update.message.reply_text("🔎 Checking IMEI, please wait...")
            send_imei_result(user_id, text)
            user_states[user_id] = None
        else:
            await update.message.reply_text("ℹ️ Use /start to begin.")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

register_handlers()

def send_imei_result(user_id, imei):
    async def send():
        try:
            print("✅ Sending IMEI result...")
            params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
            logger.info(f"Querying IMEI API with params: {params}")
            res = requests.get(IMEI_API_URL, params=params, timeout=15)
            res.raise_for_status()
            logger.info(f"API raw response: {res.text}")

            data = res.json()
            logger.info(f"Parsed JSON: {data}")
            info = data.get("data", data)
            logger.info(f"Used IMEI info block: {info}")

            msg = "✅ *IMEI check complete!*\n\n"
            msg += "📱 *IMEI Info:*\n"
            msg += f"🔹 *IMEI:* {info.get('IMEI', 'N/A')}\n"
            msg += f"🔹 *IMEI2:* {info.get('IMEI2', 'N/A')}\n"
            msg += f"🔹 *MEID:* {info.get('MEID', 'N/A')}\n"
            msg += f"🔹 *Serial:* {info.get('Serial Number', 'N/A')}\n"
            msg += f"🔹 *Desc:* {info.get('Description', 'N/A')}\n"
            msg += f"🔹 *Purchase:* {info.get('Date of purchase', 'N/A')}\n"
            msg += f"🔹 *Coverage:* {info.get('Repairs & Service Coverage', 'N/A')}\n"
            msg += f"🔹 *Replaced:* {info.get('is replaced', 'N/A')}\n"
            msg += f"🔹 *SIM Lock:* {info.get('SIM Lock', 'N/A')}"

            logger.info(f"Final IMEI message: {msg}")
            await application.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Sending result error: {str(e)}")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(send())
        else:
            asyncio.run(send())
    except Exception as e:
        logger.error(f"Fallback loop scheduling failed: {str(e)}")

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")
        update = Update.de_json(update_json, application.bot)

        async def handle():
            await application.initialize()
            await application.process_update(update)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(handle())
        return "OK"
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error", 500

async def set_webhook_async():
    try:
        url = f"{BASE_URL}/{TOKEN}"
        await application.bot.set_webhook(url=url)
        logger.info(f"✅ Webhook set to {url}")
    except Exception as e:
        logger.error(f"Webhook setup failed: {e}")

def set_webhook():
    asyncio.run(set_webhook_async())

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=8080)
