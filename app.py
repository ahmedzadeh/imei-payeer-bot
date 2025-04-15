import psycopg2
import requests
from flask import Flask, request, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import hashlib
import uuid
import os
import threading
from urllib.parse import urlencode
import base64
import logging
import asyncio
import traceback
from threading import Lock

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")]
)
logger = logging.getLogger(__name__)

# Configuration
TOKEN = os.getenv("TOKEN")
IMEI_API_KEY = os.getenv("IMEI_API_KEY")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# Validate critical configuration
assert TOKEN, "TELEGRAM_TOKEN not set"
assert DATABASE_URL, "DATABASE_URL not set"

# Constants
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"
ADMIN_IDS = {2103379072, 6927331058}

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# Thread-safe state management
user_states = {}
state_lock = Lock()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    order_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    imei TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    paid BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("Database initialized")

init_db()

# Keyboard templates
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]], 
        resize_keyboard=True,
        input_field_placeholder="Choose an option"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"Start command from {user.id}")
    
    await update.message.reply_text(
        "👋 Welcome! I can check iPhone IMEI information. Choose an option:",
        reply_markup=main_menu_keyboard()
    )

async def handle_imei_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    
    if not text.isdigit() or len(text) != 15:
        await update.message.reply_text(
            "❌ Invalid IMEI format. Must be 15 digits.",
            reply_markup=main_menu_keyboard()
        )
        return

    order_id = str(uuid.uuid4())
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    INSERT INTO payments (order_id, user_id, imei, amount, currency)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (order_id, user.id, text, PRICE, "USD"))
                conn.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")
        await update.message.reply_text("⚠️ Service temporary unavailable. Please try later.")
        return

    # Generate Payeer payment URL
    description = f"IMEI Check for {text}"
    m_desc = base64.b64encode(description.encode()).decode()
    sign_str = f"{PAYEER_MERCHANT_ID}:{order_id}:{PRICE}:USD:{m_desc}:{PAYEER_SECRET_KEY}"
    m_sign = hashlib.sha256(sign_str.encode()).hexdigest().upper()
    
    payment_params = {
        "m_shop": PAYEER_MERCHANT_ID,
        "m_orderid": order_id,
        "m_amount": PRICE,
        "m_curr": "USD",
        "m_desc": m_desc,
        "m_sign": m_sign,
        "m_status_url": f"{BASE_URL}/payeer",
        "m_success_url": f"{BASE_URL}/success?order={order_id}",
        "m_fail_url": f"{BASE_URL}/fail"
    }
    
    payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_params)}"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay $0.32 USD", url=payment_url)]])
    
    await update.message.reply_text(
        f"📱 IMEI Received: {text}\nClick below to complete payment:",
        reply_markup=keyboard
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if text == "🔍 Check IMEI":
        with state_lock:
            user_states[user.id] = "awaiting_imei"
        await update.message.reply_text("🔢 Please enter your 15-digit IMEI:")
    elif text == "❓ Help":
        await show_help(update)
    else:
        await update.message.reply_text("Please use the menu buttons to interact with the bot.",
                                      reply_markup=main_menu_keyboard())

async def show_help(update: Update):
    help_text = """
🆘 *Help Guide*

1. Send your 15-digit IMEI
2. Complete the payment
3. Receive detailed report

🔍 Find IMEI: 
• iPhone: Settings → General → About
• Dial *#06# on most devices

⚠️ Note: 
• Double-check IMEI before sending
• No refunds for incorrect IMEIs
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

# Payment processing
@app.route("/payeer", methods=["POST"])
def handle_payeer_callback():
    try:
        data = request.form.to_dict()
        logger.info(f"Payeer callback: {data}")
        
        # Signature verification
        sign_str = f"{data['m_shop']}:{data['m_orderid']}:{data['m_amount']}:{data['m_curr']}:{data['m_desc']}:{PAYEER_SECRET_KEY}"
        expected_sign = hashlib.sha256(sign_str.encode()).hexdigest().upper()
        
        if data["m_sign"] != expected_sign:
            logger.warning("Invalid signature")
            return "Invalid signature", 403

        if data["m_status"] != "success":
            logger.info(f"Payment failed: {data}")
            return "OK"

        # Update payment status
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    UPDATE payments 
                    SET paid = TRUE 
                    WHERE order_id = %s AND paid = FALSE
                    RETURNING user_id, imei
                ''', (data["m_orderid"],))
                result = cursor.fetchone()
                
                if result:
                    user_id, imei = result
                    threading.Thread(target=process_imei_check, args=(user_id, imei)).start()
                    logger.info(f"Payment processed: {data['m_orderid']}")
                conn.commit()

        return "OK"
    except Exception as e:
        logger.error(f"Payment error: {e}")
        return "Server error", 500

def process_imei_check(user_id: int, imei: str):
    try:
        response = requests.get(
            IMEI_API_URL,
            params={"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("error"):
            message = "❌ IMEI not found in database. Please verify the number."
        else:
            message = f"""
✅ *IMEI Report for {imei}*

📱 *Device Info:*
• Model: {data.get('Description', 'N/A')}
• Serial: {data.get('Serial Number', 'N/A')}
• Purchase Date: {data.get('Date of purchase', 'N/A')}
• SIM Lock: {data.get('SIM Lock', 'N/A')}
• Warranty: {data.get('Repairs & Service Coverage', 'N/A')}
"""
        send_telegram_message(user_id, message)
    except Exception as e:
        logger.error(f"IMEI check failed: {e}")
        send_telegram_message(user_id, "⚠️ Service temporary unavailable. Please try again later.")

def send_telegram_message(chat_id: int, text: str):
    async def async_send():
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown"
            )
        except Exception as e:
            if "bot was blocked" in str(e):
                logger.warning(f"User {chat_id} blocked the bot")
            else:
                logger.error(f"Message send error: {e}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_send())
    loop.close()

# Webhook setup
@app.route("/webhook", methods=["POST"])
def webhook_handler():
    update = Update.de_json(request.get_json(), application.bot)
    asyncio.run_coroutine_threadsafe(
        application.process_update(update), 
        application.update_queue.get_loop()
    )
    return "OK"

def setup_handlers():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex(r"^🔍 Check IMEI$"), handle_message))
    application.add_handler(MessageHandler(filters.Regex(r"^❓ Help$"), handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_imei_input))

if __name__ == "__main__":
    setup_handlers()
    
    # Configure webhook
    async def post_init():
        await application.bot.set_webhook(
            url=f"{BASE_URL}/webhook",
            allowed_updates=Update.ALL_TYPES
        )
        logger.info(f"Webhook configured: {BASE_URL}/webhook")

    application.run_webhook(
        listen="0.0.0.0",
        port=8080,
        webhook_url=f"{BASE_URL}/webhook",
        cert_open=True,
        post_init=post_init
    )
