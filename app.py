import os
import logging
from flask import Flask, request, jsonify
import requests
import hashlib
import uuid
import base64
from urllib.parse import urlencode
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
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"
ADMIN_IDS = {2103379072, 6927331058}

# Flask app
app = Flask(__name__)

# Storage
pending_orders = {}
user_languages = {}
user_states = {}

# Set webhook
try:
    response = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{BASE_URL}/webhook"})
    logger.info(f"Webhook set: {response.json()}")
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
        'help_text': "📋 How to use:\n1. Send your 15-digit IMEI\n2. Click payment button\n3. Get your result\n\n📱 *How to find IMEI:*\n• Dial *#06#\n• Settings → About phone → IMEI\n\n⚠️ No refunds for wrong IMEI numbers!",
        'payment_successful': "✅ Payment successful!",
        'imei_info': "📱 IMEI Info:",
        'imei_not_found': "⚠️ IMEI not found in the database. Please ensure it is correct.",
        'service_unavailable': "❌ Service temporarily unavailable. Please try again later.",
        'check_another': "🔍 Check another IMEI",
        'admin_payment_received': "💰 Payment received!",
        'admin_user': "User",
        'admin_api_response': "API Response",
        'back': "🔙 Back",
        'use_menu': "❗ Please use the menu buttons below."
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
        'help_text': "📋 Как использовать:\n1. Отправьте 15-значный IMEI\n2. Нажмите кнопку оплаты\n3. Получите результат\n\n📱 *Как найти IMEI:*\n• Наберите *#06#\n• Настройки → О телефоне → IMEI\n\n⚠️ Возврат за неверный IMEI не предоставляется!",
        'payment_successful': "✅ Оплата успешна!",
        'imei_info': "📱 Информация об IMEI:",
        'imei_not_found': "⚠️ IMEI не найден в базе данных. Пожалуйста, убедитесь, что он правильный.",
        'service_unavailable': "❌ Сервис временно недоступен. Пожалуйста, попробуйте позже.",
        'check_another': "🔍 Проверить другой IMEI",
        'admin_payment_received': "💰 Платеж получен!",
        'admin_user': "Пользователь",
        'admin_api_response': "Ответ API",
        'back': "🔙 Назад",
        'use_menu': "❗ Пожалуйста, используйте кнопки меню ниже."
    }
}

# Field labels for IMEI results
field_labels = {
    'en': {
        'imei': "*IMEI:*",
        'imei2': "*IMEI2:*",
        'meid': "*MEID:*",
        'serial': "*Serial:*",
        'desc': "*Desc:*",
        'purchase': "*Purchase:*",
        'coverage': "*Coverage:*",
        'replaced': "*Replaced:*",
        'simlock': "*SIM Lock:*"
    },
    'ru': {
        'imei': "*IMEI:*",
        'imei2': "*IMEI2:*",
        'meid': "*MEID:*",
        'serial': "*Серийный номер:*",
        'desc': "*Описание:*",
        'purchase': "*Дата покупки:*",
        'coverage': "*Гарантия:*",
        'replaced': "*Заменен:*",
        'simlock': "*SIM-блокировка:*"
    }
}

def get_text(user_id, key, *args):
    lang = user_languages.get(user_id, 'en')
    text = texts.get(lang, texts['en']).get(key, key)
    return text.format(*args) if args else text

def get_field_label(user_id, field):
    lang = user_languages.get(user_id, 'en')
    return field_labels.get(lang, field_labels['en']).get(field, field)

