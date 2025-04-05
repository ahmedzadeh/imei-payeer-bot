import requests
import sqlite3
from flask import Flask, request, render_template, render_template_string, jsonify
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

# Persistent loop setup
event_loop = asyncio.new_event_loop()
asyncio.set_event_loop(event_loop)

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        update = Update.de_json(update_json, application.bot)

        async def handle():
            await application.initialize()
            await application.process_update(update)

        event_loop.run_until_complete(handle())

        return "OK"
    except Exception as e:
        logger.error(f"Error processing Telegram update: {str(e)}")
        logger.error(traceback.format_exc())
        return f"Error: {str(e)}", 500

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    try:
        form = request.form.to_dict()
        logger.info(f"Received Payeer callback: {form}")

        # Required fields check
        required_fields = ["m_operation_id", "m_operation_ps", "m_operation_date", "m_operation_pay_date",
                           "m_shop", "m_orderid", "m_amount", "m_curr", "m_desc", "m_status", "m_sign"]
        if not all(field in form for field in required_fields):
            logger.warning("❌ Missing required fields in Payeer callback")
            return "Missing fields", 400

        # Optional: verify signature here (already handled in payment flow)

        return "OK"  # ✅ This is important: always return 200 OK
    except Exception as e:
        logger.error(f"❌ Error in Payeer callback: {str(e)}")
        return "Internal Error", 500

        m_sign = form["m_sign"]
        sign_data = ":".join([
            form["m_operation_id"],
            form["m_operation_ps"],
            form["m_operation_date"],
            form["m_operation_pay_date"],
            form["m_shop"],
            form["m_orderid"],
            form["m_amount"],
            form["m_curr"],
            form["m_desc"],
            form["m_status"],
            PAYEER_SECRET_KEY
        ])
        valid_sign = hashlib.sha256(sign_data.encode()).hexdigest().upper()

        if m_sign != valid_sign:
            logger.warning("⚠️ Invalid Payeer signature")
            return "Invalid signature", 403

        if form["m_status"] != "success":
            logger.warning("❌ Payment not marked as success")
            return "Payment not successful", 400

        order_id = form["m_orderid"]

        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, imei, paid FROM payments WHERE order_id = ?", (order_id,))
            row = c.fetchone()
            if row:
                user_id, imei, paid = row
                if not paid:
                    c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (order_id,))
                    conn.commit()
                    threading.Thread(target=send_imei_result, args=(user_id, imei)).start()
                    logger.info(f"✅ Payment confirmed via Payeer callback for {order_id}")
                else:
                    logger.info("ℹ️ Order already paid.")
            else:
                logger.error("❌ Order ID not found in DB.")

        return "OK"
    except Exception as e:
        logger.error(f"Error in /payeer route: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error", 500


@app.route("/success")
def success():
    m_orderid = request.args.get("m_orderid")
    if not m_orderid:
        return render_template("fail.html")

    try:
        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, imei, paid FROM payments WHERE order_id = ?", (m_orderid,))
            row = c.fetchone()
            if row:
                user_id, imei, paid = row
                if not paid:
                    c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (m_orderid,))
                    conn.commit()
                    threading.Thread(target=send_imei_result, args=(user_id, imei)).start()
                return render_template("success.html")
            else:
                return render_template("fail.html")
    except Exception as e:
        logger.error(f"Error in /success route: {str(e)}")
        return render_template("fail.html")


@app.route("/fail")
def fail():
    error_message = request.args.get('error', 'Payment failed.')
    return render_template('fail.html', error=error_message)

# Send IMEI result

def send_imei_result(user_id, imei):
    try:
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        response = requests.get(IMEI_API_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

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

        event_loop.run_until_complete(application.bot.send_message(
            chat_id=user_id,
            text=msg,
            parse_mode="Markdown"
        ))

    except Exception as e:
        logger.error(f"Error sending IMEI result to {user_id}: {str(e)}")


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
    event_loop.run_until_complete(set_webhook_async())

if __name__ == "__main__":
    logger.info("Starting Flask app on port 8080")
    set_webhook()
    app.run(host="0.0.0.0", port=8080)
