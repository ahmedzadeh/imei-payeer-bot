import requests
import hashlib
import json
import os
import base64
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import asyncio

TOKEN = "8018027330:AAGbqSQ5wQvLj2rPGXQ_MOWU3I8z7iUpjPw"
API_KEY = "PKZ-HK5-K6H-MRF-AXE-5VZ-LCN-W6L"
API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_MERCHANT_ID = "2209595647"
SECRET_KEY = "123"
WEBHOOK_URL = f"https://imei-payeer-bot.onrender.com/{TOKEN}"

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# Simple /start command
async def start(update: Update, context: CallbackContext):
    keyboard = [[KeyboardButton("🔍 Check IMEI")], [KeyboardButton("❓ Help")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("👋 Welcome! Choose an option:", reply_markup=reply_markup)

# Handle /check
async def check(update: Update, context: CallbackContext):
    await update.message.reply_text("This is a placeholder for IMEI check.")

# Add commands
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("check", check))

# Webhook endpoint
@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)

        async def process():
            if not application.running:
                await application.initialize()
            await application.process_update(update)

        asyncio.run(process())
        return "OK", 200
    except Exception as e:
        print("❌ Webhook ERROR:", str(e))
        return "FAIL", 500

# Render health check
@app.route("/")
def home():
    return "✅ Bot is running!"

if __name__ == "__main__":
    asyncio.run(application.bot.set_webhook(url=WEBHOOK_URL))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
