import requests
import sqlite3
from flask import Flask, request, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
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
TOKEN = os.getenv("TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
IMEI_API_KEY = os.getenv("IMEI_API_KEY", "PKZ-HK5K6HMRFAXE5VZLCNW6L")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2210021863")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "11%=2;}-|O@.{QVvXdw~")
BASE_URL = os.getenv("BASE_URL", "https://api.imeichecks.online")
TELEGRAM_PROVIDER_TOKEN = os.getenv("TELEGRAM_PROVIDER_TOKEN", "YOUR_PROVIDER_TOKEN_HERE")

IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"

app = Flask(__name__)

# IMEI validation using Luhn algorithm
def is_valid_imei(imei: str) -> bool:
    if not imei.isdigit() or len(imei) != 15:
        return False
    total = 0
    for i in range(14):
        digit = int(imei[i])
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    check_digit = (10 - (total % 10)) % 10
    return check_digit == int(imei[14])

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
        keyboard = [[KeyboardButton("\U0001F50D Check IMEI")], [KeyboardButton("\u2753 Help")]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("\U0001F44B Welcome! Choose an option:", reply_markup=reply_markup)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("\u2139\ufe0f Use the 'Check IMEI' button and follow instructions to proceed.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text

        if text == "\U0001F50D Check IMEI":
            user_states[user_id] = "awaiting_imei"
            await update.message.reply_text("\U0001F522 Please enter your 15-digit IMEI number.")
        elif text == "\u2753 Help":
            await update.message.reply_text("\u2139\ufe0f Use the 'Check IMEI' button and follow instructions to proceed.")
        elif user_states.get(user_id) == "awaiting_imei":
            imei = text.strip()
            if not is_valid_imei(imei):
                await update.message.reply_text("\u274C Invalid IMEI. Please make sure it’s a real 15-digit IMEI.")
                return

            order_id = str(uuid.uuid4())
            with sqlite3.connect("payments.db") as conn:
                c = conn.cursor()
                c.execute("INSERT INTO payments (order_id, user_id, imei, amount, currency, paid) VALUES (?, ?, ?, ?, ?, ?)",
                          (order_id, user_id, imei, PRICE, "USD", False))
                conn.commit()

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001F4B3 Pay via Payeer", callback_data=f"pay_payeer:{order_id}")],
                [InlineKeyboardButton("\U0001F4B3 Pay via Telegram (Card)", callback_data=f"pay_telegram:{order_id}")]
            ])
            await update.message.reply_text(
                f"\U0001F4F1 IMEI: {imei}\nPlease choose a payment method to continue:",
                reply_markup=keyboard
            )
            user_states[user_id] = None
        else:
            await update.message.reply_text("\u2757 Please use the menu or /start to begin.")

    async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        data = query.data

        if data.startswith("pay_payeer:"):
            order_id = data.split(":")[1]
            with sqlite3.connect("payments.db") as conn:
                c = conn.cursor()
                c.execute("SELECT imei FROM payments WHERE order_id = ?", (order_id,))
                row = c.fetchone()

            if row:
                imei = row[0]
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
                button = InlineKeyboardMarkup([[InlineKeyboardButton("\U0001F4B3 Pay $0.32 USD", url=payment_url)]])
                await query.edit_message_text("\U0001F517 Click the button below to pay via Payeer:", reply_markup=button)

        elif data.startswith("pay_telegram:"):
            order_id = data.split(":")[1]
            prices = [LabeledPrice("IMEI Check", 32)]
            await context.bot.send_invoice(
                chat_id=user_id,
                title="IMEI Check",
                description="Оплата IMEI проверки",
                payload=order_id,
                provider_token=TELEGRAM_PROVIDER_TOKEN,
                currency="USD",
                prices=prices,
                start_parameter="imei-check"
            )

    async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        order_id = update.message.successful_payment.invoice_payload

        with sqlite3.connect("payments.db") as conn:
            c = conn.cursor()
            c.execute("SELECT imei, paid FROM payments WHERE order_id = ?", (order_id,))
            row = c.fetchone()
            if row:
                imei, paid = row
                if not paid:
                    c.execute("UPDATE payments SET paid = 1 WHERE order_id = ?", (order_id,))
                    conn.commit()
                    threading.Thread(target=send_imei_result, args=(user_id, imei)).start()

        await update.message.reply_text("\u2705 Thank you! Payment successful. Your IMEI report is being prepared...")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

register_handlers()

if __name__ == "__main__":
    import threading

    def run_flask():
        app.run(host="0.0.0.0", port=8080)

    def run_webhook():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def set_webhook_async():
            try:
                webhook_url = f"{BASE_URL}/{TOKEN}"
                await application.bot.set_webhook(url=webhook_url)
                logger.info(f"✅ Webhook установлен: {webhook_url}")
            except Exception as e:
                logger.error(f"Webhook Error: {str(e)}")
                logger.error(traceback.format_exc())

        loop.run_until_complete(set_webhook_async())

    threading.Thread(target=run_webhook).start()
    run_flask()

