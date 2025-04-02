import requests
import sqlite3
from flask import Flask, request, render_template_string, jsonify
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import hashlib
import uuid
import asyncio
import os
import threading
from urllib.parse import urlencode
import base64
import logging
import time

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

# Function to send IMEI check results
def send_imei_results(user_id, imei):
    logger.info(f"Sending IMEI results to user {user_id} for IMEI {imei}")
    
    try:
        # Fetch IMEI data
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        response = requests.get(IMEI_API_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        logger.info(f"IMEI API response: {data}")
        
        # Format message
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
        
        # Send message to user
        bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        logger.info(f"✅ Results sent to user {user_id} for IMEI {imei}")
        
        # Notify admin
        for admin_id in ADMIN_CHAT_IDS:
            try:
                bot.send_message(
                    chat_id=admin_id,
                    text=f"✅ Results sent to user {user_id} for IMEI {imei}"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        return True
    except Exception as e:
        error_msg = f"❌ Error fetching IMEI data: {str(e)}"
        logger.error(f"Error sending results to user {user_id}: {str(e)}")
        
        try:
            bot.send_message(chat_id=user_id, text=error_msg)
        except Exception as inner_e:
            logger.error(f"Failed to send error message to user {user_id}: {str(inner_e)}")
        
        # Notify admin about the error
        for admin_id in ADMIN_CHAT_IDS:
            try:
                bot.send_message(
                    chat_id=admin_id,
                    text=f"❌ Error sending results to user {user_id} for IMEI {imei}: {str(e)}"
                )
            except Exception as admin_e:
                logger.error(f"Failed to notify admin {admin_id} about error: {str(admin_e)}")
        
        return False

# Flask routes
@app.route("/")
def index():
    return "IMEI Check Bot is running!"

@app.route("/health")
def health_check():
    return jsonify({"status": "ok", "timestamp": time.time()})

@app.route('/payeer', methods=['POST'])
def payeer_callback():
    data = request.form
    logger.info(f"Received Payeer callback: {data}")
    
    # Validate required fields
    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
        logger.error(f"Missing required fields in Payeer callback: {data}")
        return "Invalid callback data", 400
    
    # Extract data
    m_operation_id = data['m_operation_id']
    m_operation_ps = data.get('m_operation_ps', '')
    m_operation_date = data.get('m_operation_date', '')
    m_operation_pay_date = data.get('m_operation_pay_date', '')
    m_shop = data.get('m_shop', '')
    m_orderid = data['m_orderid']
    m_amount = data['m_amount']
    m_curr = data['m_curr']
    m_status = data['m_status']
    m_sign = data['m_sign']
    
    # Verify signature
    sign_string = f"{m_operation_id}:{m_operation_ps}:{m_operation_date}:{m_operation_pay_date}:{PAYEER_MERCHANT_ID}:{m_orderid}:{m_amount}:{m_curr}:{m_status}:{PAYEER_SECRET_KEY}"
    expected_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()
    
    logger.info(f"Verifying signature: Expected={expected_sign}, Received={m_sign}")
    
    if m_sign != expected_sign:
        logger.error(f"Invalid signature in Payeer callback. Expected: {expected_sign}, Got: {m_sign}")
        return "Invalid signature", 400
    
    if m_status != "success":
        logger.warning(f"Payment not successful. Status: {m_status}")
        return "Payment not successful", 400
    
    # Process payment
    try:
        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, imei FROM payments WHERE order_id = ? AND paid = 0", (m_orderid,))
            result = c.fetchone()
            
            if not result:
                logger.warning(f"Order {m_orderid} not found or already paid")
                return "Order not found or already paid", 400
            
            user_id, imei = result
            logger.info(f"Processing payment for order {m_orderid}, user {user_id}, IMEI {imei}")
            
            # Mark as paid
            c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (m_orderid,))
            conn.commit()
            
            # Send results in a separate thread to avoid blocking
            threading.Thread(target=send_imei_results, args=(int(user_id), imei)).start()
            
            # Notify admin
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    bot.send_message(
                        chat_id=admin_id,
                        text=f"💰 Payment received!\nOrder: {m_orderid}\nUser: {user_id}\nIMEI: {imei}\nAmount: {m_amount} {m_curr}"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin_id}: {e}")
            
            return "OK", 200
    except Exception as e:
        logger.error(f"Error processing payment: {str(e)}")
        return f"Error: {str(e)}", 500

@app.route('/success')
def success():
    m_orderid = request.args.get("m_orderid")
    message = "✅ Payment successful! Your IMEI result will be sent to you in Telegram shortly."
    
    if m_orderid:
        logger.info(f"Success page visited for order {m_orderid}")
        try:
            with sqlite3.connect("payments.db") as conn:
                c = conn.cursor()
                c.execute("SELECT user_id, imei, paid FROM payments WHERE order_id = ?", (m_orderid,))
                result = c.fetchone()
                
                if result:
                    user_id, imei, paid = result
                    if not paid:
                        # This is a manual check - the webhook might not have been called yet
                        logger.info(f"Manual check for order {m_orderid} - not marked as paid yet")
                        message = f"✅ Payment successful! Your IMEI {imei} result will be sent to you in Telegram shortly."
                        
                        # Try to send results anyway (as a backup)
                        threading.Thread(target=send_imei_results, args=(int(user_id), imei)).start()
                        
                        # Mark as paid
                        c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (m_orderid,))
                        conn.commit()
                    else:
                        message = f"✅ Payment already processed! Your IMEI {imei} result has been sent to you in Telegram."
                else:
                    logger.warning(f"Order {m_orderid} not found in database")
        except Exception as e:
            logger.error(f"Error in success page: {str(e)}")
    
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                max-width: 600px;
                margin: 0 auto;
                background-color: white;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            .success {
                color: #4CAF50;
                font-weight: bold;
                font-size: 18px;
            }
            .back-button {
                display: inline-block;
                margin-top: 20px;
                padding: 10px 20px;
                background-color: #4CAF50;
                color: white;
                text-decoration: none;
                border-radius: 5px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Payment Successful</h1>
            <p class="success">{{ message }}</p>
            <a href="https://t.me/your_bot_username" class="back-button">Return to Telegram Bot</a>
        </div>
    </body>
    </html>
    """, message=message)

@app.route('/fail')
def fail():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Failed</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                max-width: 600px;
                margin: 0 auto;
                background-color: white;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            .fail {
                color: #f44336;
                font-weight: bold;
                font-size: 18px;
            }
            .back-button {
                display: inline-block;
                margin-top: 20px;
                padding: 10px 20px;
                background-color: #4CAF50;
                color: white;
                text-decoration: none;
                border-radius: 5px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Payment Failed</h1>
            <p class="fail">Your payment was not successful. Please try again in Telegram.</p>
            <a href="https://t.me/your_bot_username" class="back-button">Return to Telegram Bot</a>
        </div>
    </body>
    </html>
    """)

