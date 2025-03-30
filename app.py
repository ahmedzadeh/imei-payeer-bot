import requests
import hashlib
import json
import os
import base64
from flask import Flask, request, abort
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackContext, MessageHandler, filters
import asyncio

TOKEN = os.getenv("BOT_TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
API_KEY = os.getenv("PROIMEI_API_KEY", "PKZ-HK5-K6H-MRF-AXE-5VZ-LCN-W6L")
API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2209595647")
SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://imei-payeer-bot.onrender.com")

app = Flask(__name__)
PAYMENTS_FILE = "payments.json"

# Utilities
def has_paid(user_id, imei):
    if not os.path.exists(PAYMENTS_FILE):
        return False
    with open(PAYMENTS_FILE, "r") as f:
        payments = json.load(f)
    return str(user_id) in payments and imei in payments[str(user_id)]

def save_payment(user_id, imei):
    if os.path.exists(PAYMENTS_FILE):
        with open(PAYMENTS_FILE, "r") as f:
            payments = json.load(f)
    else:
        payments = {}

    user_id_str = str(user_id)
    if user_id_str not in payments:
        payments[user_id_str] = []
    if imei not in payments[user_id_str]:
        payments[user_id_str].append(imei)

    with open(PAYMENTS_FILE, "w") as f:
        json.dump(payments, f)

def generate_payeer_link(user_id, imei):
    m_orderid = f"tg{user_id}_imei{imei}"
    m_amount = "0.32"
    m_curr = "USD"
    plain_desc = "IMEI Check"
    m_desc = base64.b64encode(plain_desc.encode("utf-8", errors="ignore")).decode()

    sign_string = ":".join([
        PAYEER_MERCHANT_ID,
        m_orderid,
        m_amount,
        m_curr,
        m_desc,
        SECRET_KEY
    ])
    m_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    return (
        f"https://payeer.com/merchant/?m_shop={PAYEER_MERCHANT_ID}"
        f"&m_orderid={m_orderid}"
        f"&m_amount={m_amount}"
        f"&m_curr={m_curr}"
        f"&m_desc={m_desc}"
        f"&m_sign={m_sign}"
        f"&lang=en"
    )

# Handlers
async def start(update: Update, context: CallbackContext):
    keyboard = [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"👋 Welcome {update.effective_user.first_name}!\nI can check your IMEI info.\nPlease choose an option below:",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "❓ *Help Menu*\n\nEach IMEI check costs $0.32. You'll be provided a Payeer payment link.\nUse /check <IMEI> to begin.",
        parse_mode="Markdown"
    )

async def check_imei(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("⚠️ Please enter your 15-digit IMEI.")
        return

    imei = context.args[0]
    user_id = update.effective_user.id

    if not has_paid(user_id, imei):
        link = generate_payeer_link(user_id, imei)
        await update.message.reply_text(
            f"🔐 This IMEI check costs $0.32\n💳 Please pay using the link below to continue:\n{link}"
        )
        return

    api_url = f"{API_URL}?api_key={API_KEY}&checker=simlock2&number={imei}"
    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            data = response.json()
            msg = (
                f"📱 *IMEI Information:*\n\n"
                f"🔹 *IMEI 1:* {data.get('IMEI', 'N/A')}\n"
                f"🔹 *IMEI 2:* {data.get('IMEI2', 'N/A')}\n"
                f"🔹 *MEID:* {data.get('MEID', 'N/A')}\n"
                f"🔹 *Serial Number:* {data.get('Serial Number', 'N/A')}\n"
                f"🔹 *Description:* {data.get('Description', 'N/A')}\n"
                f"🔹 *Date of Purchase:* {data.get('Date of purchase', 'N/A')}\n"
                f"🔹 *Repairs & Service Coverage:* {data.get('Repairs & Service Coverage', 'N/A')}\n"
                f"🔹 *Is Replaced:* {data.get('is replaced', 'N/A')}\n"
                f"🔹 *SIM Lock:* {data.get('SIM Lock', 'N/A')}"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Error checking the IMEI.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ An error occurred: {str(e)}")

# Initialize application
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("check", check_imei))
application.add_handler(CommandHandler("help", help_command))

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)

    async def handle():
        if not application.running:
            await application.initialize()
        await application.process_update(update)

    asyncio.run(handle())
    return "OK"

@app.route("/")
def home():
    return "Bot is running."

if __name__ == "__main__":
    asyncio.run(application.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}"))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
