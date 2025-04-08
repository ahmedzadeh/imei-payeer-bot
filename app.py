import requests
import sqlite3
from flask import Flask, request, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
import hashlib
import uuid
import os 
import threading
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
TOKEN = os.getenv("TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
TELEGRAM_PROVIDER_TOKEN = os.getenv("TELEGRAM_PROVIDER_TOKEN", "1877036958:TEST:65538b0a37f9013ba8001b53ca6b4c00176ba816")
BASE_URL = os.getenv("BASE_URL", "https://api.imeichecks.online")

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PRICE = "0.32"

app = Flask(__name__)

# Database initialization
def init_db():
    with sqlite3.connect("payments.db") as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            imei TEXT,
            amount TEXT,
            currency TEXT,
            paid BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        logger.info("Database initialized")

init_db()

# Bot setup
application = Application.builder().token(TOKEN).build()
user_states = {}

def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[KeyboardButton("\U0001F50D Check IMEI")], [KeyboardButton("\u2753 Help")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("\U0001F44B Welcome! Choose an option:", reply_markup=reply_markup)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("\u2139\ufe0f Use the 'Check IMEI' button and follow instructions to proceed.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text

        if text == "\U0001F50D Check IMEI":
            user_states[user_id] = "awaiting_imei"
            await update.message.reply_text("\U0001F522 Please enter your 15-digit IMEI number.")
        elif text == "\u2753 Help":
            await help_cmd(update, context)
        elif user_states.get(user_id) == "awaiting_imei":
            imei = text.strip()
            if not imei.isdigit() or len(imei) != 15:
                await update.message.reply_text("\u274C Invalid IMEI. It must be 15 digits.")
                return

            order_id = str(uuid.uuid4())
            with sqlite3.connect("payments.db") as conn:
                c = conn.cursor()
                c.execute("INSERT INTO payments (order_id, user_id, imei, amount, currency, paid) VALUES (?, ?, ?, ?, ?, ?)",
                          (order_id, user_id, imei, PRICE, "USD", False))
                conn.commit()

            prices = [LabeledPrice("IMEI Check", int(float(PRICE) * 100))]
            await context.bot.send_invoice(
                chat_id=user_id,
                title="IMEI Check",
                description="Payment for IMEI report",
                payload=order_id,
                provider_token=TELEGRAM_PROVIDER_TOKEN,
                currency="USD",
                prices=prices,
                start_parameter="imei-check"
            )
            user_states[user_id] = None
        else:
            await update.message.reply_text("\u2757 Please use the menu or /start to begin.")

    async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        order_id = update.message.successful_payment.invoice_payload

        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("SELECT imei, paid FROM payments WHERE order_id = ?", (order_id,))
            row = c.fetchone()
            if row:
                imei, paid = row
                if not paid:
                    c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (order_id,))
                    conn.commit()
                    threading.Thread(target=send_imei_result, args=(user_id, imei)).start()

        await update.message.reply_text("\u2705 Thank you! Payment successful. Your IMEI report is being prepared...")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

register_handlers()

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        update = Update.de_json(update_json, application.bot)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def handle():
            await application.initialize()
            await application.process_update(update)

        loop.run_until_complete(handle())
        return "OK"
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error", 500

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
            asyncio.run(application.bot.send_message(chat_id=user_id, text="❌ IMEI not found.", parse_mode="Markdown"))
            return

        msg = "✅ *Payment successful!*\n\n"
        msg += "📱 *IMEI Info:*\n"
        msg += f"🔹 *IMEI:* {data.get('IMEI', 'N/A')}\n"
        msg += f"🔹 *IMEI2:* {data.get('IMEI2', 'N/A')}\n"
        msg += f"🔹 *MEID:* {data.get('MEID', 'N/A')}\n"
        msg += f"🔹 *Serial:* {data.get('Serial Number', 'N/A')}\n"
        msg += f"🔹 *Desc:* {data.get('Description', 'N/A')}\n"
        msg += f"🔹 *Purchase:* {data.get('Date of purchase', 'N/A')}\n"
        msg += f"🔹 *Coverage:* {data.get('Repairs & Service Coverage', 'N/A')}\n"
        msg += f"🔹 *Replaced:* {data.get('is replaced', 'N/A')}\n"
        msg += f"🔹 *SIM Lock:* {data.get('SIM Lock', 'N/A')}"

        asyncio.run(application.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown"))
    except Exception as e:
        logger.error(f"Sending result error: {str(e)}")

async def set_webhook_async():
    try:
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
