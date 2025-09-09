import requests
from flask import Flask, request, render_template
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
import hashlib
import uuid
import os
import threading
from urllib.parse import urlencode
import base64
import logging
import asyncio
import traceback
import time
import json
from datetime import datetime
import sys
from queue import Queue

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")]
)
logger = logging.getLogger(__name__)

# Railway Environment Configuration
logger.info("Loading environment variables from Railway...")

TOKEN = os.getenv("TOKEN")
IMEI_API_KEY = os.getenv("IMEI_API_KEY")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")

# Validate environment variables
env_vars = {
    "TOKEN": TOKEN,
    "IMEI_API_KEY": IMEI_API_KEY,
    "PAYEER_MERCHANT_ID": PAYEER_MERCHANT_ID,
    "PAYEER_SECRET_KEY": PAYEER_SECRET_KEY,
    "BASE_URL": BASE_URL
}

# Check for missing variables
missing_vars = [name for name, value in env_vars.items() if not value]
if missing_vars:
    logger.error(f"FATAL: Missing environment variables: {', '.join(missing_vars)}")
    logger.error("Please set these variables in Railway dashboard under Variables tab")
    sys.exit(1)
else:
    logger.info("All environment variables loaded successfully")

# Log non-sensitive configuration info
logger.info(f"BASE_URL configured: {BASE_URL}")
logger.info(f"Bot token loaded: {TOKEN[:10]}...")

# Constants
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"
ADMIN_IDS = {2103379072, 6927331058}

# Flask app
app = Flask(__name__)

# In-memory storage
pending_orders = {}  # order_id -> {imei, user_id, timestamp, status}
user_languages = {}  # user_id -> language
user_states = {}
user_request_times = {}
payment_stats = {
    "total_requests": 0,
    "successful_payments": 0,
    "total_revenue": 0.0,
    "unique_users": set()
}

# Message queue for async operations
message_queue = Queue()

# Bot setup
application = Application.builder().token(TOKEN).build()

