import os
import json
import hashlib
import base64
import requests
from flask import Flask, request, render_template
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackContext, filters
import asyncio

# Environment Variables and Constants
TOKEN = os.getenv("BOT_TOKEN", "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw")
API_KEY = os.getenv("PROIMEI_API_KEY", "PKZ-HK5-K6H-MRF-AXE-5VZ-LCN-W6L")
API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID", "2209595647")
SECRET_KEY = os.getenv("PAYEER_SECRET_KEY", "123")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://imei-payeer-bot.onrender.com")

app = Flask(__name__, template_folder="templates")
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
    m_desc = base64.b64encode(plain_desc.encode()).decode()
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
    await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=reply_markup)

async def help_command(update: Update, context: CallbackContext):
    await update.message.reply_text("Use /check <IMEI> to check your IMEI after payment.")

async def handle_text(update: Update, context: CallbackContext):
    text = update.message.text
    if "Check IMEI" in text:
        await update.message.reply_text("📲 Please enter your 15-digit IMEI using the command:\n/check <IMEI>")
    elif "Help" in text:
        await help_command(update, context)

async def check_imei(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a 15-digit IMEI.")
        return
    imei = context.args[0]
    user_id = update.effective_user.id
    if not has_paid(user_id, imei):
        link = generate_payeer_link(user_id, imei)
        await update.message.reply_text(f"🔐 IMEI check costs $0.32. Pay here:\n{link}")
        return
    try:
        response = requests.get(f"{API_URL}?api_key={API_KEY}&checker=simlock2&number={imei}")
        if response.status_code == 200:
            data = response.json()
            msg = f"IMEI Info:\nIMEI: {data.get('IMEI', 'N/A')}\nModel: {data.get('Description', 'N/A')}"
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("❌ Error fetching IMEI info.")
    except Exception as e:
        await update.message.reply_text(f"❗ Error: {str(e)}")

# Telegram App
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("check", check_imei))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# Flask Routes
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())
    loop.run_until_complete(application.process_update(update))
    return "OK"

@app.route("/success")
def payment_success():
    status = request.args.get("m_status")
    if status == "success":
        return render_template("success.html")
    elif status is None:
        return render_template("no_data.html")
    else:
        return render_template("error.html")

@app.route("/")
def home():
    return "Bot is running."

if __name__ == "__main__":
    asyncio.run(application.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}"))
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
