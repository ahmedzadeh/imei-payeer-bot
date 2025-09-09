import os
import logging
from flask import Flask, request, jsonify
import telegram
import hashlib
import uuid
import base64
from urllib.parse import urlencode
import requests
from datetime import datetime
import json

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

# Bot instance (synchronous)
bot = telegram.Bot(token=TOKEN)

# Storage
pending_orders = {}
user_languages = {}
user_states = {}

# Set webhook
try:
    bot.set_webhook(url=f"{BASE_URL}/webhook")
    logger.info(f"Webhook set to {BASE_URL}/webhook")
except Exception as e:
    logger.error(f"Failed to set webhook: {e}")

# Translations
texts = {
    'en': {
        'welcome': "👋 Welcome! Choose an option:",
        'language_selected': "🇬🇧 English language selected.",
        'check_imei': "🔍 Check IMEI",
        'help': "❓ Help",
        'enter_imei': "🔢 Please enter your 15-digit IMEI number.",
        'invalid_imei': "❌ Invalid IMEI. It must be 15 digits.",
        'payment_prompt': "📱 IMEI: {}\nTo receive your result, please complete payment:",
        'pay_button': "💳 Pay $0.32 USD",
        'choose_language': "Please select your language / Пожалуйста, выберите ваш язык:",
        'help_text': "📋 How to use:\n1. Send your 15-digit IMEI\n2. Click payment button\n3. Get your result\n\n⚠️ No refunds for wrong IMEI numbers!"
    },
    'ru': {
        'welcome': "👋 Добро пожаловать! Выберите опцию:",
        'language_selected': "🇷🇺 Выбран русский язык.",
        'check_imei': "🔍 Проверить IMEI",
        'help': "❓ Помощь",
        'enter_imei': "🔢 Пожалуйста, введите ваш 15-значный номер IMEI.",
        'invalid_imei': "❌ Неверный IMEI. Он должен состоять из 15 цифр.",
        'payment_prompt': "📱 IMEI: {}\nЧтобы получить результат, пожалуйста, выполните оплату:",
        'pay_button': "💳 Оплатить $0.32 USD",
        'choose_language': "Please select your language / Пожалуйста, выберите ваш язык:",
        'help_text': "📋 Как использовать:\n1. Отправьте 15-значный IMEI\n2. Нажмите кнопку оплаты\n3. Получите результат\n\n⚠️ Возврат за неверный IMEI не предоставляется!"
    }
}

def get_text(user_id, key, *args):
    lang = user_languages.get(user_id, 'en')
    text = texts.get(lang, texts['en']).get(key, key)
    return text.format(*args) if args else text

def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    try:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.error(f"Failed to send message: {e}")

def handle_start(chat_id):
    keyboard = telegram.InlineKeyboardMarkup([
        [
            telegram.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            telegram.InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
        ]
    ])
    send_message(chat_id, texts['en']['choose_language'], reply_markup=keyboard)

def handle_language_selection(chat_id, language):
    user_languages[chat_id] = language
    
    keyboard = telegram.ReplyKeyboardMarkup([
        [telegram.KeyboardButton(get_text(chat_id, 'check_imei'))],
        [telegram.KeyboardButton(get_text(chat_id, 'help'))]
    ], resize_keyboard=True)
    
    send_message(chat_id, get_text(chat_id, 'language_selected'))
    send_message(chat_id, get_text(chat_id, 'welcome'), reply_markup=keyboard)

def handle_text(chat_id, text):
    if chat_id not in user_languages:
        handle_start(chat_id)
        return
    
    if text == get_text(chat_id, 'check_imei'):
        user_states[chat_id] = "awaiting_imei"
        send_message(chat_id, get_text(chat_id, 'enter_imei'))
    
    elif text == get_text(chat_id, 'help'):
        send_message(chat_id, get_text(chat_id, 'help_text'))
    
    elif user_states.get(chat_id) == "awaiting_imei":
        imei = text.strip()
        if not imei.isdigit() or len(imei) != 15:
            keyboard = telegram.ReplyKeyboardMarkup([
                [telegram.KeyboardButton(get_text(chat_id, 'check_imei'))],
                [telegram.KeyboardButton(get_text(chat_id, 'help'))]
            ], resize_keyboard=True)
            send_message(chat_id, get_text(chat_id, 'invalid_imei'), reply_markup=keyboard)
            return
        
        order_id = str(uuid.uuid4())
        pending_orders[order_id] = {
            'imei': imei,
            'user_id': chat_id,
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
        keyboard = telegram.InlineKeyboardMarkup([[
            telegram.InlineKeyboardButton(get_text(chat_id, 'pay_button'), url=payment_url)
        ]])
        
        send_message(chat_id, get_text(chat_id, 'payment_prompt', imei), reply_markup=keyboard)
        user_states[chat_id] = None

@app.route("/")
def home():
    return {"status": "healthy", "bot": "running"}, 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = telegram.Update.de_json(request.get_json(force=True), bot)
        
        # Handle different update types
        if update.message:
            chat_id = update.message.chat_id
            
            if update.message.text:
                if update.message.text.startswith('/start'):
                    handle_start(chat_id)
                else:
                    handle_text(chat_id, update.message.text)
        
        elif update.callback_query:
            query = update.callback_query
            chat_id = query.message.chat_id
            
            if query.data.startswith('lang_'):
                language = query.data.split('_')[1]
                handle_language_selection(chat_id, language)
                bot.answer_callback_query(query.id)
        
        return "OK"
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK"  # Return OK anyway to avoid retries

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
                
                send_message(user_id, msg)
                
                # Notify admins
                for admin_id in ADMIN_IDS:
                    send_message(admin_id, f"💰 Payment received!\nUser: {user_id}\nIMEI: {imei}")
        
        return order_id or "OK"
    except Exception as e:
        logger.error(f"Payeer error: {e}")
        return "Error", 500

@app.route("/success")
def success():
    return """
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { text-align: center; padding: 50px; font-family: Arial; background: #f0f0f0; }
            .container { background: white; padding: 30px; border-radius: 10px; max-width: 400px; margin: 0 auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #28a745; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✅ Payment Successful!</h1>
            <p>Check your Telegram for the IMEI result.</p>
            <p>You can close this window.</p>
        </div>
    </body>
    </html>
    """

@app.route("/fail")
def fail():
    return """
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { text-align: center; padding: 50px; font-family: Arial; background: #f0f0f0; }
            .container { background: white; padding: 30px; border-radius: 10px; max-width: 400px; margin: 0 auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #dc3545; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>❌ Payment Failed</h1>
            <p>Please return to Telegram and try again.</p>
        </div>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