# Translations dictionary
texts = {
    'en': {
        'welcome': "👋 Welcome! Choose an option:",
        'language_selected': "🇬🇧 English language selected. You can change the language anytime using the /language command.",
        'check_imei': "🔍 Check IMEI",
        'help': "❓ Help",
        'back': "🔙 Back",
        'enter_imei': "🔢 Please enter your 15-digit IMEI number.",
        'invalid_imei': "❌ Invalid IMEI. It must be 15 digits.",
        'payment_prompt': "📱 IMEI: {}\nTo receive your result, please complete payment:",
        'pay_button': "💳 Pay $0.32 USD",
        'wait_message': "⏳ Please wait a moment before sending another message.",
        'back_to_main': "🏠 Back to main menu. Please choose an option:",
        'use_menu': "❗ Please use the menu or /start to begin.",
        'help_title': "🆘 Help & Tutorial",
        'help_intro': "Welcome to the IMEI Checker Bot! Here's how to use the service correctly and safely:",
        'help_how_to': "📋 How to Use:",
        'help_step1': "1. 🔢 Send your 15-digit IMEI number (example: 358792654321789)",
        'help_step2': "2. 💳 You'll receive a payment button — click it and complete payment ($0.32)",
        'help_step3': "3. 📩 Once payment is confirmed, you will automatically receive your IMEI result",
        'help_notes': "⚠️ Important Notes:",
        'help_note1': "- ✅ Always double-check your IMEI before sending.",
        'help_note2': "- 🚫 If you enter a wrong IMEI, we are not responsible for incorrect or missing results.",
        'help_note3': "- 🔁 No refunds are provided for typos or invalid IMEI numbers.",
        'help_note4': "- 🧾 Make sure your IMEI is 15 digits — no spaces or dashes.",
        'help_sample': "📱 Sample Result (Preview):",
        'help_sample_content': "✅ Payment successful!\n\n📱 IMEI Info:\n🔷 IMEI: 358792654321789\n🔷 IMEI2: 358792654321796\n🔷 MEID: 35879265432178\n🔷 Serial: G7XP91LMN9K\n🔷 Desc: iPhone 13 Pro Max SILVER 256GB\n🔷 Purchase: 2022-11-22\n🔷 Coverage: Active – AppleCare+\n🔷 Replaced: No\n🔷 SIM Lock: Unlocked\n\n⚠️ This is a sample result for demonstration only. Your actual result will depend on the IMEI you submit.",
        'not_authorized': "🚫 You are not authorized to view stats.",
        'service_unavailable': "❌ Service temporarily unavailable. Please try again later.",
        'imei_not_found': "⚠️ IMEI not found in the database. Please ensure it is correct.",
        'payment_successful': "✅ Payment successful!",
        'imei_info': "📱 IMEI Info:",
        'imei_field': "🔹 IMEI: {}",
        'imei2_field': "🔹 IMEI2: {}",
        'meid_field': "🔹 MEID: {}",
        'serial_field': "🔹 Serial: {}",
        'desc_field': "🔹 Desc: {}",
        'purchase_field': "🔹 Purchase: {}",
        'coverage_field': "🔹 Coverage: {}",
        'replaced_field': "🔹 Replaced: {}",
        'simlock_field': "🔹 SIM Lock: {}",
        'api_error': "❌ Error connecting to IMEI service. Please try again later or contact support.",
        'unexpected_error': "❌ An unexpected error occurred. Please contact support.",
        'choose_language': "Please select your language / Пожалуйста, выберите ваш язык:"
    },
    'ru': {
        'welcome': "👋 Добро пожаловать! Выберите опцию:",
        'language_selected': "🇷🇺 Выбран русский язык. Вы можете изменить язык в любое время с помощью команды /language.",
        'check_imei': "🔍 Проверить IMEI",
        'help': "❓ Помощь",
        'back': "🔙 Назад",
        'enter_imei': "🔢 Пожалуйста, введите ваш 15-значный номер IMEI.",
        'invalid_imei': "❌ Неверный IMEI. Он должен состоять из 15 цифр.",
        'payment_prompt': "📱 IMEI: {}\nЧтобы получить результат, пожалуйста, выполните оплату:",
        'pay_button': "💳 Оплатить $0.32 USD",
        'wait_message': "⏳ Пожалуйста, подождите немного перед отправкой следующего сообщения.",
        'back_to_main': "🏠 Возврат в главное меню. Пожалуйста, выберите опцию:",
        'use_menu': "❗ Пожалуйста, используйте меню или /start для начала.",
        'help_title': "🆘 Помощь и Руководство",
        'help_intro': "Добро пожаловать в бот проверки IMEI! Вот как правильно и безопасно пользоваться сервисом:",
        'help_how_to': "📋 Как использовать:",
        'help_step1': "1. 🔢 Отправьте ваш 15-значный номер IMEI (пример: 358792654321789)",
        'help_step2': "2. 💳 Вы получите кнопку оплаты — нажмите на неё и выполните оплату ($0.32)",
        'help_step3': "3. 📩 После подтверждения оплаты вы автоматически получите результат проверки IMEI",
        'help_notes': "⚠️ Важные примечания:",
        'help_note1': "- ✅ Всегда проверяйте ваш IMEI перед отправкой.",
        'help_note2': "- 🚫 Если вы введете неправильный IMEI, мы не несем ответственности за неверные или отсутствующие результаты.",
        'help_note3': "- 🔁 Возврат средств за опечатки или недействительные номера IMEI не предоставляется.",
        'help_note4': "- 🧾 Убедитесь, что ваш IMEI состоит из 15 цифр — без пробелов или дефисов.",
        'help_sample': "📱 Пример результата (Превью):",
        'help_sample_content': "✅ Оплата успешна!\n\n📱 Информация об IMEI:\n🔷 IMEI: 358792654321789\n🔷 IMEI2: 358792654321796\n🔷 MEID: 35879265432178\n🔷 Серийный номер: G7XP91LMN9K\n🔷 Описание: iPhone 13 Pro Max СЕРЕБРИСТЫЙ 256GB\n🔷 Дата покупки: 2022-11-22\n🔷 Гарантия: Активна – AppleCare+\n🔷 Заменен: Нет\n🔷 SIM-блокировка: Разблокирован\n\n⚠️ Это образец результата только для демонстрации. Ваш фактический результат будет зависеть от предоставленного IMEI.",
        'not_authorized': "🚫 У вас нет прав для просмотра статистики.",
        'service_unavailable': "❌ Сервис временно недоступен. Пожалуйста, попробуйте позже.",
        'imei_not_found': "⚠️ IMEI не найден в базе данных. Пожалуйста, убедитесь, что он правильный.",
        'payment_successful': "✅ Оплата успешна!",
        'imei_info': "📱 Информация об IMEI:",
        'imei_field': "🔹 IMEI: {}",
        'imei2_field': "🔹 IMEI2: {}",
        'meid_field': "🔹 MEID: {}",
        'serial_field': "🔹 Серийный номер: {}",
        'desc_field': "🔹 Описание: {}",
        'purchase_field': "🔹 Дата покупки: {}",
        'coverage_field': "🔹 Гарантия: {}",
        'replaced_field': "🔹 Заменен: {}",
        'simlock_field': "🔹 SIM-блокировка: {}",
        'api_error': "❌ Ошибка подключения к сервису IMEI. Пожалуйста, попробуйте позже или обратитесь в поддержку.",
        'unexpected_error': "❌ Произошла непредвиденная ошибка. Пожалуйста, обратитесь в поддержку.",
        'choose_language': "Please select your language / Пожалуйста, выберите ваш язык:"
    }
}

