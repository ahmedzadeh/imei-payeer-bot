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
import time
from psycopg2 import pool

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

# Database connection pool
connection_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL)

def get_db_connection():
    return connection_pool.getconn()

def release_db_connection(conn):
    connection_pool.putconn(conn)

# Database initialization
def init_db():
    conn = get_db_connection()
    try:
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
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        conn.rollback()
    finally:
        release_db_connection(conn)

init_db()

# Bot setup
application = Application.builder().token(TOKEN).build()
user_states = {}
user_request_times = {}

# Rate limiting function
def is_rate_limited(user_id, limit_seconds=5):
    current_time = time.time()
    if user_id in user_request_times:
        if current_time - user_request_times[user_id] < limit_seconds:
            return True
    user_request_times[user_id] = current_time
    return False

# Main menu keyboard
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]], resize_keyboard=True
    )

# Process payment function to avoid duplicate processing
def process_payment(order_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # First check if already paid
            c.execute("SELECT user_id, imei, paid FROM payments WHERE order_id = %s", (order_id,))
            row = c.fetchone()
            if not row:
                return None, None, False
                
            user_id, imei, paid = row
            if paid:
                return user_id, imei, True  # Already processed
                
            # Mark as paid
            c.execute("UPDATE payments SET paid = TRUE WHERE order_id = %s", (order_id,))
            conn.commit()
            return user_id, imei, False  # Newly processed
    except Exception as e:
        logger.error(f"Payment processing error: {e}")
        conn.rollback()
        return None, None, False
    finally:
        release_db_connection(conn)

# Handlers
def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=main_menu_keyboard())

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
            conn = get_db_connection()
            try:
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
            finally:
                release_db_connection(conn)
        except Exception as e:
            logger.error(f"/stats error: {e}")
            await update.message.reply_text("❌ Failed to load stats.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text

        # Rate limiting check
        if is_rate_limited(user_id):
            await update.message.reply_text("⏳ Please wait a moment before sending another message.")
            return

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
            conn = get_db_connection()
            try:
                with conn.cursor() as c:
                    c.execute(
                        "INSERT INTO payments (order_id, user_id, imei, amount, currency, paid) VALUES (%s, %s, %s, %s, %s, %s)",
                        (order_id, user_id, imei, PRICE, "USD", False)
                    )
                    conn.commit()
            except Exception as e:
                logger.error(f"Database error: {e}")
                conn.rollback()
                await update.message.reply_text("❌ An error occurred. Please try again later.")
                return
            finally:
                release_db_connection(conn)

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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

register_handlers()


@app.route("/webhook", methods=["POST"])
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


@app.route("/payeer", methods=["POST"])
def payeer_callback():
    try:
        form = request.form.to_dict()
        logger.info(f"Received Payeer callback: {form}")

        # Verify payment signature
        if "m_sign" in form:
            received_sign = form.get("m_sign")
            sign_string = f"{form.get('m_operation_id')}:{form.get('m_operation_ps')}:{form.get('m_operation_date')}:{form.get('m_operation_pay_date')}:{form.get('m_shop')}:{form.get('m_orderid')}:{form.get('m_amount')}:{form.get('m_curr')}:{PAYEER_SECRET_KEY}"
            expected_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()
            
            if received_sign != expected_sign:
                logger.warning("Invalid payment signature")
                return "Invalid signature", 403

        order_id = form.get("m_orderid")
        if form.get("m_status") != "success":
            logger.warning(f"Payment not successful for order {order_id}")
            return "Payment not successful", 400

        user_id, imei, already_processed = process_payment(order_id)
        
        if user_id and imei and not already_processed:
            threading.Thread(target=send_imei_result, args=(user_id, imei)).start()
            
        return "OK"
    except Exception as e:
        logger.error(f"Payeer callback error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error processing payment", 500
        
@app.route("/success")
def success():
    order_id = request.args.get("m_orderid")
    if not order_id:
        return render_template("fail.html", message="Invalid order ID")

    try:
        user_id, imei, already_processed = process_payment(order_id)
        
        if user_id and imei and not already_processed:
            threading.Thread(target=send_imei_result, args=(user_id, imei)).start()
            
        return render_template("success.html")
    except Exception as e:
        logger.error(f"/success error: {e}")
        logger.error(traceback.format_exc())
        return render_template("fail.html", message="An error occurred")

@app.route("/fail")
def fail():
    return render_template("fail.html", message="Payment was not completed")

@app.route("/health")
def health_check():
    # Simple health check endpoint
    try:
        # Test database connection
        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute("SELECT 1")
        release_db_connection(conn)
        return "OK", 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return "Service Unavailable", 503

def send_imei_result(user_id, imei):
    try:
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        res = requests.get(IMEI_API_URL, params=params, timeout=15)
        
        # More detailed error handling
        if res.status_code != 200:
            logger.error(f"API error: Status {res.status_code}, Response: {res.text}")
            asyncio.run(application.bot.send_message(
                chat_id=user_id, 
                text="❌ Service temporarily unavailable. Please try again later.",
                parse_mode="Markdown"
            ))
            return
            
        data = res.json()

        if 'error' in data or not any(value for key, value in data.items() if key != 'error'):
            msg = "⚠️ IMEI not found in the database. Please ensure it is correct."
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

        asyncio.run(application.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown"))
        
        # Notify admins about successful payment
        admin_msg = f"💰 New payment received!\n👤 User ID: {user_id}\n📱 IMEI: {imei}"
        for admin_id in ADMIN_IDS:
            try:
                asyncio.run(application.bot.send_message(chat_id=admin_id, text=admin_msg))
            except Exception as admin_err:
                logger.error(f"Failed to notify admin {admin_id}: {admin_err}")
                
    except requests.RequestException as e:
        logger.error(f"API request error: {str(e)}")
        error_msg = "❌ Error connecting to IMEI service. Please try again later or contact support."
        asyncio.run(application.bot.send_message(chat_id=user_id, text=error_msg))
    except Exception as e:
        logger.error(f"Sending result error: {str(e)}")
        logger.error(traceback.format_exc())
        error_msg = "❌ An unexpected error occurred. Please contact support."
        try:
            asyncio.run(application.bot.send_message(chat_id=user_id, text=error_msg))
        except:
            logger.error(f"Failed to send error message to user {user_id}")

async def set_webhook_async():
    try:
        webhook_url = f"{BASE_URL}/webhook"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        logger.error(traceback.format_exc())

def set_webhook():
    asyncio.run(set_webhook_async())

# Graceful shutdown
def shutdown_pool():
    if connection_pool:
        connection_pool.closeall()
        logger.info("Database connection pool closed")

# Create templates directory if it doesn't exist
os.makedirs('templates', exist_ok=True)

# Create template files
with open('templates/success.html', 'w') as f:
    f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Payment Successful</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 20px; }
        .success { color: green; font-size: 24px; margin: 20px 0; }
        .message { margin: 20px 0; }
    </style>
</head>
<body>
    <div class="success">✅ Payment Successful!</div>
    <div class="message">Your IMEI check result has been sent to your Telegram chat.</div>
    <div class="message">You can close this window and return to Telegram.</div>
</body>
</html>''')

with open('templates/fail.html', 'w') as f:
    f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Payment Failed</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 20px; }
        .fail { color: red; font-size: 24px; margin: 20px 0; }
        .message { margin: 20px 0; }
    </style>
</head>
<body>
    <div class="fail">❌ Payment Failed</div>
    <div class="message">{{ message|default("Your payment was not processed successfully.") }}</div>
    <div class="message">Please return to Telegram and try again.</div>
</body>
</html>''')

if __name__ == "__main__":
    try:
        set_webhook()
        app.run(host="0.0.0.0", port=8080)
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    finally:
        shutdown_pool()
