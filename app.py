import asyncio
import base64
import hashlib
import logging
import os
import sqlite3
import threading  # Added missing import
import uuid
from datetime import datetime
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, render_template, request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
TOKEN = os.getenv("TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2210021863")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
ADMIN_CHAT_IDS = os.getenv("ADMIN_CHAT_ID", "6927331058").split(",")
BASE_URL = os.getenv("BASE_URL", "https://api.imeichecks.online")
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://imeichecks.online")
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PAYMENT_AMOUNT = "0.32"
PAYMENT_CURRENCY = "USD"

# Conversation states
TYPING_IMEI = 0

# Initialize Flask app
app = Flask(__name__)

# Initialize database
def init_db():
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        # Payments table
        c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            imei TEXT NOT NULL,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL,
            paid INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            payment_date TEXT,
            result_data TEXT
        )
        """)
        
        # Users table
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_at TEXT NOT NULL,
            last_active TEXT NOT NULL
        )
        """)
        
        # Logs table
        c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT
        )
        """)
        conn.commit()

init_db()

# Database helper functions
def log_action(user_id, action, details=None):
    """Log user actions to the database"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute(
            "INSERT INTO logs (timestamp, user_id, action, details) VALUES (?, ?, ?, ?)",
            (timestamp, user_id, action, details)
        )
        conn.commit()

def register_user(user_id, username=None, first_name=None, last_name=None):
    """Register a new user or update existing user info"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        
        # Check if user exists
        c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if c.fetchone():
            # Update existing user
            c.execute(
                "UPDATE users SET username = ?, first_name = ?, last_name = ?, last_active = ? WHERE user_id = ?",
                (username, first_name, last_name, timestamp, user_id)
            )
        else:
            # Insert new user
            c.execute(
                "INSERT INTO users (user_id, username, first_name, last_name, joined_at, last_active) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, first_name, last_name, timestamp, timestamp)
            )
        conn.commit()

def create_payment(user_id, imei, amount, currency):
    """Create a new payment record"""
    order_id = str(uuid.uuid4())
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute(
            "INSERT INTO payments (order_id, user_id, imei, amount, currency, created_at, paid, processed) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
            (order_id, user_id, imei, amount, currency, timestamp)
        )
        conn.commit()
    return order_id

def update_payment_status(order_id, paid=True, result_data=None):
    """Update payment status and store result data if provided"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        payment_date = datetime.now().isoformat() if paid else None
        
        if result_data:
            c.execute(
                "UPDATE payments SET paid = ?, payment_date = ?, result_data = ? WHERE order_id = ?",
                (1 if paid else 0, payment_date, result_data, order_id)
            )
        else:
            c.execute(
                "UPDATE payments SET paid = ?, payment_date = ? WHERE order_id = ?",
                (1 if paid else 0, payment_date, order_id)
            )
        conn.commit()

def mark_payment_processed(order_id):
    """Mark payment as processed"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        c.execute("UPDATE payments SET processed = 1 WHERE order_id = ?", (order_id,))
        conn.commit()

def get_payment_info(order_id):
    """Get payment information by order ID"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT user_id, imei, paid, processed, result_data FROM payments WHERE order_id = ?",
            (order_id,)
        )
        return c.fetchone()

def get_user_payments(user_id):
    """Get all payments for a user"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT order_id, imei, amount, currency, created_at, paid, processed, payment_date, result_data FROM payments WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
        return c.fetchall()

def get_pending_payments():
    """Get all paid but unprocessed payments"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT order_id, user_id, imei FROM payments WHERE paid = 1 AND processed = 0"
        )
        return c.fetchall()

def store_imei_result(order_id, result_data):
    """Store IMEI check result data"""
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE payments SET result_data = ? WHERE order_id = ?",
            (result_data, order_id)
        )
        conn.commit()

# IMEI API functions
def check_imei(imei):
    """Check if IMEI number is valid for lookup"""
    if not imei.isdigit() or len(imei) < 14 or len(imei) > 16:
        return False, "IMEI should be a 14-16 digit number."
    return True, None

def get_imei_info(imei):
    """Get IMEI information from API"""
    try:
        params = {
            'key': IMEI_API_KEY,
            'imei': imei
        }
        response = requests.get(IMEI_API_URL, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"IMEI API error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error checking IMEI: {str(e)}")
        return None