# Helper functions
def is_rate_limited(user_id, limit_seconds=5):
    current_time = time.time()
    if user_id in user_request_times:
        if current_time - user_request_times[user_id] < limit_seconds:
            return True
    user_request_times[user_id] = current_time
    return False

def get_user_language(user_id):
    return user_languages.get(user_id, 'en')

def set_user_language(user_id, language):
    user_languages[user_id] = language
    return True

def get_text(user_id, text_key, *args):
    lang = get_user_language(user_id)
    text = texts.get(lang, texts['en']).get(text_key, texts['en'].get(text_key, f"Missing text: {text_key}"))
    
    if args:
        return text.format(*args)
    return text

def create_order(order_id, imei, user_id):
    pending_orders[order_id] = {
        'imei': imei,
        'user_id': user_id,
        'timestamp': datetime.now(),
        'status': 'pending'
    }
    payment_stats['total_requests'] += 1
    payment_stats['unique_users'].add(user_id)
    return True

def process_payment(order_id):
    if order_id in pending_orders:
        order = pending_orders[order_id]
        if order['status'] == 'paid':
            return order['user_id'], order['imei'], True
        
        order['status'] = 'paid'
        payment_stats['successful_payments'] += 1
        payment_stats['total_revenue'] += float(PRICE)
        return order['user_id'], order['imei'], False
    
    return None, None, False

# Cleanup old orders periodically
def cleanup_old_orders():
    current_time = datetime.now()
    to_remove = []
    for order_id, order in pending_orders.items():
        if (current_time - order['timestamp']).total_seconds() > 86400:  # 24 hours
            to_remove.append(order_id)
    
    for order_id in to_remove:
        del pending_orders[order_id]

# Main menu keyboard
def main_menu_keyboard(user_id):
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(get_text(user_id, 'check_imei'))], 
            [KeyboardButton(get_text(user_id, 'help'))]
        ], 
        resize_keyboard=True
    )

# Language selection keyboard
def language_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
        ]
    ])

# Message processor for async operations
def message_processor():
    """Background thread to process messages"""
    while True:
        try:
            task = message_queue.get()
            if task is None:
                break
                
            task_type = task.get('type')
            
            if task_type == 'send_message':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    application.bot.send_message(
                        chat_id=task['chat_id'],
                        text=task['text'],
                        parse_mode=task.get('parse_mode', None)
                    )
                )
                loop.close()
                
        except Exception as e:
            logger.error(f"Message processor error: {e}")
            logger.error(traceback.format_exc())

