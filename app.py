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
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"

ADMIN_IDS = {2103379072, 6927331058}

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")  # Set this in Railway using PostgreSQL connection string

# Create and set the event loop
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# Database initialization
def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    order_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    imei TEXT,
                    amount TEXT,
                    currency TEXT,
                    paid BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("PostgreSQL Database initialized")

init_db()

# Bot setup
application = Application.builder().token(TOKEN).build()
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

loop.run_until_complete(application.initialize())  # ✅ Only this

# Main menu keyboard
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]], resize_keyboard=True
    )

# Handlers
def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"Start command received from user {update.effective_user.id}")
        await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=main_menu_keyboard())
        logger.info(f"Response sent to user {update.effective_user.id}")

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[KeyboardButton("🔙 Back")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        help_text = (
            "🆘 *Help & Tutorial*\n\n"
            "Welcome to the IMEI Checker Bot! Here's how to use the service correctly and safely:\n\n"
            "📋 *How to Use:*\n"
            "1. 🔢 Send your 15-digit IMEI number (example: 358792654321789)\n"
            "2. 💳 You'll receive a payment button — click it and complete payment ($0.32)\n"
            "3. 📩 Once payment is confirmed, you will automatically receive your IMEI result\n\n"
            "⚠️ *Important Notes:*\n"
            "- ✅ Always double-check your IMEI before sending.\n"
            "- 🚫 If you enter a wrong IMEI, we are not responsible for incorrect or missing results.\n"
            "- 🔁 No refunds are provided for typos or invalid IMEI numbers.\n"
            "- 🧾 Make sure your IMEI is 15 digits — no spaces or dashes.\n\n"
            "📱 *Sample Result (Preview):*\n\n"
            "✅ Payment successful!\n\n"
            "📱 IMEI Info:\n"
            "🔷 IMEI: 358792654321789\n"
            "🔷 IMEI2: 358792654321796\n"
            "🔷 MEID: 35879265432178\n"
            "🔷 Serial: G7XP91LMN9K\n"
            "🔷 Desc: iPhone 13 Pro Max SILVER 256GB\n"
            "🔷 Purchase: 2022-11-22\n"
            "🔷 Coverage: Active – AppleCare+\n"
            "🔷 Replaced: No\n"
            "🔷 SIM Lock: Unlocked\n\n"
            "⚠️ This is a sample result for demonstration only. Your actual result will depend on the IMEI you submit."
        )

        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=reply_markup)

    async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("🚫 You are not authorized to view stats.")
            return

        try:
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute("SELECT COUNT(*) FROM payments WHERE paid = TRUE")
                    total_paid = c.fetchone()[0]

                    c.execute("SELECT COUNT(*) FROM payments")
                    total_requests = c.fetchone()[0]

                    c.execute("SELECT COUNT(DISTINCT user_id) FROM payments")
                    unique_users = c.fetchone()[0]

            msg = (
                "📊 *Bot Usage Stats:*\n"
                f"• Total IMEI checks: *{total_requests}*\n"
                f"• Successful payments: *{total_paid}*\n"
                f"• Unique users: *{unique_users}*"
            )

            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"/stats error: {e}")
            await update.message.reply_text("❌ Failed to load stats.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        logger.info(f"Text message received: '{text}' from user {user_id}")

        if text == "🔙 Back":
            await update.message.reply_text("🏠 Back to main menu. Please choose an option:", reply_markup=main_menu_keyboard())
        elif text == "🔍 Check IMEI":
            user_states[user_id] = "awaiting_imei"
            await update.message.reply_text("🔢 Please enter your 15-digit IMEI number.")
        elif text == "❓ Help":
            await help_cmd(update, context)
        elif user_states.get(user_id) == "awaiting_imei":
            imei = text.strip()
            if not imei.isdigit() or len(imei) != 15:
                await update.message.reply_text("❌ Invalid IMEI. It must be 15 digits.", reply_markup=main_menu_keyboard())
                return

            order_id = str(uuid.uuid4())
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute(
                        "INSERT INTO payments (order_id, user_id, imei, amount, currency, paid) VALUES (%s, %s, %s, %s, %s, %s)",
                        (order_id, user_id, imei, PRICE, "USD", False)
                    )
                    conn.commit()

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
            user_states[user_id] = None
        else:
            await update.message.reply_text("❗ Please use the menu or /start to begin.", reply_markup=main_menu_keyboard())

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(MessageHandler(filters.TEXT, text_handler))

register_handlers()


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        update = Update.de_json(update_json, application.bot)

        # Just hand off the update to the application
        asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            loop
        )

        return "OK"
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error", 500

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    try:
        form = request.form.to_dict()
        logger.info(f"Received Payeer callback: {form}")

        # Improved error handling for missing fields
        order_id = form.get("m_orderid")
        if not order_id:
            logger.warning("❌ Missing order_id in Payeer callback")
            return "Missing order ID", 400
            
        status = form.get("m_status")
        if status != "success":
            logger.warning(f"❌ Payment status not successful: {status}")
            return "Payment not successful", 400
            
        # Security: Verify the signature
        received_sign = form.get("m_sign")
        if not received_sign:
            logger.warning("❌ Missing signature in Payeer callback")
            return "Missing signature", 400
            
        # Get payment details from database to verify
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT user_id, imei, amount, currency, paid FROM payments WHERE order_id = %s", (order_id,))
                row = c.fetchone()
                if not row:
                    logger.warning(f"❌ Order ID not found: {order_id}")
                    return "Order not found", 404
                    
                user_id, imei, amount, currency, paid = row
                
                # Verify the payment signature
                m_desc = base64.b64encode(f"IMEI Check for {imei}".encode()).decode()
                expected_sign = hashlib.sha256(
                    f"{PAYEER_MERCHANT_ID}:{order_id}:{amount}:{currency}:{m_desc}:{PAYEER_SECRET_KEY}".encode()
                ).hexdigest().upper()
                
                if received_sign != expected_sign:
                    logger.warning("⚠️ Invalid Payeer signature!")
                    return "Invalid signature", 403
                
                # Process the payment if not already paid
                if not paid:
                    c.execute("UPDATE payments SET paid = TRUE WHERE order_id = %s", (order_id,))
                    conn.commit()
                    threading.Thread(target=send_imei_result, args=(user_id, imei)).start()
                    logger.info(f"✅ Payment processed for order: {order_id}")
                else:
                    logger.info(f"ℹ️ Payment already processed for order: {order_id}")
                    
        return "OK"
    except Exception as e:
        logger.error(f"Payeer callback error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error processing payment", 500
        
@app.route("/success")
def success():
    order_id = request.args.get("m_orderid")
    if not order_id:
        logger.warning("❌ Missing order_id in success callback")
        return render_template("fail.html")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT user_id, imei, paid FROM payments WHERE order_id = %s", (order_id,))
                row = c.fetchone()
                if not row:
                    logger.warning(f"❌ Order ID not found in success page: {order_id}")
                    return render_template("fail.html")
                    
                user_id, imei, paid = row
                if not paid:
                    c.execute("UPDATE payments SET paid = TRUE WHERE order_id = %s", (order_id,))
                    conn.commit()
                    threading.Thread(target=send_imei_result, args=(user_id, imei)).start()
                    logger.info(f"✅ Payment marked as successful via success page: {order_id}")
                else:
                    logger.info(f"ℹ️ Payment already processed (success page): {order_id}")
                    
        return render_template("success.html")
    except Exception as e:
        logger.error(f"/success error: {str(e)}")
        logger.error(traceback.format_exc())
        return render_template("fail.html")

@app.route("/fail")
def fail():
    order_id = request.args.get("m_orderid")
    if order_id:
        logger.info(f"❌ Payment failed for order: {order_id}")
    else:
        logger.info("❌ Payment failed (no order ID)")
    return render_template("fail.html")

@app.route("/test_token")
def test_token():
    try:
        future = asyncio.run_coroutine_threadsafe(
            application.bot.get_me(),
            loop
        )
        result = future.result(timeout=5)
        return f"Bot info: {result}"
    except Exception as e:
        logger.error(f"Test token error: {str(e)}")
        logger.error(traceback.format_exc())
        return f"Error: {str(e)}"

@app.route("/test_network")
def test_network():
    try:
        response = requests.get("https://api.telegram.org", timeout=5)
        return f"Network test: {response.status_code}"
    except Exception as e:
        return f"Network error: {str(e)}"

def send_imei_result(user_id, imei):
    try:
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        res = requests.get(IMEI_API_URL, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()

        if 'error' in data or not any(value for key, value in data.items() if key != 'error'):
            msg = "⚠️ IMEI not found in the database. Please ensure it is correct."
            logger.warning(f"⚠️ IMEI not found in database: {imei}")
        else:
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
            logger.info(f"✅ IMEI result sent for: {imei}")

        # Use the global event loop instead of creating a new one
        future = asyncio.run_coroutine_threadsafe(
            application.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown"),
            loop
        )
        # Wait for the result if needed
        future.result(timeout=30)
    except requests.RequestException as e:
        logger.error(f"API request error for IMEI {imei}: {str(e)}")
        # Try to notify the user about the error
        error_msg = "❌ There was an error checking your IMEI. Please try again later or contact support."
        try:
            future = asyncio.run_coroutine_threadsafe(
                application.bot.send_message(chat_id=user_id, text=error_msg),
                loop
            )
            future.result(timeout=10)
        except Exception:
            pass  # If we can't send the error message, just log and continue
    except Exception as e:
        logger.error(f"Sending result error for IMEI {imei}: {str(e)}")
        logger.error(traceback.format_exc())

async def set_webhook_async():
    try:
        webhook_url = f"{BASE_URL}/webhook"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        logger.error(traceback.format_exc())

def set_webhook():
    # Use the existing event loop
    loop.run_until_complete(set_webhook_async())

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=8080)
