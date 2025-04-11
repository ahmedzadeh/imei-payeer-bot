import hashlib
import uuid
import asyncio
import requests
import logging
import os
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ==== Secure Env Setup ====
TOKEN = os.getenv("TOKEN")
IMEI_API_KEY = os.getenv("IMEI_API_KEY")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")  # example: https://api.imeichecks.online
WEB_URL = os.getenv("WEB_URL")    # example: https://imeichecks.online
PRICE = "0.32"

# ==== Flask + Telegram Init ====
app = Flask(__name__)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
application = Application.builder().token(TOKEN).build()
bot = application.bot

pending_orders = {}

@app.route("/")
def index():
    return "Bot is running."

@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    update_json = request.get_json(force=True)
    update = Update.de_json(update_json, bot)
    loop.run_until_complete(application.process_update(update))
    return "OK"

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    data = request.form
    m_orderid = data.get("m_orderid")
    m_status = data.get("m_status")
    m_sign = data.get("m_sign")

    sign_string = ":".join([
        data.get("m_operation_id", ""),
        data.get("m_operation_ps", ""),
        data.get("m_operation_date", ""),
        data.get("m_operation_pay_date", ""),
        PAYEER_MERCHANT_ID,
        m_orderid,
        data.get("m_amount", ""),
        data.get("m_curr", ""),
        m_status,
        PAYEER_SECRET_KEY
    ])
    expected_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()

    if m_sign == expected_sign and m_status == "success":
        order = pending_orders.get(m_orderid)
        if order and not order["paid"]:
            order["paid"] = True
            loop.create_task(send_results(order["user_id"], order["imei"]))
            return "OK"
    return "FAIL", 400

@app.route("/payment-status")
def payment_status():
    order_id = request.args.get("order_id")
    order = pending_orders.get(order_id)
    return jsonify({"paid": bool(order and order["paid"])})

# ==== Bot Handlers ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send /check <IMEI> to begin.")

async def check_imei(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please enter a 15-digit IMEI.")
        return

    imei = context.args[0]
    if not imei.isdigit() or len(imei) != 15:
        await update.message.reply_text("Invalid IMEI format.")
        return

    user_id = update.effective_user.id
    order_id = str(uuid.uuid4())
    pending_orders[order_id] = {"user_id": user_id, "imei": imei, "paid": False}

    webapp_url = f"{WEB_URL}/pay.html?order_id={order_id}&imei={imei}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay via WebApp", web_app=WebAppInfo(url=webapp_url))]
    ])

    await update.message.reply_text(
        f"📱 IMEI: `{imei}`\n💳 Price: `${PRICE} USD`\n\nPress the button below to pay:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def send_results(user_id: int, imei: str):
    try:
        response = requests.get("https://proimei.info/en/prepaid/api", params={
            "api_key": IMEI_API_KEY,
            "checker": "simlock2",
            "number": imei
        }, timeout=10)

        data = response.json()
        msg = "\n".join([
            "✅ *IMEI Info:*",
            f"IMEI: {data.get('IMEI', 'N/A')}",
            f"IMEI2: {data.get('IMEI2', 'N/A')}",
            f"Serial: {data.get('Serial Number', 'N/A')}",
            f"Purchase: {data.get('Date of purchase', 'N/A')}",
            f"Coverage: {data.get('Repairs & Service Coverage', 'N/A')}",
            f"Replaced: {data.get('is replaced', 'N/A')}",
            f"SIM Lock: {data.get('SIM Lock', 'N/A')}"
        ])
        await bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
    except Exception as e:
        await bot.send_message(chat_id=user_id, text=f"❌ Error: {e}")

# ==== Launch ====
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("check", check_imei))
application.add_handler(MessageHandler(filters.ALL, lambda u, c: None))
loop.run_until_complete(application.initialize())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
