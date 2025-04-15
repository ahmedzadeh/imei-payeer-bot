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

# Railway PostgreSQL connection
DATABASE_URL = "postgresql://postgres:zTFbouZOdiuXYvmBvpTvLLkyJYOORSrN@maglev.proxy.rlwy.net:17420/railway"

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
            # Create payments table if it doesn't exist
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
            
            # Create a statistics table to track usage
            c.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    id SERIAL PRIMARY KEY,
                    event_type TEXT,
                    user_id BIGINT,
                    data JSONB,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            logger.info("PostgreSQL Database initialized on Railway")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        conn.rollback()
    finally:
        release_db_connection(conn)

# Initialize database
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

# Log event to stats table
def log_event(event_type, user_id, data=None):
    if data is None:
        data = {}
    
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO stats (event_type, user_id, data) VALUES (%s, %s, %s::jsonb)",
                (event_type, user_id, psycopg2.extras.Json(data))
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to log event: {e}")
        conn.rollback()
    finally:
        release_db_connection(conn)

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
            
            # Log successful payment
            log_event("payment_success", user_id, {"order_id": order_id, "imei": imei})
            
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
        user_id = update.effective_user.id
        log_event("start_command", user_id)
        await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=main_menu_keyboard())

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        log_event("help_command", user_id)
        
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
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
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
                    
                    c.execute("SELECT SUM(CAST(amount AS DECIMAL)) FROM payments WHERE paid = TRUE")
                    total_revenue = c.fetchone()[0] or 0
                    
                    c.execute("""
                        SELECT DATE(created_at), COUNT(*) 
                        FROM payments 
                        WHERE paid = TRUE 
                        GROUP BY DATE(created_at) 
                        ORDER BY DATE(created_at) DESC 
                        LIMIT 7
                    """)
                    daily_stats = c.fetchall()
                    
                    daily_report = "\n".join([f"• {date.strftime('%Y-%m-%d')}: {count} payments" for date, count in daily_stats])

                msg = (
                    "📊 *Bot Usage Stats:*\n"
                    f"• Total IMEI checks: *{total_requests}*\n"
                    f"• Successful payments: *{total_paid}*\n"
                    f"• Unique users: *{unique_users}*\n"
                    f"• Total revenue: *${total_revenue:.2f} USD*\n\n"
                    f"📅 *Last 7 Days:*\n{daily_report}"
                )

                await update.message.reply_text(msg, parse_mode="Markdown")
            finally:
                release_db_connection(conn)
        except Exception as e:
            logger.error(f"/stats error: {e}")
            logger.error(traceback.format_exc())
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

            # Log IMEI check request
            log_event("imei_check_request", user_id, {"imei": imei})
            
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

@app.route("/admin/dashboard")
def admin_dashboard():
    # Simple admin dashboard - in a real app, add authentication
    try:
        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM payments WHERE paid = TRUE")
            total_paid = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM payments")
            total_requests = c.fetchone()[0]

            c.execute("SELECT COUNT(DISTINCT user_id) FROM payments")
            unique_users = c.fetchone()[0]
            
            c.execute("SELECT SUM(CAST(amount AS DECIMAL)) FROM payments WHERE paid = TRUE")
            total_revenue = c.fetchone()[0] or 0
            
            c.execute("""
                SELECT order_id, user_id, imei, amount, currency, paid, created_at
                FROM payments
                ORDER BY created_at DESC
                LIMIT 50
            """)
            recent_payments = c.fetchall()
        
        release_db_connection(conn)
        
        return render_template(
            "admin_dashboard.html", 
            total_paid=total_paid,
            total_requests=total_requests,
            unique_users=unique_users,
            total_revenue=total_revenue,
            recent_payments=recent_payments
        )
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        return "Error loading dashboard", 500

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
            
            # Log API error
            log_event("api_error", user_id, {"imei": imei, "status_code": res.status_code})
            return
            
        data = res.json()

        if 'error' in data or not any(value for key, value in data.items() if key != 'error'):
            msg = "⚠️ IMEI not found in the database. Please ensure it is correct."
            log_event("imei_not_found", user_id, {"imei": imei})
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
            
            # Log successful IMEI check
            log_event("imei_check_success", user_id, {"imei": imei})

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
        log_event("api_connection_error", user_id, {"imei": imei, "error": str(e)})
    except Exception as e:
        logger.error(f"Sending result error: {str(e)}")
        logger.error(traceback.format_exc())
        error_msg = "❌ An unexpected error occurred. Please contact support."
        try:
            asyncio.run(application.bot.send_message(chat_id=user_id, text=error_msg))
        except:
            logger.error(f"Failed to send error message to user {user_id}")
        log_event("unexpected_error", user_id, {"imei": imei, "error": str(e)})

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