# Start the message processor thread
processor_thread = threading.Thread(target=message_processor, daemon=True)
processor_thread.start()

# Handlers
def register_handlers():
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Always show language selection first for every /start command
        await update.message.reply_text(
            "Please select your language / Пожалуйста, выберите ваш язык:",
            reply_markup=language_keyboard()
        )

    async def language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Show language selection keyboard
        await update.message.reply_text(
            get_text(update.effective_user.id, 'choose_language'),
            reply_markup=language_keyboard()
        )

    async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        lang = query.data.split('_')[1]
        
        # Save user language preference
        set_user_language(user_id, lang)
        
        # Respond in the selected language
        await query.answer()
        await query.edit_message_text(text=get_text(user_id, 'language_selected'))
        
        # Send main menu with the new language
        await context.bot.send_message(
            chat_id=user_id,
            text=get_text(user_id, 'welcome'),
            reply_markup=main_menu_keyboard(user_id)
        )

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        keyboard = [[KeyboardButton(get_text(user_id, 'back'))]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        help_text = (
            f"*{get_text(user_id, 'help_title')}*\n\n"
            f"{get_text(user_id, 'help_intro')}\n\n"
            f"{get_text(user_id, 'help_how_to')}\n"
            f"{get_text(user_id, 'help_step1')}\n"
            f"{get_text(user_id, 'help_step2')}\n"
            f"{get_text(user_id, 'help_step3')}\n\n"
            f"{get_text(user_id, 'help_notes')}\n"
            f"{get_text(user_id, 'help_note1')}\n"
            f"{get_text(user_id, 'help_note2')}\n"
            f"{get_text(user_id, 'help_note3')}\n"
            f"{get_text(user_id, 'help_note4')}\n\n"
            f"{get_text(user_id, 'help_sample')}\n\n"
            f"{get_text(user_id, 'help_sample_content')}"
        )

        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=reply_markup)

    async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
            await update.message.reply_text(get_text(user_id, 'not_authorized'))
            return

        # Clean up old orders first
        cleanup_old_orders()
        
        msg = (
            "📊 *Bot Usage Stats:*\n"
            f"• Total IMEI checks: *{payment_stats['total_requests']}*\n"
            f"• Successful payments: *{payment_stats['successful_payments']}*\n"
            f"• Unique users: *{len(payment_stats['unique_users'])}*\n"
            f"• Total revenue: *${payment_stats['total_revenue']:.2f} USD*\n\n"
            f"• Active orders: *{len(pending_orders)}*\n"
            f"• Languages: EN: *{sum(1 for l in user_languages.values() if l == 'en')}*, "
            f"RU: *{sum(1 for l in user_languages.values() if l == 'ru')}*"
        )

        await update.message.reply_text(msg, parse_mode="Markdown")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        user = update.effective_user

        # Rate limiting check
        if is_rate_limited(user_id):
            await update.message.reply_text(get_text(user_id, 'wait_message'))
            return

        # Check if user has language preference
        if user_id not in user_languages:
            # If no language preference, show language selection
            await update.message.reply_text(
                "Please select your language / Пожалуйста, выберите ваш язык:",
                reply_markup=language_keyboard()
            )
            return

        # Get user's language
        lang = get_user_language(user_id)
        
        # Check if text matches any of the translated buttons
        if text == get_text(user_id, 'back'):
            await update.message.reply_text(
                get_text(user_id, 'back_to_main'),
                reply_markup=main_menu_keyboard(user_id)
            )
        elif text == get_text(user_id, 'check_imei'):
            user_states[user_id] = "awaiting_imei"
            await update.message.reply_text(get_text(user_id, 'enter_imei'))
        elif text == get_text(user_id, 'help'):
            await help_cmd(update, context)
        elif user_states.get(user_id) == "awaiting_imei":
            imei = text.strip()
            if not imei.isdigit() or len(imei) != 15:
                await update.message.reply_text(
                    get_text(user_id, 'invalid_imei'),
                    reply_markup=main_menu_keyboard(user_id)
                )
                return

            order_id = str(uuid.uuid4())
            
            if create_order(order_id, imei, user_id):
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
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(get_text(user_id, 'pay_button'), url=payment_url)]])
                
                await update.message.reply_text(
                    get_text(user_id, 'payment_prompt', imei),
                    reply_markup=keyboard
                )
            else:
                await update.message.reply_text("❌ An error occurred. Please try again later.")
                
            user_states[user_id] = None
        else:
            await update.message.reply_text(
                get_text(user_id, 'use_menu'),
                reply_markup=main_menu_keyboard(user_id)
            )

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("language", language_cmd))
    application.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

