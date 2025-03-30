import os
import json
import base64
import hashlib
import requests
from flask import Flask, request
from telegram import Update, Bot, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
API_KEY = os.getenv("PROIMEI_API_KEY", "PKZ-HK5-K6H-MRF-AXE-5VZ-LCN-W6L")
API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2209595647")
SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
PAYMENTS_FILE = "payments.json"

bot = Bot(token=TOKEN)
app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

def has_paid(user_id, imei):
    if not os.path.exists(PAYMENTS_FILE):
        return False
    with open(PAYMENTS_FILE, "r") as f:
        data = json.load(f)
    return str(user_id) in data and imei in data[str(user_id)]

def generate_payment_link(user_id, imei):
    m_orderid = f"tg{user_id}_imei{imei}"
    m_amount = "0.32"
    m_curr = "USD"
    m_desc = base64.b64encode("IMEI_Check".encode()).decode()

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
        f"&m_orderid={m_orderid}&m_amount={m_amount}&m_curr={m_curr}"
        f"&m_desc={m_desc}&m_sign={m_sign}&lang=en"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("🔍 Check IMEI")]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        f"👋 Welcome {update.effective_user.first_name}!\nI can check your IMEI info.\nPlease choose an option below:",
        reply_markup=keyboard
    )

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 Please enter your IMEI like this: /check 123456789012345")
        return

    imei = context.args[0]
    user_id = update.effective_user.id

    if not has_paid(user_id, imei):
        link = generate_payment_link(user_id, imei)
        await update.message.reply_text(f"💳 This IMEI check costs $0.32\nPlease pay using the link below:\n{link}")
        return

    api_url = f"{API_URL}?api_key={API_KEY}&checker=simlock2&number={imei}"
    response = requests.get(api_url)
    if response.ok:
        data = response.json()
        msg = (
            f"📱 *IMEI Info:*\n"
            f"🔹 IMEI: {data.get('IMEI', 'N/A')}\n"
            f"🔹 Description: {data.get('Description', 'N/A')}\n"
            f"🔹 Purchase Date: {data.get('Date of purchase', 'N/A')}\n"
            f"🔹 SIM Lock: {data.get('SIM Lock', 'N/A')}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Failed to get IMEI data.")

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("check", check))

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.update_queue.put_nowait(update)
    return "OK"

@app.route("/")
def index():
    return "Bot is running."

if __name__ == "__main__":
    # If you want to test locally
    application.run_polling()