# Create template files if they don't exist
if not os.path.exists('templates/success.html'):
    with open('templates/success.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Payment Successful</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 20px; background-color: #f5f5f5; }
        .container { max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .success { color: #28a745; font-size: 28px; margin: 20px 0; }
        .message { margin: 20px 0; font-size: 18px; color: #333; }
        .icon { font-size: 60px; color: #28a745; }
        .footer { margin-top: 30px; font-size: 14px; color: #777; }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">✅</div>
        <div class="success">Payment Successful!</div>
        <div class="message">Your IMEI check result has been sent to your Telegram chat.</div>
        <div class="message">You can close this window and return to Telegram.</div>
        <div class="footer">Thank you for using our service.</div>
    </div>
</body>
</html>''')

if not os.path.exists('templates/fail.html'):
    with open('templates/fail.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Payment Failed</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 20px; background-color: #f5f5f5; }
        .container { max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .fail { color: #dc3545; font-size: 28px; margin: 20px 0; }
        .message { margin: 20px 0; font-size: 18px; color: #333; }
        .icon { font-size: 60px; color: #dc3545; }
        .footer { margin-top: 30px; font-size: 14px; color: #777; }
        .button { display: inline-block; background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">❌</div>
        <div class="fail">Payment Failed</div>
        <div class="message">{{ message|default("Your payment was not processed successfully.") }}</div>
        <div class="message">Please return to Telegram and try again.</div>
        <a href="https://t.me/your_bot_username" class="button">Return to Telegram</a>
        <div class="footer">If you need assistance, please contact our support.</div>
    </div>
</body>
</html>''')

if not os.path.exists('templates/admin_dashboard.html'):
    with open('templates/admin_dashboard.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Admin Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #333; }
        .stats { display: flex; flex-wrap: wrap; margin-bottom: 30px; }
        .stat-card { flex: 1; min-width: 200px; background-color: #f8f9fa; margin: 10px; padding: 20px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .stat-value { font-size: 24px; font-weight: bold; color: #007bff; }
        .stat-label { color: #6c757d; margin-top: 5px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f8f9fa; color: #333; }
        tr:hover { background-color: #f1f1f1; }
        .status { padding: 5px 10px; border-radius: 3px; font-size: 12px; }
        .paid { background-color: #d4edda; color: #155724; }
        .unpaid { background-color: #f8d7da; color: #721c24; }
        .refresh { float: right; padding: 10px 15px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>IMEI Checker Bot - Admin Dashboard</h1>
        <a href="/admin/dashboard" class="refresh">Refresh</a>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{{ total_requests }}</div>
                <div class="stat-label">Total Requests</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ unique_users }}</div>
                <div class="stat-label">Unique Users</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${{ "%.2f"|format(total_revenue) }}</div>
                <div class="stat-label">Total Revenue</div>
            </div>
        </div>
        
        <h2>Recent Payments</h2>
        <table>
            <thead>
                <tr>
                    <th>Order ID</th>
                    <th>User ID</th>
                    <th>IMEI</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>Date</th>
                </tr>
            </thead>
            <tbody>
                {% for payment in recent_payments %}
                <tr>
                    <td>{{ payment[0] }}</td>
                    <td>{{ payment[1] }}</td>
                    <td>{{ payment[2] }}</td>
                    <td>${{ payment[3] }} {{ payment[4] }}</td>
                    <td>
                        {% if payment[5] %}
                        <span class="status paid">Paid</span>
                        {% else %}
                        <span class="status unpaid">Unpaid</span>
                        {% endif %}
                    </td>
                    <td>{{ payment[6].strftime('%Y-%m-%d %H:%M:%S') }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>''')

if __name__ == "__main__":
    try:
        # Import psycopg2.extras for JSON support
        import psycopg2.extras
        
        # Set webhook
        set_webhook()
        
        # Start Flask app
        app.run(host="0.0.0.0", port=8080)
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Startup error: {e}")
        logger.error(traceback.format_exc())
    finally:
        shutdown_pool()