register_handlers()

# Flask routes
@app.route("/")
def home():
    """Root endpoint for Railway health checks"""
    return {
        "status": "healthy",
        "service": "IMEI Checker Bot",
        "timestamp": datetime.now().isoformat()
    }, 200

@app.route("/health")
def health_check():
    """Detailed health check endpoint"""
    return {
        "status": "healthy",
        "service": "IMEI Checker Bot",
        "active_orders": len(pending_orders),
        "total_requests": payment_stats['total_requests']
    }, 200

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    try:
        form = request.form.to_dict()
        logger.info(f"Received Payeer callback: {form}")

        # For now, skip signature verification to test
        # TODO: Fix signature verification with Payeer support
        
        order_id = form.get("m_orderid")
        if form.get("m_status") != "success":
            logger.warning(f"Payment not successful for order {order_id}")
            return "Payment not successful", 400

        user_id, imei, already_processed = process_payment(order_id)
        
        if user_id and imei and not already_processed:
            threading.Thread(target=send_imei_result, args=(user_id, imei, order_id)).start()
            
        return order_id  # Payeer expects the order ID in response
    except Exception as e:
        logger.error(f"Payeer callback error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error processing payment", 500
        
@app.route("/success")
def success():
    order_id = request.args.get("m_orderid")
    if not order_id:
        return render_template("fail.html", message="Invalid order ID")

    try:
        # Try to process payment if not already processed
        user_id, imei, already_processed = process_payment(order_id)
        
        if user_id and imei and not already_processed:
            threading.Thread(target=send_imei_result, args=(user_id, imei, order_id)).start()
            
        return render_template("success.html")
    except Exception as e:
        logger.error(f"/success error: {e}")
        logger.error(traceback.format_exc())
        return render_template("fail.html", message="An error occurred")

@app.route("/fail")
def fail():
    return render_template("fail.html", message="Payment was not completed")

def send_imei_result(user_id, imei, order_id):
    try:
        # Get user's language
        lang = get_user_language(user_id)
        
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        res = requests.get(IMEI_API_URL, params=params, timeout=15)
        
        # More detailed error handling
        if res.status_code != 200:
            logger.error(f"API error: Status {res.status_code}, Response: {res.text}")
            message_queue.put({
                'type': 'send_message',
                'chat_id': user_id,
                'text': get_text(user_id, 'service_unavailable'),
                'parse_mode': 'Markdown'
            })
            return
            
        data = res.json()

        if 'error' in data or not any(value for key, value in data.items() if key != 'error'):
            msg = get_text(user_id, 'imei_not_found')
        else:
            msg = f"*{get_text(user_id, 'payment_successful')}*\n\n"
            msg += f"*{get_text(user_id, 'imei_info')}*\n"
            msg += get_text(user_id, 'imei_field', data.get('IMEI', 'N/A')) + "\n"
            msg += get_text(user_id, 'imei2_field', data.get('IMEI2', 'N/A')) + "\n"
            msg += get_text(user_id, 'meid_field', data.get('MEID', 'N/A')) + "\n"
            msg += get_text(user_id, 'serial_field', data.get('Serial Number', 'N/A')) + "\n"
            msg += get_text(user_id, 'desc_field', data.get('Description', 'N/A')) + "\n"
            msg += get_text(user_id, 'purchase_field', data.get('Date of purchase', 'N/A')) + "\n"
            msg += get_text(user_id, 'coverage_field', data.get('Repairs & Service Coverage', 'N/A')) + "\n"
            msg += get_text(user_id, 'replaced_field', data.get('is replaced', 'N/A')) + "\n"
            msg += get_text(user_id, 'simlock_field', data.get('SIM Lock', 'N/A'))

        message_queue.put({
            'type': 'send_message',
            'chat_id': user_id,
            'text': msg,
            'parse_mode': 'Markdown'
        })
        
        # Notify admins about successful payment
        admin_msg = f"💰 New payment received!\n👤 User ID: {user_id}\n📱 IMEI: {imei}"
        for admin_id in ADMIN_IDS:
            message_queue.put({
                'type': 'send_message',
                'chat_id': admin_id,
                'text': admin_msg
            })
                
    except Exception as e:
        logger.error(f"Sending result error: {str(e)}")
        logger.error(traceback.format_exc())
        message_queue.put({
            'type': 'send_message',
            'chat_id': user_id,
            'text': get_text(user_id, 'unexpected_error')
        })

# Create templates directory if it doesn't exist
os.makedirs('templates', exist_ok=True)

# Create template files if they don't exist
if not os.path.exists('templates/success.html'):
    with open('templates/success.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Payment Successful</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 20px; background-color: #f5f5f5; }
        .container { max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .success { color: #28a745; font-size: 28px; margin: 20px 0; }
        .message { margin: 20px 0; font-size: 18px; color: #333; }
        .icon { font-size: 60px; color: #28a745; }
        .footer { margin-top: 30px; font-size: 14px; color: #777; }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">✅</div>
        <div class="success">Payment Successful!</div>
        <div class="message">Your IMEI check result has been sent to your Telegram chat.</div>
        <div class="message">You can close this window and return to Telegram.</div>
        <div class="footer">Thank you for using our service.</div>
    </div>
</body>
</html>''')

if not os.path.exists('templates/fail.html'):
    with open('templates/fail.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Payment Failed</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 20px; background-color: #f5f5f5; }
        .container { max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .fail { color: #dc3545; font-size: 28px; margin: 20px 0; }
        .message { margin: 20px 0; font-size: 18px; color: #333; }
        .icon { font-size: 60px; color: #dc3545; }
        .footer { margin-top: 30px; font-size: 14px; color: #777; }
        .button { display: inline-block; background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">❌</div>
        <div class="fail">Payment Failed</div>
        <div class="message">{{ message|default("Your payment was not processed successfully.") }}</div>
        <div class="message">Please return to Telegram and try again.</div>
        <a href="https://t.me/your_bot_username" class="button">Return to Telegram</a>
        <div class="footer">If you need assistance, please contact our support.</div>
    </div>
</body>
</html>''')

# Initialize bot and set webhook
async def setup_bot():
    """Initialize bot and set webhook"""
    await application.initialize()
    webhook_url = f"{BASE_URL}/webhook"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

# Initialize bot before starting Flask
try:
    asyncio.run(setup_bot())
except Exception as e:
    logger.error(f"Bot setup error: {e}")
    logger.error(traceback.format_exc())

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        # Create update object
        update = Update.de_json(update_json, application.bot)

        # Use a thread to process the update
        def process_async():
            asyncio.run(application.process_update(update))
        
        threading.Thread(target=process_async).start()
        
        return "OK"
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error", 500

if __name__ == "__main__":
    try:
        logger.info("Starting Telegram bot on Railway...")
        
        # Get port from Railway environment
        port = int(os.environ.get("PORT", 8080))
        logger.info(f"Starting Flask server on port {port}")
        
        # Railway requires 0.0.0.0 to bind to all interfaces
        app.run(
            host="0.0.0.0", 
            port=port,
            debug=False,
            use_reloader=False  # Important: disable reloader to prevent double initialization
        )
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal startup error: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        # Signal message processor to stop
        message_queue.put(None)
        logger.info("Shutdown complete")
