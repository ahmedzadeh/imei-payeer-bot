import requests
import sqlite3
from flask import Flask, request, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import hashlib
import uuid
import asyncio
import os
from urllib.parse import urlencode, quote_plus
import base64
import logging
from concurrent.futures import ThreadPoolExecutor

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Configuration
TOKEN = os.getenv("TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2210021863")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
ADMIN_CHAT_IDS = [os.getenv("ADMIN_CHAT_ID", "6927331058")]
BASE_URL = "https://api.imeichecks.online"
WEBSITE_URL = "https://imeichecks.online"

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"

app = Flask(__name__)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
executor = ThreadPoolExecutor(max_workers=5)

# Init DB
def init_db():
    conn = sqlite3.connect("payments.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        order_id TEXT PRIMARY KEY,
        user_id INTEGER,
        imei TEXT,
        paid BOOLEAN
    )""")
    conn.commit()
    conn.close()

init_db()

@app.route("/")
def index():
    return "Bot is running via Flask webhook."

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update_json = request.get_json(force=True)
    logger.info("Webhook called with payload: %s", update_json)

    try:
        update = Update.de_json(update_json, bot)

        async def handle():
            await application.process_update(update)

        future = asyncio.run_coroutine_threadsafe(handle(), loop)
        future.result(timeout=10)  # Wait for the result with a timeout
    except Exception as e:
        logger.error("Error processing update: %s", str(e))

    return "OK"

@app.route('/payeer', methods=['POST'])
def payeer_callback():
    data = request.form
    logger.info("Received Payeer callback data: %s", data)

    # Check if all required fields are present
    required_fields = ['m_operation_id', 'm_sign', 'm_orderid', 'm_amount', 'm_curr', 'm_status']
    if not all(field in data for field in required_fields):
        logger.error("Invalid callback data: %s", data)
        return "Invalid callback data", 400

    # Extract data from the callback
    m_operation_id = data['m_operation_id']
    m_operation_ps = data.get('m_operation_ps', '')
    m_operation_date = data.get('m_operation_date', '')
    m_operation_pay_date = data.get('m_operation_pay_date', '')
    m_shop = data.get('m_shop', PAYEER_MERCHANT_ID)  # Use provided shop ID or default
    m_orderid = data['m_orderid']
    m_amount = data['m_amount']
    m_curr = data['m_curr']
    m_desc = data.get('m_desc', '')
    m_status = data['m_status']
    m_sign = data['m_sign']

    # Create signature string according to Payeer documentation
    sign_string = f"{m_shop}:{m_orderid}:{m_amount}:{m_curr}:{m_desc}:{m_status}:{PAYEER_SECRET_KEY}"
    expected_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    logger.info(f"Calculated signature: {expected_sign}")
    logger.info(f"Received signature: {m_sign}")

    if m_sign.upper() == expected_sign and m_status == "success":
        conn = sqlite3.connect("payments.db")
        c = conn.cursor()
        c.execute("SELECT user_id, imei FROM payments WHERE order_id = ? AND paid = 0", (m_orderid,))
        result = c.fetchone()
        if result:
            user_id, imei = result
            c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (m_orderid,))
            conn.commit()
            
            # Create a task to send results
            asyncio.run_coroutine_threadsafe(send_results(user_id, imei), loop)
            logger.info(f"✅ Payment confirmed for IMEI {imei}, sending result to user {user_id}")
        else:
            logger.warning(f"⚠️ Payment callback received but order ID {m_orderid} not found or already paid")
        conn.close()
        return "OK"
    
    logger.warning(f"❌ Signature mismatch or payment not successful! Status: {m_status}")
    return "Payment not verified", 400

@app.route('/success')
def success():
    # Get all parameters from the request
    params = request.args.to_dict()
    m_orderid = params.get("m_orderid")
    m_status = params.get("m_status", "success")  # Default to success for the success page
    
    logger.info(f"/success triggered with params: {params}")
    
    message = "✅ Payment is being processed. Please wait while your IMEI result is being sent to Telegram."

    if m_orderid:
        conn = sqlite3.connect("payments.db")
        c = conn.cursor()
        c.execute("SELECT user_id, imei FROM payments WHERE order_id = ?", (m_orderid,))
        result = c.fetchone()
        
        if result:
            user_id, imei = result
            c.execute("SELECT paid FROM payments WHERE order_id = ?", (m_orderid,))
            paid_result = c.fetchone()
            
            # Only process if not already paid
            if not paid_result or not paid_result[0]:
                c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (m_orderid,))
                conn.commit()
                
                message = f"✅ Payment successful! IMEI `{imei}` result is being sent to Telegram."
                # Use run_coroutine_threadsafe to run the coroutine in the event loop
                asyncio.run_coroutine_threadsafe(send_results(user_id, imei), loop)
                logger.info(f"✅ /success triggered sending result for IMEI {imei} to user {user_id}")
            else:
                message = f"✅ Payment was already processed. IMEI result has been sent to Telegram."
                logger.info(f"Payment for order {m_orderid} was already processed")
        else:
            logger.warning(f"⚠️ /success: Order ID {m_orderid} not found in database")
        conn.close()

    return render_template_string("""<!DOCTYPE html>