# Payment functions
def generate_payeer_payment_link(order_id, amount, currency, description):
    """Generate Payeer payment link"""
    params = {
        'm_shop': PAYEER_MERCHANT_ID,
        'm_orderid': order_id,
        'm_amount': amount,
        'm_curr': currency,
        'm_desc': base64.b64encode(description.encode()).decode()
    }
    
    # Generate signature
    sign_str = f"{params['m_shop']}:{params['m_orderid']}:{params['m_amount']}:{params['m_curr']}:{params['m_desc']}:{PAYEER_SECRET_KEY}"
    sign = hashlib.sha256(sign_str.encode()).hexdigest().upper()
    params['m_sign'] = sign
    
    # Add success and failure URLs
    params['m_process'] = 'send'
    params['m_success_url'] = f"{WEBSITE_URL}/success"
    params['m_fail_url'] = f"{WEBSITE_URL}/fail"
    
    return f"{PAYEER_PAYMENT_URL}?{urlencode(params)}"

def verify_payeer_signature(data):
    """Verify Payeer callback signature"""
    try:
        m_shop = data.get('m_shop', '')
        m_orderid = data.get('m_orderid', '')
        m_amount = data.get('m_amount', '')
        m_curr = data.get('m_curr', '')
        m_sign = data.get('m_sign', '')
        
        # Generate signature for verification
        sign_str = f"{m_shop}:{m_orderid}:{m_amount}:{m_curr}:{PAYEER_SECRET_KEY}"
        sign = hashlib.sha256(sign_str.encode()).hexdigest().upper()
        
        return sign == m_sign
    except Exception as e:
        logger.error(f"Error verifying Payeer signature: {str(e)}")
        return False