def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    try:
        data = {
            "chat_id": chat_id,
            "text": text
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        if parse_mode:
            data["parse_mode"] = parse_mode
            
        response = requests.post(f"{TELEGRAM_API}/sendMessage", json=data)
        logger.info(f"Message sent: {response.json()}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")

def answer_callback_query(callback_query_id):
    try:
        requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": callback_query_id})
    except Exception as e:
        logger.error(f"Failed to answer callback query: {e}")

def handle_start(chat_id):
    keyboard = {
        "inline_keyboard": [[
            {"text": "🇬🇧 English", "callback_data": "lang_en"},
            {"text": "🇷🇺 Русский", "callback_data": "lang_ru"}
        ]]
    }
    send_message(chat_id, texts['en']['choose_language'], reply_markup=keyboard)

def handle_language_selection(chat_id, language):
    user_languages[chat_id] = language
    
    keyboard = {
        "keyboard": [
            [{"text": get_text(chat_id, 'check_imei')}],
            [{"text": get_text(chat_id, 'help')}]
        ],
        "resize_keyboard": True
    }
    
    send_message(chat_id, get_text(chat_id, 'language_selected'))
    send_message(chat_id, get_text(chat_id, 'welcome'), reply_markup=keyboard)

def quick_action_keyboard(user_id):
    return {
        "keyboard": [
            [{"text": get_text(user_id, 'check_another')}],
            [{"text": get_text(user_id, 'back')}]
        ],
        "resize_keyboard": True
    }

def handle_text(chat_id, text):
    if chat_id not in user_languages:
        handle_start(chat_id)
        return
    
    # Handle all button texts
    if text == get_text(chat_id, 'check_imei') or text == get_text(chat_id, 'check_another'):
        user_states[chat_id] = "awaiting_imei"
        send_message(chat_id, get_text(chat_id, 'enter_imei'))
    
    elif text == get_text(chat_id, 'help'):
        send_message(chat_id, get_text(chat_id, 'help_text'), parse_mode="Markdown")
    
    elif text == get_text(chat_id, 'back'):
        # Go back to main menu
        keyboard = {
            "keyboard": [
                [{"text": get_text(chat_id, 'check_imei')}],
                [{"text": get_text(chat_id, 'help')}]
            ],
            "resize_keyboard": True
        }
        send_message(chat_id, get_text(chat_id, 'welcome'), 
                    reply_markup=keyboard)
    
    elif user_states.get(chat_id) == "awaiting_imei":
        imei = text.strip()
        if not imei.isdigit() or len(imei) != 15:
            keyboard = {
                "keyboard": [
                    [{"text": get_text(chat_id, 'check_imei')}],
                    [{"text": get_text(chat_id, 'help')}]
                ],
                "resize_keyboard": True
            }
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
        keyboard = {
            "inline_keyboard": [[
                {"text": get_text(chat_id, 'pay_button'), "url": payment_url}
            ]]
        }
        
        send_message(chat_id, get_text(chat_id, 'payment_prompt', imei), reply_markup=keyboard)
        user_states[chat_id] = None
    else:
        # If text doesn't match any button, show main menu
        keyboard = {
            "keyboard": [
                [{"text": get_text(chat_id, 'check_imei')}],
                [{"text": get_text(chat_id, 'help')}]
            ],
            "resize_keyboard": True
        }
        send_message(chat_id, get_text(chat_id, 'use_menu'), 
                    reply_markup=keyboard)

def send_imei_result(user_id, imei):
    try:
        logger.info(f"Calling IMEI API for: {imei}")
        
        # Use simlock3 checker as specified
        params = {
            "api_key": IMEI_API_KEY,
            "checker": "simlock3",
            "number": imei
        }
        
        # Make the API request
        res = requests.get(IMEI_API_URL, params=params, timeout=15)
        
        logger.info(f"API response status: {res.status_code}")
        logger.info(f"API response: {res.text}")
        
        if res.status_code == 200:
            try:
                data = res.json()
                
                # Check if there's an error in the response
                if 'error' in data:
                    msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'imei_not_found')}"
                    send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
                else:
                    # Format the response with proper language and markdown
                    msg = f"*{get_text(user_id, 'payment_successful')}*\n\n*{get_text(user_id, 'imei_info')}*\n"
                    
                    # Define the exact order and format for fields
                    if 'IMEI' in data:
                        msg += f"🔹 {get_field_label(user_id, 'imei')} `{data['IMEI']}`\n"
                    if 'IMEI2' in data:
                        msg += f"🔹 {get_field_label(user_id, 'imei2')} `{data['IMEI2']}`\n"
                    if 'MEID' in data:
                        msg += f"🔹 {get_field_label(user_id, 'meid')} `{data['MEID']}`\n"
                    if 'Serial Number' in data:
                        msg += f"🔹 {get_field_label(user_id, 'serial')} `{data['Serial Number']}`\n"
                    if 'Description' in data:
                        msg += f"🔹 {get_field_label(user_id, 'desc')} `{data['Description']}`\n"
                    elif 'Model' in data:
                        msg += f"🔹 {get_field_label(user_id, 'desc')} `{data['Model']}`\n"
                    if 'Date of purchase' in data:
                        msg += f"🔹 {get_field_label(user_id, 'purchase')} `{data['Date of purchase']}`\n"
                    elif 'Purchase Date' in data:
                        msg += f"🔹 {get_field_label(user_id, 'purchase')} `{data['Purchase Date']}`\n"
                    if 'Repairs & Service Coverage' in data:
                        msg += f"🔹 {get_field_label(user_id, 'coverage')} `{data['Repairs & Service Coverage']}`\n"
                    elif 'Coverage' in data:
                        msg += f"🔹 {get_field_label(user_id, 'coverage')} `{data['Coverage']}`\n"
                    elif 'Warranty' in data:
                        msg += f"🔹 {get_field_label(user_id, 'coverage')} `{data['Warranty']}`\n"
                    if 'is replaced' in data:
                        msg += f"🔹 {get_field_label(user_id, 'replaced')} `{data['is replaced']}`\n"
                    elif 'Replaced' in data:
                        msg += f"🔹 {get_field_label(user_id, 'replaced')} `{data['Replaced']}`\n"
                    if 'SIM Lock' in data:
                        msg += f"🔹 {get_field_label(user_id, 'simlock')} `{data['SIM Lock']}`\n"
                    elif 'SimLock' in data:
                        msg += f"🔹 {get_field_label(user_id, 'simlock')} `{data['SimLock']}`\n"
                
                    send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id), parse_mode="Markdown")
                
                # Notify admins with full response
                notify_admins(user_id, imei, data)
                
            except json.JSONDecodeError:
                logger.error("Failed to parse API response as JSON")
                msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
                send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
                notify_admins(user_id, imei, {"error": "Invalid JSON response", "raw": res.text})
                
        else:
            logger.error(f"API returned status code: {res.status_code}")
            msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
            send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
            notify_admins(user_id, imei, {"error": f"Status code: {res.status_code}"})
            
    except requests.Timeout:
        logger.error("API request timed out")
        msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
        send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
        notify_admins(user_id, imei, {"error": "Timeout"})
        
    except Exception as e:
        logger.error(f"IMEI API error: {e}")
        msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
        send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
        notify_admins(user_id, imei, {"error": str(e)})

