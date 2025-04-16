import psycopg2
import psycopg2.extras
import requests
from flask import Flask, request, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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
import time
from psycopg2 import pool
import json
from datetime import datetime, timedelta
import dotenv

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "bot.log", maxBytes=10485760, backupCount=5  # 10MB per file, keep 5 files
        )
    ]
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
TOKEN = os.getenv("TOKEN")
IMEI_API_KEY = os.getenv("IMEI_API_KEY")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:zTFbouZOdiuXYvmBvpTvLLkyJYOORSrN@maglev.proxy.rlwy.net:17420/railway")

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = os.getenv("PRICE", "0.32")

# Convert admin IDs from string to set of integers
ADMIN_IDS = set()
admin_ids_str = os.getenv("ADMIN_IDS", "2103379072,6927331058")
for id_str in admin_ids_str.split(','):
    try:
        ADMIN_IDS.add(int(id_str.strip()))
    except ValueError:
        logger.warning(f"Invalid admin ID: {id_str}")

logger.info(f"Admin IDs configured: {ADMIN_IDS}")

app = Flask(__name__)

# Database connection pool
connection_pool = pool.SimpleConnectionPool(
    minconn=2,  # Minimum connections
    maxconn=20,  # Maximum connections
    dsn=DATABASE_URL
)

def get_db_connection():
    return connection_pool.getconn()

def release_db_connection(conn):
    connection_pool.putconn(conn)

