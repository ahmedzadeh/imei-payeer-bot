import requests
import sqlite3
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
TOKEN = os.getenv("TOKEN", "8018027330:AAE6Se5mieBz4YzRESLJRj-5p3M1KHAQ6Go")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2210021863")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
BASE_URL = os.getenv("BASE_URL", "https://api.imeichecks.online")

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"

app = Flask(__name__)

# Database initialization
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

# Bot setup
application = Application.builder().token(TOKEN).build()
user_states = {}

# Handlers
def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=reply_markup)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("ℹ️ Use the 'Check IMEI' button and follow instructions to proceed.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text

        if text == "🔍 Check IMEI":
            user_states[user_id] = "awaiting_imei"
            await update.message.reply_text("🔢 Please enter your 15-digit IMEI number.")
        elif text == "❓ Help":
            await update.message.reply_text("ℹ️ Use the 'Check IMEI' button and follow instructions to proceed.")
        elif user_states.get(user_id) == "awaiting_imei":
            imei = text.strip()
            if not imei.isdigit() or len(imei) != 15:
                await update.message.reply_text("❌ Invalid IMEI. It must be 15 digits.")
                return

            order_id = str(uuid.uuid4())
            with sqlite3.connect("payments.db") as conn:
                c = conn.cursor()
                c.execute("INSERT INTO payments (order_id, user_id, imei, amount, currency, paid) VALUES (?, ?, ?, ?, ?, ?)",
                          (order_id, user_id, imei, PRICE, "USD", False))
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
            await update.message.reply_text("❗ Please use the menu or /start to begin.")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

register_handlers()

@app.route(f"/{TOKEN}", methods=["POST"])
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

        order_id = form.get("m_orderid")
        if form.get("m_status") != "success":
            return "Payment not successful", 400

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
        return "OK"
    except Exception as e:
        logger.error(f"Callback Error: {str(e)}")
        return "Error", 500

@app.route("/success")
def success():
    order_id = request.args.get("m_orderid")
    if not order_id:
        return render_template("fail.html")

    try:
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
        return render_template("success.html")
    except:
        return render_template("fail.html")

@app.route("/fail")
def fail():
    return render_template("fail.html")

def send_imei_result(user_id, imei):
    async def send():
        try:
            print("✅ Sending IMEI result...")
            params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
            res = requests.get(IMEI_API_URL, params=params, timeout=15)
            res.raise_for_status()
            logger.info(f"API raw response: {res.text}")

            data = res.json()
            info = data.get("data", data)  # Use nested data if available

            msg = "✅ *Payment successful!*\n\n"
            msg += "📱 *IMEI Info:*\n"
            msg += f"🔹 *IMEI:* {info.get('IMEI', 'N/A')}\n"
            msg += f"🔹 *IMEI2:* {info.get('IMEI2', 'N/A')}\n"
            msg += f"🔹 *MEID:* {info.get('MEID', 'N/A')}\n"
            msg += f"🔹 *Serial:* {info.get('Serial Number', 'N/A')}\n"
            msg += f"🔹 *Desc:* {info.get('Description', 'N/A')}\n"
            msg += f"🔹 *Purchase:* {info.get('Date of purchase', 'N/A')}\n"
            msg += f"🔹 *Coverage:* {info.get('Repairs & Service Coverage', 'N/A')}\n"
            msg += f"🔹 *Replaced:* {info.get('is replaced', 'N/A')}\n"
            msg += f"🔹 *SIM Lock:* {info.get('SIM Lock', 'N/A')}"

            await application.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Sending result error: {str(e)}")

    try:
        asyncio.run_coroutine_threadsafe(send(), application.loop)
    except Exception as e:
        logger.error(f"Error scheduling coroutine: {str(e)}")

async def set_webhook_async():
    try:
        webhook_url = f"{BASE_URL}/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        logger.error(traceback.format_exc())

def set_webhook():
    asyncio.run(set_webhook_async())

if __name__ == "__main__":
    set_webhook()
    app.run(host="0.0.0.0", port=8080)
