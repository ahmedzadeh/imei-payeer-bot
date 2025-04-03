import requests
import sqlite3
from flask import Flask, request, render_template_string, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

PRICE = "0.32"

app = Flask(__name__)

# Initialize DB
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

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        update = Update.de_json(update_json, application.bot)

        async def handle():
            await application.initialize()
            await application.process_update(update)

        asyncio.run(handle())

        return "OK"
    except Exception as e:
        logger.error(f"Error processing Telegram update: {str(e)}")
        logger.error(traceback.format_exc())
        return f"Error: {str(e)}", 500

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    data = request.form
    logger.info(f"Received Payeer callback: {data}")

    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
        return "Missing fields", 400

    sign_string = f"{data['m_operation_id']}:{data.get('m_operation_ps','')}:{data.get('m_operation_date','')}:{data.get('m_operation_pay_date','')}:{PAYEER_MERCHANT_ID}:{data['m_orderid']}:{data['m_amount']}:{data['m_curr']}:{data['m_status']}:{PAYEER_SECRET_KEY}"
    expected_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    if data['m_sign'] != expected_sign or data['m_status'] != "success":
        return "Invalid signature or status", 400

    with sqlite3.connect("payments.db") as conn:
        c = conn.cursor()
        c.execute("SELECT user_id, imei FROM payments WHERE order_id = ? AND paid = 0", (data['m_orderid'],))
        row = c.fetchone()
        if row:
            user_id, imei = row
            c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (data['m_orderid'],))
            conn.commit()
            threading.Thread(target=send_imei_results, args=(user_id, imei)).start()

    return "OK"

@app.route("/success")
def success():
    order_id = request.args.get("m_orderid")
    message = "✅ Payment received. Your IMEI result will arrive in Telegram shortly."

    if order_id:
        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, imei, paid FROM payments WHERE order_id = ?", (order_id,))
            row = c.fetchone()
            if row:
                user_id, imei, paid = row
                if not paid:
                    c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (order_id,))
                    conn.commit()
                    threading.Thread(target=send_imei_results, args=(user_id, imei)).start()
                    message += f"\nIMEI: {imei}"

    return render_template_string("""
    <html><body><h2>{{ message }}</h2><a href="https://t.me/your_bot_username">Return to bot</a></body></html>
    """, message=message)

@app.route("/fail")
def fail():
    return "❌ Payment failed. Please try again."

# Set webhook for Telegram
async def set_webhook_async():
    try:
        webhook_url = f"{BASE_URL}/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url)
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

def send_imei_results(user_id, imei):
    try:
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        response = requests.get(IMEI_API_URL, params=params, timeout=15)
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
        asyncio.run(application.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown"))
    except Exception as e:
        logger.error(f"Failed to send IMEI result: {str(e)}")