# Database initialization
def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # Create a more comprehensive imei_checks table
            c.execute('''
                CREATE TABLE IF NOT EXISTS imei_checks (
                    id SERIAL PRIMARY KEY,
                    order_id TEXT UNIQUE,
                    imei TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    check_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    imei_found BOOLEAN DEFAULT NULL,
                    payment_status TEXT DEFAULT 'initiated',
                    payment_amount TEXT,
                    payment_currency TEXT DEFAULT 'USD',
                    payeer_client_id TEXT,
                    payeer_client_email TEXT,
                    flow_status TEXT DEFAULT 'imei_submitted',
                    api_response JSONB,
                    notes TEXT
                )
            ''')
            
            # Create indexes for faster lookups
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_user_id ON imei_checks (user_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_imei ON imei_checks (imei)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_order_id ON imei_checks (order_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_check_time ON imei_checks (check_time)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_payment_status ON imei_checks (payment_status)')
            
            conn.commit()
            logger.info("PostgreSQL Database initialized successfully")
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

# IMEI validation function
def validate_imei(imei):
    # Basic validation (length and digits)
    if not imei.isdigit() or len(imei) != 15:
        return False
        
    # Luhn algorithm check (IMEI validation)
    sum = 0
    double = False
    for i in range(len(imei) - 1, -1, -1):
        digit = int(imei[i])
        if double:
            digit *= 2
            if digit > 9:
                digit -= 9
        sum += digit
        double = not double
    return sum % 10 == 0

# Rate limiting function
def is_rate_limited(user_id, limit_seconds=5):
    current_time = time.time()
    if user_id in user_request_times:
        if current_time - user_request_times[user_id] < limit_seconds:
            return True
    user_request_times[user_id] = current_time
    return False

# Update IMEI check record
def update_imei_check(order_id=None, imei=None, user_id=None, **kwargs):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            if order_id:
                # Build the SET part of the query dynamically
                set_parts = []
                params = []
                
                for key, value in kwargs.items():
                    set_parts.append(f"{key} = %s")
                    params.append(value)
                
                if not set_parts:
                    return False
                
                query = f"UPDATE imei_checks SET {', '.join(set_parts)} WHERE order_id = %s"
                params.append(order_id)
                
                c.execute(query, params)
                conn.commit()
                return True
            elif imei and user_id:
                # Find the most recent check for this IMEI and user
                c.execute(
                    "SELECT order_id FROM imei_checks WHERE imei = %s AND user_id = %s ORDER BY check_time DESC LIMIT 1",
                    (imei, user_id)
                )
                result = c.fetchone()
                if result:
                    order_id = result[0]
                    return update_imei_check(order_id=order_id, **kwargs)
            
            return False
    except Exception as e:
        logger.error(f"Error updating IMEI check: {e}")
        conn.rollback()
        return False
    finally:
        release_db_connection(conn)

# Create new IMEI check record
def create_imei_check(order_id, imei, user_id, username=None, first_name=None, last_name=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO imei_checks 
                (order_id, imei, user_id, username, first_name, last_name, payment_amount, flow_status) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (order_id, imei, user_id, username, first_name, last_name, PRICE, 'imei_submitted')
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error creating IMEI check: {e}")
        conn.rollback()
        return False
    finally:
        release_db_connection(conn)

# Process payment function with enhanced tracking
def process_payment(order_id, payeer_client_id=None, payeer_client_email=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # Get the current record
            c.execute(
                "SELECT user_id, imei, payment_status FROM imei_checks WHERE order_id = %s",
                (order_id,)
            )
            row = c.fetchone()
            if not row:
                return None, None, False
                
            user_id, imei, payment_status = row
            
            # Check if already paid
            if payment_status == 'paid':
                return user_id, imei, True  # Already processed
            
            # Update payment information
            update_data = {
                'payment_status': 'paid',
                'flow_status': 'payment_completed',
                'payeer_client_id': payeer_client_id,
                'payeer_client_email': payeer_client_email
            }
            
            set_parts = []
            params = []
            
            for key, value in update_data.items():
                if value is not None:
                    set_parts.append(f"{key} = %s")
                    params.append(value)
            
            if set_parts:
                query = f"UPDATE imei_checks SET {', '.join(set_parts)} WHERE order_id = %s"
                params.append(order_id)
                c.execute(query, params)
                conn.commit()
            
            return user_id, imei, False  # Newly processed
    except Exception as e:
        logger.error(f"Payment processing error: {e}")
        conn.rollback()
        return None, None, False
    finally:
        release_db_connection(conn)

# Main menu keyboard
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]], resize_keyboard=True
    )

# Handlers
def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        welcome_message = (
            f"👋 Welcome {user.first_name}!\n\n"
            "I can help you check IMEI information for your device.\n"
            "Choose an option from the menu below:"
        )
        await update.message.reply_text(welcome_message, reply_markup=main_menu_keyboard())

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
        user_id = update.effective_user.id
        logger.info(f"Stats command called by user {user_id}")
        
        if user_id not in ADMIN_IDS:
            logger.info(f"User {user_id} not in admin list {ADMIN_IDS}")
            await update.message.reply_text("🚫 You are not authorized to view stats.")
            return

        # Send initial message to indicate processing
        processing_msg = await update.message.reply_text("⏳ Fetching statistics...")
        
        try:
            conn = get_db_connection()
            stats_data = {}
            
            try:
                with conn.cursor() as c:
                    # Basic stats
                    try:
                        c.execute("SELECT COUNT(*) FROM imei_checks WHERE payment_status = 'paid'")
                        stats_data['total_paid'] = c.fetchone()[0]
                    except Exception as e:
                        logger.error(f"Error querying total_paid: {e}")
                        stats_data['total_paid'] = "Error"
                    
                    try:
                        c.execute("SELECT COUNT(*) FROM imei_checks")
                        stats_data['total_requests'] = c.fetchone()[0]
                    except Exception as e:
                        logger.error(f"Error querying total_requests: {e}")
                        stats_data['total_requests'] = "Error"
                    
                    try:
                        c.execute("SELECT COUNT(DISTINCT user_id) FROM imei_checks")
                        stats_data['unique_users'] = c.fetchone()[0]
                    except Exception as e:
                        logger.error(f"Error querying unique_users: {e}")
                        stats_data['unique_users'] = "Error"
                    
                    try:
                        c.execute("SELECT SUM(CAST(payment_amount AS DECIMAL)) FROM imei_checks WHERE payment_status = 'paid'")
                        result = c.fetchone()[0]
                        stats_data['total_revenue'] = result if result else 0
                    except Exception as e:
                        logger.error(f"Error querying total_revenue: {e}")
                        stats_data['total_revenue'] = "Error"
                    
                    # Daily stats for the last 7 days
                    try:
                        c.execute("""
                            SELECT DATE(check_time) as date, COUNT(*) as count
                            FROM imei_checks 
                            WHERE payment_status = 'paid' AND check_time >= NOW() - INTERVAL
                            SELECT DATE(check_time) as date, COUNT(*) as count
                            FROM imei_checks 
                            WHERE payment_status = 'paid' AND check_time >= NOW() - INTERVAL '7 days'
                            GROUP BY DATE(check_time) 
                            ORDER BY DATE(check_time) DESC
                        """)
                        stats_data['daily_stats'] = c.fetchall()
                    except Exception as e:
                        logger.error(f"Error querying daily_stats: {e}")
                        stats_data['daily_stats'] = []
                    
                    # Flow status distribution
                    try:
                        c.execute("""
                            SELECT flow_status, COUNT(*) 
                            FROM imei_checks 
                            GROUP BY flow_status
                        """)
                        stats_data['flow_stats'] = c.fetchall()
                    except Exception as e:
                        logger.error(f"Error querying flow_stats: {e}")
                        stats_data['flow_stats'] = []
            finally:
                release_db_connection(conn)
            
            # Format the stats message
            daily_report = "\n".join([f"• {date.strftime('%Y-%m-%d')}: {count} payments" for date, count in stats_data.get('daily_stats', [])])
            if not daily_report:
                daily_report = "• No payments in the last 7 days"
                
            flow_report = "\n".join([f"• {status}: {count} users" for status, count in stats_data.get('flow_stats', [])])
            if not flow_report:
                flow_report = "• No flow status data available"
            
            total_revenue = stats_data.get('total_revenue', 0)
            if isinstance(total_revenue, (int, float)):
                revenue_display = f"${total_revenue:.2f} USD"
            else:
                revenue_display = "Error calculating"

            msg = (
                "📊 *Bot Usage Stats:*\n"
                f"• Total IMEI checks: *{stats_data.get('total_requests', 'Error')}*\n"
                f"• Successful payments: *{stats_data.get('total_paid', 'Error')}*\n"
                f"• Unique users: *{stats_data.get('unique_users', 'Error')}*\n"
                f"• Total revenue: *{revenue_display}*\n\n"
                f"📅 *Last 7 Days:*\n{daily_report}\n\n"
                f"🔄 *User Flow:*\n{flow_report}"
            )

            # Update the processing message with results
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=msg,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"/stats command error: {e}")
            logger.error(traceback.format_exc())
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id,
                text=f"❌ Failed to load stats: {type(e).__name__}\n\nPlease check server logs for details."
            )

    async def db_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("🚫 Not authorized.")
            return
            
        try:
            conn = get_db_connection()
            try:
                with conn.cursor() as c:
                    c.execute("SELECT 1 as test")
                    result = c.fetchone()[0]
                    await update.message.reply_text(f"✅ Database connection successful: {result}")
            finally:
                release_db_connection(conn)
        except Exception as e:
            await update.message.reply_text(f"❌ Database connection failed: {str(e)}")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        user = update.effective_user

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
            
            # Validate IMEI format
            if not imei.isdigit() or len(imei) != 15:
                await update.message.reply_text("❌ Invalid IMEI. It must be 15 digits.", reply_markup=main_menu_keyboard())
                return

            # Optional: Validate IMEI using Luhn algorithm
            if not validate_imei(imei):
                await update.message.reply_text(
                    "⚠️ Warning: This IMEI number appears to be invalid. Please double-check it.\n"
                    "If you're sure it's correct, you can proceed with payment, but we cannot guarantee results.",
                    reply_markup=main_menu_keyboard()
                )
                # Continue anyway, as some users might have valid IMEIs that fail the check

            order_id = str(uuid.uuid4())
            
            # Create IMEI check record with user details
            username = user.username
            first_name = user.first_name
            last_name = user.last_name

            if create_imei_check(order_id, imei, user_id, username, first_name, last_name):
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
                    "m_fail_url": f"{BASE_URL}/fail?m_orderid={order_id}"
                }

                payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay $0.32 USD", url=payment_url)]])
                
                await update.message.reply_text(
                    f"📱 IMEI: {imei}\nTo receive your result, please complete payment:",
                    reply_markup=keyboard
                )
                
                # Update flow status to payment_initiated
                update_imei_check(order_id=order_id, flow_status='payment_initiated')
            else:
                await update.message.reply_text("❌ An error occurred. Please try again later.")
                
            user_states[user_id] = None
        else:
            await update.message.reply_text("❗ Please use the menu or /start to begin.", reply_markup=main_menu_keyboard())

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("dbtest", db_test))
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
        logger.error(f"Webhook error: {str(e)}")
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
            # Update payment status to failed
            update_imei_check(order_id=order_id, payment_status='failed', flow_status='payment_failed')
            logger.warning(f"Payment not successful for order {order_id}")
            return "Payment not successful", 400

        # Extract Payeer client details if available
        payeer_client_id = form.get("client_id", None)
        payeer_client_email = form.get("client_email", None)
        
        user_id, imei, already_processed = process_payment(
            order_id, 
            payeer_client_id=payeer_client_id, 
            payeer_client_email=payeer_client_email
        )
        
        if user_id and imei and not already_processed:
            threading.Thread(target=send_imei_result, args=(user_id, imei, order_id)).start()
            
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
        # Update flow status even if payment is not yet confirmed
        update_imei_check(order_id=order_id, flow_status='payment_page_success')
        
        # Try to process payment if not already processed
        user_id, imei, already_processed = process_payment(order_id)
        
        if user_id and imei and not already_processed:
            threading.Thread(target=send_imei_result, args=(user_id, imei, order_id)).start()
            
        return render_template("success.html")
    except Exception as e:
        logger.error(f"/success error: {e}")
        logger.error(traceback.format_exc())
        return render_template("fail.html", message="An error occurred")