# Initialize bot and application
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# Create a new event loop for async operations
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    user = update.effective_user
    user_id = user.id
    register_user(user_id, user.username, user.first_name, user.last_name)
    log_action(user_id, "start")
    
    welcome_message = (
        f"👋 Hello {user.first_name}!\n\n"
        f"Welcome to the IMEI Checker Bot. I can help you check IMEI details for your device.\n\n"
        f"To start, simply send me an IMEI number or use the /check command."
    )
    
    keyboard = [
        [InlineKeyboardButton("Check IMEI", callback_data="check")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command"""
    user_id = update.effective_user.id
    log_action(user_id, "help")
    
    help_message = (
        "🔍 *IMEI Checker Bot Help*\n\n"
        "Here's how to use this bot:\n\n"
        "1. Send me an IMEI number directly or use /check command\n"
        "2. I'll generate a payment link for you\n"
        "3. After payment, you'll receive detailed information about your device\n\n"
        "Available commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/check - Check a new IMEI\n"
        "/history - View your previous checks\n\n"
        "If you have any questions or issues, please contact our support."
    )
    
    await update.message.reply_text(help_message, parse_mode="Markdown")
    return ConversationHandler.END

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /check command"""
    user_id = update.effective_user.id
    log_action(user_id, "check_command")
    
    await update.message.reply_text(
        "Please send me the IMEI number you want to check.\n"
        "You can find the IMEI by dialing *#06# on your phone."
    )
    return TYPING_IMEI

async def imei_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receiving an IMEI number"""
    user_id = update.effective_user.id
    imei = update.message.text.strip()
    log_action(user_id, "imei_received", imei)
    
    # Validate IMEI
    valid, error_message = check_imei(imei)
    if not valid:
        await update.message.reply_text(f"❌ {error_message} Please try again.")
        return TYPING_IMEI
    
    # Create payment
    order_id = create_payment(user_id, imei, PAYMENT_AMOUNT, PAYMENT_CURRENCY)
    payment_description = f"IMEI Check for {imei}"
    payment_link = generate_payeer_payment_link(order_id, PAYMENT_AMOUNT, PAYMENT_CURRENCY, payment_description)
    
    # Send payment instructions
    message = (
        f"✅ IMEI: {imei} is valid for checking.\n\n"
        f"💰 Price: {PAYMENT_AMOUNT} {PAYMENT_CURRENCY}\n\n"
        f"Please complete the payment to receive your IMEI information."
    )
    
    keyboard = [
        [InlineKeyboardButton("Pay Now", url=payment_link)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup)
    return ConversationHandler.END

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /history command"""
    user_id = update.effective_user.id
    log_action(user_id, "history")
    
    payments = get_user_payments(user_id)
    
    if not payments:
        await update.message.reply_text("You haven't made any IMEI checks yet.")
        return ConversationHandler.END
    
    message = "📜 *Your IMEI Check History*\n\n"
    
    for i, (order_id, imei, amount, currency, created_at, paid, processed, payment_date, result_data) in enumerate(payments[:5], 1):
        status = "✅ Completed" if paid else "⏳ Pending"
        message += (
            f"{i}. IMEI: `{imei}`\n"
            f"   Date: {datetime.fromisoformat(created_at).strftime('%Y-%m-%d %H:%M')}\n"
            f"   Status: {status}\n\n"
        )
    
    if len(payments) > 5:
        message += f"_Showing 5 of {len(payments)} checks._"
    
    await update.message.reply_text(message, parse_mode="Markdown")
    return ConversationHandler.END

async def default_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages that could be IMEI numbers"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # Check if text looks like an IMEI number
    if text.isdigit() and 14 <= len(text) <= 16:
        log_action(user_id, "direct_imei", text)
        # Process as an IMEI number
        return await imei_received(update, context)
    else:
        # Handle as a regular message
        await update.message.reply_text(
            "I'm designed to check IMEI numbers. Please send me a valid IMEI number or use /check command."
        )
        return ConversationHandler.END

# Admin commands
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /stats command for admins"""
    user_id = update.effective_user.id
    
    if str(user_id) not in ADMIN_CHAT_IDS:
        await update.message.reply_text("You don't have permission to use this command.")
        return
    
    with sqlite3.connect("imei_bot.db") as conn:
        c = conn.cursor()
        
        # Get user count
        c.execute("SELECT COUNT(*) FROM users")
        user_count = c.fetchone()[0]
        
        # Get total payment count
        c.execute("SELECT COUNT(*) FROM payments")
        payment_count = c.fetchone()[0]
        
        # Get successful payment count
        c.execute("SELECT COUNT(*) FROM payments WHERE paid = 1")
        successful_count = c.fetchone()[0]
        
        # Get total revenue
        c.execute("SELECT SUM(amount) FROM payments WHERE paid = 1")
        total_revenue = c.fetchone()[0] or 0
        
        # Get recent activity
        c.execute(
            "SELECT timestamp, user_id, action FROM logs ORDER BY timestamp DESC LIMIT 5"
        )
        recent_activity = c.fetchall()
    
    stats_message = (
        "📊 *Bot Statistics*\n\n"
        f"👥 Total Users: {user_count}\n"
        f"🧾 Total Requests: {payment_count}\n"
        f"✅ Successful Payments: {successful_count}\n"
        f"💰 Total Revenue: ${float(total_revenue):.2f}\n\n"
        f"*Recent Activity:*\n"
    )
    
    for timestamp, uid, action in recent_activity:
        dt = datetime.fromisoformat(timestamp).strftime("%m-%d %H:%M")
        stats_message += f"- {dt}: User {uid} - {action}\n"
    
    await update.message.reply_text(stats_message, parse_mode="Markdown")

# Function to send IMEI results to user
async def send_imei_result(user_id, imei, result_data):
    """Send IMEI check results to the user"""
    try:
        # Parse result data (assuming it's stored as JSON string)
        result = result_data
        if isinstance(result_data, str):
            import json
            result = json.loads(result_data)
        
        # Format the message
        message = (
            f"📱 *IMEI Check Results*\n\n"
            f"IMEI: `{imei}`\n\n"
        )
        
        # Add details from the result
        if isinstance(result, dict):
            for key, value in result.items():
                if key != "status" and value:
                    readable_key = key.replace("_", " ").title()
                    message += f"*{readable_key}*: {value}\n"
        else:
            message += f"*Result*: {result}\n"
        
        message += "\nThank you for using our service!"
        
        # Send the message
        await bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode="Markdown"
        )
        
        return True
    except Exception as e:
        logger.error(f"Error sending IMEI result: {str(e)}")
        return False

# Process pending payments
async def process_pending_payments():
    """Process all pending payments that have been paid but not processed"""
    try:
        pending_payments = get_pending_payments()
        
        for order_id, user_id, imei in pending_payments:
            logger.info(f"Processing pending payment for order {order_id}")
            
            # Get IMEI information
            result = get_imei_info(imei)
            if result:
                # Store result and mark as processed
                store_imei_result(order_id, str(result))
                mark_payment_processed(order_id)
                
                # Send result to user
                await send_imei_result(user_id, imei, result)
                
                logger.info(f"Successfully processed order {order_id} for user {user_id}")
            else:
                logger.error(f"Failed to get IMEI information for order {order_id}")
                
    except Exception as e:
        logger.error(f"Error processing pending payments: {str(e)}")

# Scheduled task to process pending payments
async def scheduled_pending_payments(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled task to process pending payments"""
    await process_pending_payments()

# Flask routes
@app.route("/")
def index():
    return render_template("index.html", title="IMEI Checker Bot")

@app.route("/test-webhook")
def test_webhook():
    return jsonify({
        "status": "ok", 
        "message": "Webhook is working!", 
        "timestamp": datetime.now().isoformat()
    })

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update_json = request.get_json(force=True)
    logger.info("Webhook received update: %s", update_json)
    
    async def process_update():
        update = Update.de_json(update_json, bot)
        await application.process_update(update)
    
    future = asyncio.run_coroutine_threadsafe(process_update(), loop)
    try:
        future.result(timeout=60)  # Wait for up to 60 seconds
    except Exception as e:
        logger.error("Error processing update: %s", str(e))
    
    return "OK"

@app.route('/payeer', methods=['POST'])
def payeer_callback():
    """Handle Payeer payment callback"""
    data = request.form.to_dict()
    logger.info("Received Payeer callback: %s", data)
    
    # Verify required fields
    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
        logger.error("Missing required fields in Payeer callback")
        return "Invalid callback data", 400
    
    # Extract data
    m_operation_id = data['m_operation_id']
    m_orderid = data['m_orderid']
    m_amount = data['m_amount']
    m_curr = data['m_curr']
    m_status = data['m_status']
    
    # Verify signature
    if not verify_payeer_signature(data):
        logger.error("Invalid signature in Payeer callback")
        return "Invalid signature", 403
    
    # Check payment status
    if m_status != "success":
        logger.warning(f"Payment {m_orderid} not successful: {m_status}")
        return "Payment not successful", 200
    
    # Get payment info from database
    payment_info = get_payment_info(m_orderid)
    if not payment_info:
        logger.error(f"Payment {m_orderid} not found in database")
        return "Payment not found", 404
    
    user_id, imei, is_paid, is_processed, result_data = payment_info
    
    # Check if payment already processed
    if is_paid and is_processed:
        logger.info(f"Payment {m_orderid} already processed")
        return "Payment already processed", 200
    
    # Update payment status
    update_payment_status(m_orderid, paid=True)
    
    # Process IMEI check
    async def process_payment():
        try:
            # Get IMEI information
            result = get_imei_info(imei)
            if result:
                # Store result and mark as processed
                store_imei_result(m_orderid, str(result))
                mark_payment_processed(m_orderid)
                
                # Send result to user
                await send_imei_result(user_id, imei, result)
                
                logger.info(f"Successfully processed order {m_orderid} for user {user_id}")
            else:
                logger.error(f"Failed to get IMEI information for order {m_orderid}")
        except Exception as e:
            logger.error(f"Error processing payment {m_orderid}: {str(e)}")
    
    # Run the processing in the background
    asyncio.run_coroutine_threadsafe(process_payment(), loop)
    
    return "OK", 200

@app.route('/success')
def success_page():
    return render_template('success.html', title="Payment Successful")

@app.route('/fail')
def fail_page():
    return render_template('fail.html', title="Payment Failed")

# Main function
def main():
    """Start the bot and Flask server"""
    # Set up conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("check", check_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND, default_handler)
        ],
        states={
            TYPING_IMEI: [MessageHandler(filters.TEXT & ~filters.COMMAND, imei_received)]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(conv_handler)
    
    # Add job to process pending payments
    job_queue = application.job_queue
    job_queue.run_repeating(scheduled_pending_payments, interval=60, first=10)
    
    # Start bot
    application.run_polling()

if __name__ == "__main__":
    # Start the background thread for the bot
    bot_thread = threading.Thread(target=main)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Start Flask server
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
