import requests
import sqlite3
from flask import Flask, request, render_template_string, jsonify
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
import hashlib
import uuid
import os
import threading
from urllib.parse import urlencode
import base64
import logging
import time
import traceback
import asyncio
from telegram.ext import Application, CommandHandler, ContextTypes

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log")
    ]
)
logger = logging.getLogger(__name__)

# Configuration
TOKEN = os.getenv("TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2210021863")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
ADMIN_CHAT_IDS = [int(os.getenv("ADMIN_CHAT_ID", "6927331058"))]
BASE_URL = os.getenv("BASE_URL", "https://api.imeichecks.online")
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://imeichecks.online")

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"

# Price in USD
PRICE = "0.32"

# Initialize Flask app
app = Flask(__name__)

# Initialize database
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

# Initialize bot
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# Add bot command handlers
def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Hello! Welcome to IMEI Checker Bot. Use /check <imei> to begin.")

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Send /check followed by an IMEI number to start a lookup.")

    async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("❌ Please provide an IMEI number after /check.")
            return

        imei = context.args[0].strip()
        if not imei.isdigit() or len(imei) != 15:
            await update.message.reply_text("❌ Invalid IMEI. It must be 15 digits.")
            return

        order_id = str(uuid.uuid4())

        # Save order in DB
        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("INSERT INTO payments (order_id, user_id, imei, amount, currency, paid) VALUES (?, ?, ?, ?, ?, ?)",
                      (order_id, user_id, imei, PRICE, "USD", False))
            conn.commit()

        # Generate Payeer payment link
        desc = f"IMEI Check for {imei}"
        m_desc = base64.b64encode(desc.encode()).decode()
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("check", check))

register_handlers()

# Telegram webhook endpoint
@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        update = Update.de_json(update_json, bot)

        async def handle():
            await application.initialize()
            await application.process_update(update)

        asyncio.run(handle())

        return "OK"
    except Exception as e:
        logger.error(f"Error processing Telegram update: {str(e)}")
        logger.error(traceback.format_exc())
        return f"Error: {str(e)}", 500

# Set webhook for Telegram
async def set_webhook_async():
    try:
        webhook_url = f"{BASE_URL}/{TOKEN}"
        await bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Failed to set webhook: {str(e)}")
        logger.error(traceback.format_exc())

def set_webhook():
    asyncio.run(set_webhook_async())

if __name__ == "__main__":
    logger.info("Starting Flask app on port 8080")
    set_webhook()
    app.run(host="0.0.0.0", port=8080)
