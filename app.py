import requests
import sqlite3
from flask import Flask, request, render_template_string, jsonify
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import hashlib
import uuid
import os
import threading
from urllib.parse import urlencode
import base64
import logging
import time
import traceback

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

# Callback URL checker

def verify_callback_url():
    callback_url = f"{BASE_URL}/payeer"
    try:
        response = requests.get(callback_url, timeout=5)
        if response.status_code == 404:
            logger.info(f"✅ Callback URL {callback_url} appears to be accessible (HTTP 404 is expected)")
        else:
            logger.warning(f"⚠️ Callback URL {callback_url} returned unexpected status: {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Callback URL {callback_url} is not accessible: {str(e)}")
        logger.error("Payments may not be processed correctly!")

# The rest of the code continues below (not shown here for brevity)

if __name__ == "__main__":
    logger.info("Starting Flask app on port 8080")
    set_webhook()
    verify_callback_url()  # ✅ Added callback check
    app.run(host="0.0.0.0", port=8080)