def notify_admins(user_id, imei, api_response=None):
    """Notify admins about the payment with API details"""
    for admin_id in ADMIN_IDS:
        # Get admin's language preference
        admin_msg = f"{get_text(admin_id, 'admin_payment_received')}\n"
        admin_msg += f"{get_text(admin_id, 'admin_user')}: {user_id}\n"
        admin_msg += f"IMEI: {imei}\n"
        
        if api_response:
            if isinstance(api_response, dict):
                admin_msg += f"\n{get_text(admin_id, 'admin_api_response')}:\n"
                for key, value in api_response.items():
                    admin_msg += f"{key}: {value}\n"
        
        send_message(admin_id, admin_msg)

@app.route("/")
def home():
    return {"status": "healthy", "bot": "running"}, 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        logger.info(f"Received update: {update}")
        
        # Handle message
        if "message" in update:
            message = update["message"]
            chat_id = message["chat"]["id"]
            
            if "text" in message:
                text = message["text"]
                if text.startswith('/start'):
                    handle_start(chat_id)
                else:
                    handle_text(chat_id, text)
        
        # Handle callback query
        elif "callback_query" in update:
            query = update["callback_query"]
            chat_id = query["message"]["chat"]["id"]
            data = query["data"]
            
            if data.startswith('lang_'):
                language = data.split('_')[1]
                handle_language_selection(chat_id, language)
                answer_callback_query(query["id"])
        
        return "OK"
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK"

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
                
                # Send IMEI result
                send_imei_result(user_id, imei)
        
        return order_id or "OK"
    except Exception as e:
        logger.error(f"Payeer error: {e}")
        return "Error", 500

@app.route("/success")
def success():
    order_id = request.args.get("m_orderid")
    if order_id and order_id in pending_orders:
        order = pending_orders[order_id]
        if order['status'] == 'pending':
            order['status'] = 'paid'
            send_imei_result(order['user_id'], order['imei'])
    
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
