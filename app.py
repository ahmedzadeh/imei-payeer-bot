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
from telegram.ext import Application

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