@app.route("/fail")
def fail():
    order_id = request.args.get("m_orderid")
    if order_id:
        # Update flow status to payment_page_failed
        update_imei_check(order_id=order_id, flow_status='payment_page_failed')
    
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
            c.execute("SELECT COUNT(*) FROM imei_checks WHERE payment_status = 'paid'")
            total_paid = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM imei_checks")
            total_requests = c.fetchone()[0]

            c.execute("SELECT COUNT(DISTINCT user_id) FROM imei_checks")
            unique_users = c.fetchone()[0]
            
            c.execute("SELECT SUM(CAST(payment_amount AS DECIMAL)) FROM imei_checks WHERE payment_status = 'paid'")
            total_revenue = c.fetchone()[0] or 0
            
            c.execute("""
                SELECT 
                    id, order_id, imei, user_id, username, check_time, 
                    imei_found, payment_status, payment_amount, 
                    payeer_client_id, payeer_client_email, flow_status
                FROM imei_checks
                ORDER BY check_time DESC
                LIMIT 50
            """)
            recent_checks = c.fetchall()
        
        release_db_connection(conn)
        
        return render_template(
            "admin_dashboard.html", 
            total_paid=total_paid,
            total_requests=total_requests,
            unique_users=unique_users,
            total_revenue=total_revenue,
            recent_checks=recent_checks
        )
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        return "Error loading dashboard", 500