<html>
<head>
    <title>Payment Successful</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .success-message {
            color: #4CAF50;
            font-weight: bold;
            font-size: 18px;
            margin: 20px 0;
            padding: 15px;
            background-color: #e8f5e9;
            border-radius: 5px;
            border: 1px solid #a5d6a7;
        }
        .back-button {
            display: inline-block;
            background-color: #4CAF50;
            color: white;
            padding: 10px 20px;
            text-decoration: none;
            border-radius: 5px;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="success-message">{{ message }}</div>
    <a href="https://t.me/IMEIChecksBot" class="back-button">Return to Telegram Bot</a>
</body>
</html>""", message=message)

@app.route('/fail')
def fail():
    return render_template_string("""<!DOCTYPE html>
<html>
<head>
    <title>Payment Failed</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .fail-message {
            color: #f44336;
            font-weight: bold;
            font-size: 18px;
            margin: 20px 0;
            padding: 15px;
            background-color: #ffebee;
            border-radius: 5px;
            border: 1px solid #ef9a9a;
        }
        .back-button {
            display: inline-block;
            background-color: #f44336;
            color: white;
            padding: 10px 20px;
            text-decoration: none;
            border-radius: 5px;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="fail-message">Payment failed. Please try again.</div>
    <a href="https://t.me/IMEIChecksBot" class="back-button">Return to Telegram Bot</a>
</body>
</html>""")

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/start handler triggered by user %s", update.effective_user.id)
    try:
        await update.message.reply_text(
            "👋 Welcome to the IMEI Checker Bot!\n"
            "Send /check followed by a 15-digit IMEI number.\n"
            "Example: `/check 013440001737488`\n"
            "Payment of $0.32 USD via Payeer is required.\n"
            f"Visit our website: {WEBSITE_URL}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error("Error in /start: %s", str(e))

async def check_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please provide a 15-digit IMEI.", parse_mode="Markdown")
        return

    imei = context.args[0]
    if not imei.isdigit() or len(imei) != 15:
        await update.message.reply_text("Invalid IMEI. Please provide a 15-digit number.", parse_mode="Markdown")
        return

    user_id = update.message.from_user.id

    # First, try to fetch result
    params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
    try:
        response = requests.get(IMEI_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # If successful, tell user result is ready but requires payment
        msg_preview = f"📱 IMEI {imei} result is ready.\n💳 To view full details, please make a $0.32 payment."
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to check IMEI: {e}")
        return

    order_id = str(uuid.uuid4())
    amount = "0.32"

    conn = sqlite3.connect("payments.db")
    c = conn.cursor()
    c.execute("INSERT INTO payments (order_id, user_id, imei, paid) VALUES (?, ?, ?, ?)", (order_id, user_id, imei, False))
    conn.commit()
    conn.close()

    desc = f"IMEI Check for {imei}"
    m_desc = base64.b64encode(desc.encode()).decode().strip()
    sign_string = f"{PAYEER_MERCHANT_ID}:{order_id}:{amount}:USD:{m_desc}:{PAYEER_SECRET_KEY}"
    m_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    payment_data = {
        "m_shop": PAYEER_MERCHANT_ID,
        "m_orderid": order_id,
        "m_amount": amount,
        "m_curr": "USD",
        "m_desc": m_desc,
        "m_sign": m_sign,
        "m_status_url": f"{BASE_URL}/payeer",
        "m_success_url": f"{BASE_URL}/success?m_orderid={order_id}",
        "m_fail_url": f"{BASE_URL}/fail",
        "lang": "en"
    }

    payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"
    logger.info("Generated Payeer payment URL: %s", payment_url)

    keyboard = [[InlineKeyboardButton("💳 Pay $0.32 USD", url=payment_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        msg_preview,
        reply_markup=reply_markup
    )

async def send_results(user_id: int, imei: str):
    logger.info(f"Sending results for IMEI {imei} to user {user_id}")
    params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
    try:
        response = requests.get(IMEI_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        logger.info(f"API response for IMEI {imei}: {data}")

        # Format the message with the data
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

        # Send the message to the user
        await bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        logger.info(f"Results sent successfully to user {user_id}")
        
        # Also notify admin
        for admin_id in ADMIN_CHAT_IDS:
            await bot.send_message(
                chat_id=admin_id, 
                text=f"✅ Payment completed and results sent to user {user_id} for IMEI {imei}"
            )
            
    except Exception as e:
        error_msg = f"❌ Failed to fetch IMEI data: {str(e)}"
        logger.error(f"Error sending results: {error_msg}")
        await bot.send_message(chat_id=user_id, text=error_msg)
        
        # Notify admin about the error
        for admin_id in ADMIN_CHAT_IDS:
            await bot.send_message(
                chat_id=admin_id, 
                text=f"❌ Error sending results to user {user_id} for IMEI {imei}: {str(e)}"
            )

# Add a handler for manual result sending (admin only)
async def admin_send_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Check if user is admin
    if str(user_id) not in ADMIN_CHAT_IDS:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return
    
    # Check if command has correct format
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /send_result <user_id> <imei>")
        return
    
    try:
        target_user_id = int(context.args[0])
        imei = context.args[1]
        
        # Validate IMEI
        if not imei.isdigit() or len(imei) != 15:
            await update.message.reply_text("Invalid IMEI. Please provide a 15-digit number.")
            return
        
        await update.message.reply_text(f"Sending results for IMEI {imei} to user {target_user_id}...")
        await send_results(target_user_id, imei)
        await update.message.reply_text(f"✅ Results sent to user {target_user_id}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

# Add a handler for checking payment status
async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Check if command has correct format
    if not context.args:
        await update.message.reply_text("Usage: /payment <imei>")
        return
    
    imei = context.args[0]
    
    # Validate IMEI
    if not imei.isdigit() or len(imei) != 15:
        await update.message.reply_text("Invalid IMEI. Please provide a 15-digit number.")
        return
    
    conn = sqlite3.connect("payments.db")
    c = conn.cursor()
    c.execute("SELECT order_id, paid FROM payments WHERE user_id = ? AND imei = ? ORDER BY rowid DESC LIMIT 1", (user_id, imei))
    result = c.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text(f"No payment found for IMEI {imei}. Please use /check {imei} to create a payment.")
        return
    
    order_id, paid = result
    
    if paid:
        await update.message.reply_text(f"✅ Payment for IMEI {imei} has been completed. If you haven't received your results, use /support to contact an admin.")
    else:
        # Generate a new payment link
        amount = "0.32"
        desc = f"IMEI Check for {imei}"
        m_desc = base64.b64encode(desc.encode()).decode().strip()
        sign_string = f"{PAYEER_MERCHANT_ID}:{order_id}:{amount}:USD:{m_desc}:{PAYEER_SECRET_KEY}"
        m_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

        payment_data = {
            "m_shop": PAYEER_MERCHANT_ID,
            "m_orderid": order_id,
            "m_amount": amount,
            "m_curr": "USD",
            "m_desc": m_desc,
            "m_sign": m_sign,
            "m_status_url": f"{BASE_URL}/payeer",
            "m_success_url": f"{BASE_URL}/success?m_orderid={order_id}",
            "m_fail_url": f"{BASE_URL}/fail",
            "lang": "en"
        }

        payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"
        
        keyboard = [[InlineKeyboardButton("💳 Pay $0.32 USD", url=payment_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"⏳ Payment for IMEI {imei} is pending. Please complete the payment:",
            reply_markup=reply_markup
        )

# Add a support command
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    
    await update.message.reply_text("Your support request has been sent to the admin. Please wait for a response.")
    
    # Notify admin
    for admin_id in ADMIN_CHAT_IDS:
        await bot.send_message(
            chat_id=admin_id,
            text=f"📞 Support request from user {user_id} (@{username}). Use /reply {user_id} <message> to respond."
        )

# Add a reply command for admins
async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Check if user is admin
    if str(user_id) not in ADMIN_CHAT_IDS:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return
    
    # Check if command has correct format
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply <user_id> <message>")
        return
    
    try:
        target_user_id = int(context.args[0])
        message = " ".join(context.args[1:])
        
        await bot.send_message(
            chat_id=target_user_id,
            text=f"📩 Admin response: {message}"
        )
        
        await update.message.reply_text(f"✅ Reply sent to user {target_user_id}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

# Telegram App Init
application = Application.builder().token(TOKEN).build()
bot = application.bot
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("check", check_imei))
application.add_handler(CommandHandler("send_result", admin_send_result))
application.add_handler(CommandHandler("payment", check_payment))
application.add_handler(CommandHandler("support", support))
application.add_handler(CommandHandler("reply", admin_reply))
application.add_handler(MessageHandler(filters.ALL, lambda u, c: logger.info("Caught unmatched update from user: %s", u.effective_user.id)))
loop.run_until_complete(application.initialize())

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting Flask app on port %s", port)
    app.run(host="0.0.0.0", port=port)