# Debug routes
@app.route('/debug/payments')
def debug_payments():
    if request.args.get('key') != PAYEER_SECRET_KEY:
        return "Unauthorized", 401
    
    try:
        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 20")
            payments = c.fetchall()
            return jsonify({"payments": payments})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/debug/imei/<imei>')
def debug_imei(imei):
    if request.args.get('key') != PAYEER_SECRET_KEY:
        return "Unauthorized", 401
    
    params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
    try:
        response = requests.get(IMEI_API_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)})

# Telegram bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Start command from user {update.effective_user.id}")
    await update.message.reply_text(
        "👋 Welcome to the IMEI Checker Bot!\n\n"
        "I can check detailed information about any device using its IMEI number.\n\n"
        "To use me, send the /check command followed by a 15-digit IMEI number.\n"
        "Example: `/check 013440001737488`\n\n"
        "A payment of $0.32 USD via Payeer is required for each check.",
        parse_mode="Markdown"
    )

async def check_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Check command from user {update.effective_user.id}")
    
    if not context.args:
        await update.message.reply_text("❌ Please provide an IMEI number.\nExample: `/check 013440001737488`", parse_mode="Markdown")
        return
    
    imei = context.args[0]
    if not imei.isdigit() or len(imei) != 15:
        await update.message.reply_text("❌ Invalid IMEI format. IMEI should be a 15-digit number.", parse_mode="Markdown")
        return
    
    user_id = update.effective_user.id
    order_id = str(uuid.uuid4())
    
    # Create payment record
    try:
        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO payments (order_id, user_id, imei, amount, currency, paid) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, user_id, imei, PRICE, "USD", False)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again later.")
        return
    
    # Generate payment link
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
    logger.info(f"Generated payment URL for order {order_id}: {payment_url}")
    
    keyboard = [[InlineKeyboardButton("💳 Pay $0.32 USD", url=payment_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📱 IMEI: `{imei}`\n\n"
        f"To receive detailed information about this device, please complete the payment.\n\n"
        f"💰 Price: ${PRICE} USD",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 *IMEI Check Bot Help*\n\n"
        "This bot allows you to check detailed information about any device using its IMEI number.\n\n"
        "*Commands:*\n"
        "/start - Start the bot\n"
        "/check IMEI - Check an IMEI number\n"
        "/help - Show this help message\n\n"
        "*How to use:*\n"
        "1. Send /check followed by a 15-digit IMEI number\n"
        "2. Complete the payment ($0.32 USD)\n"
        "3. Receive detailed device information\n\n"
        "*Example:* `/check 013440001737488`",
        parse_mode="Markdown"
    )

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle unknown commands or messages"""
    await update.message.reply_text(
        "I don't understand that command. Please use /help to see available commands."
    )

# Set up webhook for Telegram
@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    """Handle incoming Telegram webhook updates"""
    update_json = request.get_json(force=True)
    logger.info(f"Received Telegram update: {update_json}")
    
    # Process the update asynchronously
    async def process_update():
        update = Update.de_json(update_json, bot)
        await application.process_update(update)
    
    # Run the async function in the event loop
    asyncio.run(process_update())
    return "OK"

# Set up Telegram bot application
async def setup_application():
    """Set up the Telegram bot application"""
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_imei))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown))
    
    # Set webhook
    webhook_url = f"{BASE_URL}/{TOKEN}"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set to {webhook_url}")
    
    return application

# Initialize the application
application = None

def initialize_bot():
    """Initialize the Telegram bot"""
    global application
    
    # Run the async setup in a new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application = loop.run_until_complete(setup_application())
    logger.info("Bot initialized successfully")

# Initialize the bot in a separate thread
threading.Thread(target=initialize_bot).start()

# Cleanup old unpaid orders periodically
def cleanup_old_orders():
    """Remove old unpaid orders from the database"""
    while True:
        try:
            with sqlite3.connect("payments.db") as conn:
                c = conn.cursor()
                # Delete unpaid orders older than 24 hours
                c.execute("DELETE FROM payments WHERE paid = 0 AND datetime(created_at) < datetime('now', '-24 hours')")
                if c.rowcount > 0:
                    logger.info(f"Cleaned up {c.rowcount} old unpaid orders")
                conn.commit()
        except Exception as e:
            logger.error(f"Error cleaning up old orders: {e}")
        
        # Sleep for 1 hour before next cleanup
        time.sleep(3600)

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_orders)
cleanup_thread.daemon = True
cleanup_thread.start()

# Main entry point
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask app on port {port}")
    
    # Run the Flask app
    app.run(host="0.0.0.0", port=port)