@app.route("/export-data")
def export_data():
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # Get column names
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'imei_checks' ORDER BY ordinal_position")
                headers = [row[0] for row in cursor.fetchall()]
                
                # Get data
                cursor.execute("SELECT * FROM imei_checks")
                rows = cursor.fetchall()
                
                # Create CSV content
                import csv
                import io
                
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(headers)
                writer.writerows(rows)
                
                # Return as downloadable file
                from flask import Response
                
                return Response(
                    output.getvalue(),
                    mimetype="text/csv",
                    headers={"Content-disposition": "attachment; filename=imei_checks_export.csv"}
                )
        finally:
            release_db_connection(conn)
    except Exception as e:
        logger.error(f"Data export error: {e}")
        return f"Error exporting data: {str(e)}", 500

def send_imei_result(user_id, imei, order_id):
    try:
        # Send initial processing message
        processing_msg = asyncio.run(application.bot.send_message(
            chat_id=user_id, 
            text="⏳ Processing your IMEI check. Please wait..."
        ))
        
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        res = requests.get(IMEI_API_URL, params=params, timeout=15)
        
        # More detailed error handling
        if res.status_code != 200:
            logger.error(f"API error: Status {res.status_code}, Response: {res.text}")
            asyncio.run(application.bot.edit_message_text(
                chat_id=user_id, 
                message_id=processing_msg.message_id,
                text="❌ Service temporarily unavailable. Please try again later.",
                parse_mode="Markdown"
            ))
            
            # Update database with API error
            update_imei_check(
                order_id=order_id, 
                flow_status='api_error',
                notes=f"API error: Status {res.status_code}"
            )
            return
            
        data = res.json()
        
        # Store API response in database
        update_imei_check(
            order_id=order_id,
            api_response=psycopg2.extras.Json(data)
        )

        if 'error' in data or not any(value for key, value in data.items() if key != 'error'):
            msg = "⚠️ IMEI not found in the database. Please ensure it is correct."
            
            # Update database with IMEI not found
            update_imei_check(
                order_id=order_id,
                imei_found=False,
                flow_status='imei_not_found'
            )
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
            
            # Update database with IMEI found
            update_imei_check(
                order_id=order_id,
                imei_found=True,
                flow_status='completed_successfully'
            )

        # Edit the processing message with the result
        asyncio.run(application.bot.edit_message_text(
            chat_id=user_id, 
            message_id=processing_msg.message_id,
            text=msg, 
            parse_mode="Markdown"
        ))
        
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
        
        # Update database with API connection error
        update_imei_check(
            order_id=order_id,
            flow_status='api_connection_error',
            notes=str(e)
        )
    except Exception as e:
        logger.error(f"Sending result error: {str(e)}")
        logger.error(traceback.format_exc())
        error_msg = "❌ An unexpected error occurred. Please contact support."
        try:
            asyncio.run(application.bot.send_message(chat_id=user_id, text=error_msg))
        except:
            logger.error(f"Failed to send error message to user {user_id}")
        
        # Update database with unexpected error
        update_imei_check(
            order_id=order_id,
            flow_status='unexpected_error',
            notes=str(e)
        )

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
        table { width: 100%; border-collapse: collapse; margin-top: 20px; overflow-x: auto; display: block; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f8f9fa; color: #333; position: sticky; top: 0; }
        tr:hover { background-color: #f1f1f1; }
        .status { padding: 5px 10px; border-radius: 3px; font-size: 12px; }
        .paid { background-color: #d4edda; color: #155724; }
        .unpaid { background-color: #f8d7da; color: #721c24; }
        .refresh { float: right; padding: 10px 15px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; }
        .export { float: right; padding: 10px 15px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px; margin-right: 10px; }
        .flow-status { font-size: 12px; padding: 3px 6px; border-radius: 3px; background-color: #e9ecef; }
        .completed { background-color: #d4edda; color: #155724; }
        .error { background-color: #f8d7da; color: #721c24; }
        .pending { background-color: #fff3cd; color: #856404; }
        .timestamp { font-size: 12px; color: #6c757d; }
        .search-box { margin: 20px 0; padding: 10px; width: 100%; border: 1px solid #ddd; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>IMEI Checker Bot - Admin Dashboard</h1>
        <a href="/admin/dashboard" class="refresh">Refresh</a>
        <a href="/export-data" class="export">Export Data</a>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{{ total_requests }}</div>
                <div class="stat-label">Total Requests</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ total_paid }}</div>
                <div class="stat-label">Successful Payments</div>
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
        
        <h2>Recent IMEI Checks</h2>
        <input type="text" id="searchInput" class="search-box" placeholder="Search by IMEI, username, or user ID...">
        
        <table>
            <thead>
                <tr>
                    <th>IMEI</th>
                    <th>Time & Date</th>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>IMEI Found</th>
                    <th>Payment Status</th>
                    <th>Payeer Client</th>
                    <th>Flow Status</th>
                </tr>
            </thead>
            <tbody id="checksTable">
                {% for check in recent_checks %}
                <tr>
                    <td>{{ check[2] }}</td>
                    <td><span class="timestamp">{{ check[5].strftime('%Y-%m-%d %H:%M:%S') }}</span></td>
                    <td>{{ check[3] }}</td>
                    <td>{{ check[4] or 'N/A' }}</td>
                    <td>
                        {% if check[6] == True %}
                        <span class="status paid">Found</span>
                        {% elif check[6] == False %}
                        <span class="status unpaid">Not Found</span>
                        {% else %}
                        <span class="status">Unknown</span>
                        {% endif %}
                    </td>
                    <td>
                        {% if check[7] == 'paid' %}
                        <span class="status paid">Paid</span>
                        {% elif check[7] == 'failed' %}
                        <span class="status unpaid">Failed</span>
                        {% else %}
                        <span class="status">{{ check[7] }}</span>
                        {% endif %}
                    </td>
                    <td>
                        {% if check[9] %}
                        ID: {{ check[9] }}<br>
                        {% if check[10] %}
                        Email: {{ check[10] }}
                        {% endif %}
                        {% else %}
                        N/A
                        {% endif %}
                    </td>
                    <td>
                        {% if check[11] == 'completed_successfully' %}
                        <span class="flow-status completed">Completed</span>
                        {% elif check[11] in ['api_error', 'api_connection_error', 'unexpected_error'] %}
                        <span class="flow-status error">{{ check[11] }}</span>
                        {% elif check[11] in ['payment_initiated', 'payment_page_success'] %}
                        <span class="flow-status pending">{{ check[11] }}</span>
                        {% else %}
                        <span class="flow-status">{{ check[11] }}</span>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    
    <script>
        // Simple search functionality
        document.getElementById('searchInput').addEventListener('keyup', function() {
            const searchValue = this.value.toLowerCase();
            const table = document.getElementById('checksTable');
            const rows = table.getElementsByTagName('tr');
            
            for (let i = 0; i < rows.length; i++) {
                const imei = rows[i].cells[0].textContent.toLowerCase();
                const userId = rows[i].cells[2].textContent.toLowerCase();
                const username = rows[i].cells[3].textContent.toLowerCase();
                
                if (imei.includes(searchValue) || userId.includes(searchValue) || username.includes(searchValue)) {
                    rows[i].style.display = '';
                } else {
                    rows[i].style.display = 'none';
                }
            }
        });
    </script>
</body>
</html>''')

if __name__ == "__main__":
    try:
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
