import os
import logging
import asyncio
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
import hashlib
import uuid
import base64
from urllib.parse import urlencode
import requests
import threading
from datetime import datetime
import time

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TOKEN = os.getenv("TOKEN")
IMEI_API_KEY = os.getenv("IMEI_API_KEY")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")

# Constants
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"
ADMIN_IDS = {2103379072, 6927331058}

# Flask app
app = Flask(__name__)

# Bot instance
bot = Bot(token=TOKEN)

# Storage
pending_orders = {}
user_languages = {}
user_states = {}
payment_stats = {"total_requests": 0, "successful_payments": 0, "total_revenue": 0.0}

# Translations
texts = {
    'en': {
        'welcome': "👋 Welcome! Choose an option:",
        'language_selected': "🇬🇧 English language selected.",
        'check_imei': "🔍 Check IMEI",
        'help': "❓ Help",
        'back': "🔙 Back",
        'enter_imei': "🔢 Please enter your 15-digit IMEI number.",
        'invalid_imei': "❌ Invalid IMEI. It must be 15 digits.",
        'payment_prompt': "📱 IMEI: {}\nTo receive your result, please complete payment:",
        'pay_button': "💳 Pay $0.32 USD",
        'choose_language': "Please select your language / Пожалуйста, выберите ваш язык:"
    },
    'ru': {
        'welcome': "👋 Добро пожаловать! Выберите опцию:",
        'language_selected': "🇷🇺 Выбран русский язык.",
        'check_imei': "🔍 Проверить IMEI",
        'help': "❓ Помощь",
        'back': "🔙 Назад",
        'enter_imei': "🔢 Пожалуйста, введите ваш 15-значный номер IMEI.",
        'invalid_imei': "❌ Неверный IMEI. Он должен состоять из 15 цифр.",
        'payment_prompt': "📱 IMEI: {}\nЧтобы получить результат, пожалуйста, выполните оплату:",
        'pay_button': "💳 Оплатить $0.32 USD",
        'choose_language': "Please select your language / Пожалуйста, выберите ваш язык:"
    }
}

def get_text(user_id, key, *args):
    lang = user_languages.get(user_id, 'en')
    text = texts.get(lang, texts['en']).get(key, key)
    return text.format(*args) if args else text

def main_menu_keyboard(user_id):
    return ReplyKeyboardMarkup([
        [KeyboardButton(get_text(user_id, 'check_imei'))],
        [KeyboardButton(get_text(user_id, 'help'))]
    ], resize_keyboard=True)

def language_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
        ]
    ])

# Create application
application = Application.builder().token(TOKEN).build()

# Handlers
async def start(update: Update, context):
    await update.message.reply_text(
        get_text(update.effective_user.id, 'choose_language'),
        reply_markup=language_keyboard()
    )

async def language_callback(update: Update, context):
    query = update.callback_query
    user_id = query.from_user.id
    lang = query.data.split('_')[1]
    
    user_languages[user_id] = lang
    
    await query.answer()
    await query.edit_message_text(text=get_text(user_id, 'language_selected'))
    
    await context.bot.send_message(
        chat_id=user_id,
        text=get_text(user_id, 'welcome'),
        reply_markup=main_menu_keyboard(user_id)
    )

async def text_handler(update: Update, context):
    user_id = update.effective_user.id
    text = update.message.text
    
    if user_id not in user_languages:
        await update.message.reply_text(
            get_text(user_id, 'choose_language'),
            reply_markup=language_keyboard()
        )
        return
    
    if text == get_text(user_id, 'check_imei'):
        user_states[user_id] = "awaiting_imei"
        await update.message.reply_text(get_text(user_id, 'enter_imei'))
    elif user_states.get(user_id) == "awaiting_imei":
        imei = text.strip()
        if not imei.isdigit() or len(imei) != 15:
            await update.message.reply_text(
                get_text(user_id, 'invalid_imei'),
                reply_markup=main_menu_keyboard(user_id)
            )
            return
        
        order_id = str(uuid.uuid4())
        pending_orders[order_id] = {
            'imei': imei,
            'user_id': user_id,
            'timestamp': datetime.now(),
            'status': 'pending'
        }
        
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
            "m_fail_url": f"{BASE_URL}/fail?m_orderid={order_id}"
        }
        
        payment_url = f"{PAYEER_PAYMENT_URL}?{urlencode(payment_data)}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(get_text(user_id, 'pay_button'), url=payment_url)
        ]])
        
        await update.message.reply_text(
            get_text(user_id, 'payment_prompt', imei),
            reply_markup=keyboard
        )
        
        user_states[user_id] = None

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang_"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# Initialize application
async def init_app():
    await application.initialize()
    await bot.set_webhook(url=f"{BASE_URL}/webhook")
    logger.info(f"Webhook set to {BASE_URL}/webhook")

# Run initialization
asyncio.run(init_app())

# Flask routes
@app.route("/")
def home():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        
        # Process in background
        def process():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(application.process_update(update))
            loop.close()
        
        threading.Thread(target=process).start()
        return "OK"
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "Error", 500

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    try:
        form = request.form.to_dict()
        logger.info(f"Payeer callback: {form}")
        
        order_id = form.get("m_orderid")
        if form.get("m_status") == "success" and order_id in pending_orders:
            order = pending_orders[order_id]
            if order['status'] == 'pending':
                order['status'] = 'paid'
                
                # Send result
                def send_result():
                    user_id = order['user_id']
                    imei = order['imei']
                    
                    # Call IMEI API
                    try:
                        res = requests.get(IMEI_API_URL, params={
                            "api_key": IMEI_API_KEY,
                            "checker": "simlock2",
                            "number": imei
                        }, timeout=15)
                        
                        if res.status_code == 200:
                            data = res.json()
                            msg = "✅ Payment successful!\n\n📱 IMEI Info:\n"
                            for key, value in data.items():
                                if value and key != 'error':
                                    msg += f"🔹 {key}: {value}\n"
                        else:
                            msg = "❌ IMEI not found or service error."
                    except:
                        msg = "❌ Service temporarily unavailable."
                    
                    # Send message
                    asyncio.run(bot.send_message(chat_id=user_id, text=msg))
                
                threading.Thread(target=send_result).start()
        
        return order_id or "OK"
    except Exception as e:
        logger.error(f"Payeer error: {e}")
        return "Error", 500

@app.route("/success")
def success():
    return """
    <html>
    <body style="text-align:center; padding:50px; font-family:Arial;">
        <h1 style="color:green;">✅ Payment Successful!</h1>
        <p>Check your Telegram for the IMEI result.</p>
    </body>
    </html>
    """

@app.route("/fail")
def fail():
    return """
    <html>
    <body style="text-align:center; padding:50px; font-family:Arial;">
        <h1 style="color:red;">❌ Payment Failed</h1>
        <p>Please try again.</p>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
