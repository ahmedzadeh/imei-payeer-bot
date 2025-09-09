import psycopg2
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
from psycopg2 import pool
import json
from datetime import datetime
import sys

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
DATABASE_URL = os.getenv("DATABASE_URL")

# Validate environment variables
env_vars = {
    "TOKEN": TOKEN,
    "IMEI_API_KEY": IMEI_API_KEY,
    "PAYEER_MERCHANT_ID": PAYEER_MERCHANT_ID,
    "PAYEER_SECRET_KEY": PAYEER_SECRET_KEY,
    "BASE_URL": BASE_URL,
    "DATABASE_URL": DATABASE_URL
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
logger.info(f"Database URL loaded: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'URL format unclear'}")

# Constants
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"
ADMIN_IDS = {2103379072, 6927331058}

# Flask app
app = Flask(__name__)

# Database connection pool with Railway PostgreSQL
try:
    connection_pool = pool.SimpleConnectionPool(
        1, 10, 
        DATABASE_URL,
        connect_timeout=10
    )
    logger.info("Database connection pool created successfully")
except Exception as e:
    logger.error(f"Failed to create database connection pool: {e}")
    sys.exit(1)

def get_db_connection():
    return connection_pool.getconn()

def release_db_connection(conn):
    connection_pool.putconn(conn)

# Database initialization
def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # Create a more comprehensive imei_checks table
            c.execute('''
                CREATE TABLE IF NOT EXISTS imei_checks (
                    id SERIAL PRIMARY KEY,
                    order_id TEXT UNIQUE,
                    imei TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    check_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    imei_found BOOLEAN DEFAULT NULL,
                    payment_status TEXT DEFAULT 'initiated',
                    payment_amount TEXT,
                    payment_currency TEXT DEFAULT 'USD',
                    payeer_client_id TEXT,
                    payeer_client_email TEXT,
                    flow_status TEXT DEFAULT 'imei_submitted',
                    api_response JSONB,
                    notes TEXT
                )
            ''')
            
            # Create user_settings table for language preferences
            c.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    language TEXT DEFAULT 'en',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create an index for faster lookups
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_user_id ON imei_checks (user_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_imei ON imei_checks (imei)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_imei_checks_order_id ON imei_checks (order_id)')
            
            conn.commit()
            logger.info("PostgreSQL Database initialized on Railway")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        conn.rollback()
    finally:
        release_db_connection(conn)

# Initialize database
init_db()

# Bot setup
application = Application.builder().token(TOKEN).build()
user_states = {}
user_request_times = {}

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

# Rate limiting function
def is_rate_limited(user_id, limit_seconds=5):
    current_time = time.time()
    if user_id in user_request_times:
        if current_time - user_request_times[user_id] < limit_seconds:
            return True
    user_request_times[user_id] = current_time
    return False

# Check if user has language preference set
def has_language_preference(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT language FROM user_settings WHERE user_id = %s", (user_id,))
            result = c.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Error checking language preference: {e}")
        return False
    finally:
        release_db_connection(conn)

# Get user language preference
def get_user_language(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT language FROM user_settings WHERE user_id = %s", (user_id,))
            result = c.fetchone()
            if result:
                return result[0]
            else:
                # Default to English if no preference is set
                return 'en'
    except Exception as e:
        logger.error(f"Error getting user language: {e}")
        return 'en'  # Default to English on error
    finally:
        release_db_connection(conn)

# Set user language preference
def set_user_language(user_id, language):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO user_settings (user_id, language, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET language = %s, updated_at = NOW()
                """,
                (user_id, language, language)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error setting user language: {e}")
        conn.rollback()
        return False
    finally:
        release_db_connection(conn)

# Get text in user's language
def get_text(user_id, text_key, *args):
    lang = get_user_language(user_id)
    text = texts.get(lang, texts['en']).get(text_key, texts['en'].get(text_key, f"Missing text: {text_key}"))
    
    if args:
        return text.format(*args)
    return text

# Update IMEI check record
def update_imei_check(order_id=None, imei=None, user_id=None, **kwargs):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            if order_id:
                # Build the SET part of the query dynamically
                set_parts = []
                params = []
                
                for key, value in kwargs.items():
                    set_parts.append(f"{key} = %s")
                    params.append(value)
                
                if not set_parts:
                    return False
                
                query = f"UPDATE imei_checks SET {', '.join(set_parts)} WHERE order_id = %s"
                params.append(order_id)
                
                c.execute(query, params)
                conn.commit()
                return True
            elif imei and user_id:
                # Find the most recent check for this IMEI and user
                c.execute(
                    "SELECT order_id FROM imei_checks WHERE imei = %s AND user_id = %s ORDER BY check_time DESC LIMIT 1",
                    (imei, user_id)
                )
                result = c.fetchone()
                if result:
                    order_id = result[0]
                    return update_imei_check(order_id=order_id, **kwargs)
            
            return False
    except Exception as e:
        logger.error(f"Error updating IMEI check: {e}")
        conn.rollback()
        return False
    finally:
        release_db_connection(conn)

# Create new IMEI check record
def create_imei_check(order_id, imei, user_id, username=None, first_name=None, last_name=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO imei_checks 
                (order_id, imei, user_id, username, first_name, last_name, payment_amount, flow_status) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (order_id, imei, user_id, username, first_name, last_name, PRICE, 'imei_submitted')
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error creating IMEI check: {e}")
        conn.rollback()
        return False
    finally:
        release_db_connection(conn)

# Process payment function with enhanced tracking
def process_payment(order_id, payeer_client_id=None, payeer_client_email=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # Get the current record
            c.execute(
                "SELECT user_id, imei, payment_status FROM imei_checks WHERE order_id = %s",
                (order_id,)
            )
            row = c.fetchone()
            if not row:
                return None, None, False
                
            user_id, imei, payment_status = row
            
            # Check if already paid
            if payment_status == 'paid':
                return user_id, imei, True  # Already processed
            
            # Update payment information
            update_data = {
                'payment_status': 'paid',
                'flow_status': 'payment_completed',
                'payeer_client_id': payeer_client_id,
                'payeer_client_email': payeer_client_email
            }
            
            set_parts = []
            params = []
            
            for key, value in update_data.items():
                if value is not None:
                    set_parts.append(f"{key} = %s")
                    params.append(value)
            
            if set_parts:
                query = f"UPDATE imei_checks SET {', '.join(set_parts)} WHERE order_id = %s"
                params.append(order_id)
                c.execute(query, params)
                conn.commit()
            
            return user_id, imei, False  # Newly processed
    except Exception as e:
        logger.error(f"Payment processing error: {e}")
        conn.rollback()
        return None, None, False
    finally:
        release_db_connection(conn)

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

        try:
            conn = get_db_connection()
            try:
                with conn.cursor() as c:
                    c.execute("SELECT COUNT(*) FROM imei_checks WHERE payment_status = 'paid'")
                    total_paid = c.fetchone()[0]

                    c.execute("SELECT COUNT(*) FROM imei_checks")
                    total_requests = c.fetchone()[0]

                    c.execute("SELECT COUNT(DISTINCT user_id) FROM imei_checks")
                    unique_users = c.fetchone()[0]
                    
                    c.execute("SELECT SUM(CAST(payment_amount AS DECIMAL)) FROM imei_checks WHERE payment_status = 'paid'")
                    total_revenue = c.fetchone()[0] or 0
                    
                    c.execute("""
                        SELECT DATE(check_time), COUNT(*) 
                        FROM imei_checks 
                        WHERE payment_status = 'paid' 
                        GROUP BY DATE(check_time) 
                        ORDER BY DATE(check_time) DESC 
                        LIMIT 7
                    """)
                    daily_stats = c.fetchall()
                    
                    c.execute("""
                        SELECT flow_status, COUNT(*) 
                        FROM imei_checks 
                        GROUP BY flow_status
                    """)
                    flow_stats = c.fetchall()
                    
                    c.execute("""
                        SELECT language, COUNT(*) 
                        FROM user_settings 
                        GROUP BY language
                    """)
                    language_stats = c.fetchall()
                    
                    daily_report = "\n".join([f"• {date.strftime('%Y-%m-%d')}: {count} payments" for date, count in daily_stats])
                    flow_report = "\n".join([f"• {status}: {count} users" for status, count in flow_stats])
                    language_report = "\n".join([f"• {lang}: {count} users" for lang, count in language_stats])

                msg = (
                    "📊 *Bot Usage Stats:*\n"
                    f"• Total IMEI checks: *{total_requests}*\n"
                    f"• Successful payments: *{total_paid}*\n"
                    f"• Unique users: *{unique_users}*\n"
                    f"• Total revenue: *${total_revenue:.2f} USD*\n\n"
                    f"📅 *Last 7 Days:*\n{daily_report}\n\n"
                    f"🔄 *User Flow:*\n{flow_report}\n\n"
                    f"🌐 *Language Stats:*\n{language_report}"
                )

                await update.message.reply_text(msg, parse_mode="Markdown")
            finally:
                release_db_connection(conn)
        except Exception as e:
            logger.error(f"/stats error: {e}")
            logger.error(traceback.format_exc())
            await update.message.reply_text("❌ Failed to load stats.")

    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        user = update.effective_user

        # Rate limiting check
        if is_rate_limited(user_id):
            await update.message.reply_text(get_text(user_id, 'wait_message'))
            return

        # Check if user has language preference
        if not has_language_preference(user_id):
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
            
            # Create IMEI check record with user details
            username = user.username
            first_name = user.first_name
            last_name = user.last_name

            if create_imei_check(order_id, imei, user_id, username, first_name, last_name):
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
                
                # Update flow status to payment_initiated
                update_imei_check(order_id=order_id, flow_status='payment_initiated')
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
    health_status = {
        "status": "healthy",
        "checks": {
            "database": False,
            "telegram_webhook": False
        }
    }
    
    # Check database
    try:
        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute("SELECT 1")
        release_db_connection(conn)
        health_status["checks"]["database"] = True
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["checks"]["database_error"] = str(e)
    
    # Check if webhook is set (basic check)
    if TOKEN and BASE_URL:
        health_status["checks"]["telegram_webhook"] = True
    
    status_code = 200 if health_status["status"] == "healthy" else 503
    return health_status, status_code

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update_json = request.get_json(force=True)
        logger.info(f"Received Telegram update: {update_json}")

        update = Update.de_json(update_json, application.bot)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def handle():
            await application.initialize()
            await application.process_update(update)

        loop.run_until_complete(handle())
        return "OK"
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        logger.error(traceback.format_exc())
        return "Error", 500

@app.route("/payeer", methods=["POST"])
def payeer_callback():
    try:
        form = request.form.to_dict()
        logger.info(f"Received Payeer callback: {form}")

        # Verify payment signature
        if "m_sign" in form:
            received_sign = form.get("m_sign")
            sign_string = f"{form.get('m_operation_id')}:{form.get('m_operation_ps')}:{form.get('m_operation_date')}:{form.get('m_operation_pay_date')}:{form.get('m_shop')}:{form.get('m_orderid')}:{form.get('m_amount')}:{form.get('m_curr')}:{PAYEER_SECRET_KEY}"
            expected_sign = hashlib.sha256(sign_string.encode()).hexdigest().upper()
            
            if received_sign != expected_sign:
                logger.warning("Invalid payment signature")
                return "Invalid signature", 403

        order_id = form.get("m_orderid")
        if form.get("m_status") != "success":
            # Update payment status to failed
            update_imei_check(order_id=order_id, payment_status='failed', flow_status='payment_failed')
            logger.warning(f"Payment not successful for order {order_id}")
            return "Payment not successful", 400

        # Extract Payeer client details if available
        payeer_client_id = form.get("client_id", None)
        payeer_client_email = form.get("client_email", None)
        
        user_id, imei, already_processed = process_payment(
            order_id, 
            payeer_client_id=payeer_client_id, 
            payeer_client_email=payeer_client_email
        )
        
        if user_id and imei and not already_processed:
            threading.Thread(target=send_imei_result, args=(user_id, imei, order_id)).start()
            
        return "OK"
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
        # Update flow status even if payment is not yet confirmed
        update_imei_check(order_id=order_id, flow_status='payment_page_success')
        
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
    order_id = request.args.get("m_orderid")
    if order_id:
        # Update flow status to payment_page_failed
        update_imei_check(order_id=order_id, flow_status='payment_page_failed')
    
    return render_template("fail.html", message="Payment was not completed")

@app.route("/admin/dashboard")
def admin_dashboard():
    # Simple admin dashboard - in a real app, add authentication
    try:
        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM imei_checks WHERE payment_status = 'paid'")
            total_paid = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM imei_checks")
            total_requests = c.fetchone()[0]

            c.execute("SELECT COUNT(DISTINCT user_id) FROM imei_checks")
            unique_users = c.fetchone()[0]
            
            c.execute("SELECT SUM(CAST(payment_amount AS DECIMAL)) FROM imei_checks WHERE payment_status = 'paid'")
            total_revenue = c.fetchone()[0] or 0
            
            c.execute("""
                SELECT 
                    id, order_id, imei, user_id, username, check_time, 
                    imei_found, payment_status, payment_amount, 
                    payeer_client_id, payeer_client_email, flow_status
                FROM imei_checks
                ORDER BY check_time DESC
                LIMIT 50
            """)
            recent_checks = c.fetchall()
            
            # Get language statistics
            c.execute("""
                SELECT language, COUNT(*) 
                FROM user_settings 
                GROUP BY language
            """)
            language_stats = c.fetchall()
        
        release_db_connection(conn)
        
        return render_template(
            "admin_dashboard.html", 
            total_paid=total_paid,
            total_requests=total_requests,
            unique_users=unique_users,
            total_revenue=total_revenue,
            recent_checks=recent_checks,
            language_stats=language_stats
        )
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        return "Error loading dashboard", 500

def send_imei_result(user_id, imei, order_id):
    try:
        # Get user's language
        lang = get_user_language(user_id)
        
        params = {"api_key": IMEI_API_KEY, "checker": "simlock2", "number": imei}
        res = requests.get(IMEI_API_URL, params=params, timeout=15)
        
        # More detailed error handling
        if res.status_code != 200:
            logger.error(f"API error: Status {res.status_code}, Response: {res.text}")
            asyncio.run(application.bot.send_message(
                chat_id=user_id, 
                text=get_text(user_id, 'service_unavailable'),
                parse_mode="Markdown"
            ))
            
            # Update database with API error
            update_imei_check(
                order_id=order_id, 
                flow_status='api_error',
                notes=f"API error: Status {res.status_code}"
            )
            return
            
        data = res.json()
        
        # Store API response in database
        update_imei_check(
            order_id=order_id,
            api_response=psycopg2.extras.Json(data)
        )

        if 'error' in data or not any(value for key, value in data.items() if key != 'error'):
            msg = get_text(user_id, 'imei_not_found')
            
            # Update database with IMEI not found
            update_imei_check(
                order_id=order_id,
                imei_found=False,
                flow_status='imei_not_found'
            )
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
            
            # Update database with IMEI found
            update_imei_check(
                order_id=order_id,
                imei_found=True,
                flow_status='completed_successfully'
            )

        asyncio.run(application.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown"))
        
        # Notify admins about successful payment
        admin_msg = f"💰 New payment received!\n👤 User ID: {user_id}\n📱 IMEI: {imei}"
        for admin_id in ADMIN_IDS:
            try:
                asyncio.run(application.bot.send_message(chat_id=admin_id, text=admin_msg))
            except Exception as admin_err:
                logger.error(f"Failed to notify admin {admin_id}: {admin_err}")
                
    except requests.RequestException as e:
        logger.error(f"API request error: {str(e)}")
        error_msg = get_text(user_id, 'api_error')
        asyncio.run(application.bot.send_message(chat_id=user_id, text=error_msg))
        
        # Update database with API connection error
        update_imei_check(
            order_id=order_id,
            flow_status='api_connection_error',
            notes=str(e)
        )
    except Exception as e:
        logger.error(f"Sending result error: {str(e)}")
        logger.error(traceback.format_exc())
        error_msg = get_text(user_id, 'unexpected_error')
        try:
            asyncio.run(application.bot.send_message(chat_id=user_id, text=error_msg))
        except:
            logger.error(f"Failed to send error message to user {user_id}")
        
        # Update database with unexpected error
        update_imei_check(
            order_id=order_id,
            flow_status='unexpected_error',
            notes=str(e)
        )

async def set_webhook_async():
    try:
        webhook_url = f"{BASE_URL}/webhook"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        logger.error(traceback.format_exc())

def set_webhook():
    asyncio.run(set_webhook_async())

# Graceful shutdown
def shutdown_pool():
    if connection_pool:
        connection_pool.closeall()
        logger.info("Database connection pool closed")

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

if not os.path.exists('templates/admin_dashboard.html'):
    with open('templates/admin_dashboard.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Admin Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #333; }
        .stats { display: flex; flex-wrap: wrap; margin-bottom: 30px; }
        .stat-card { flex: 1; min-width: 200px; background-color: #f8f9fa; margin: 10px; padding: 20px; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .stat-value { font-size: 24px; font-weight: bold; color: #007bff; }
        .stat-label { color: #6c757d; margin-top: 5px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; overflow-x: auto; display: block; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f8f9fa; color: #333; position: sticky; top: 0; }
        tr:hover { background-color: #f1f1f1; }
        .status { padding: 5px 10px; border-radius: 3px; font-size: 12px; }
        .paid { background-color: #d4edda; color: #155724; }
        .unpaid { background-color: #f8d7da; color: #721c24; }
        .refresh { float: right; padding: 10px 15px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; }
        .flow-status { font-size: 12px; padding: 3px 6px; border-radius: 3px; background-color: #e9ecef; }
        .completed { background-color: #d4edda; color: #155724; }
        .error { background-color: #f8d7da; color: #721c24; }
        .pending { background-color: #fff3cd; color: #856404; }
        .timestamp { font-size: 12px; color: #6c757d; }
        .search-box { margin: 20px 0; padding: 10px; width: 100%; border: 1px solid #ddd; border-radius: 5px; }
        .language-stats { margin-top: 20px; }
        .language-card { display: inline-block; padding: 10px 15px; margin: 5px; border-radius: 5px; background-color: #e9ecef; }
        .language-en { background-color: #cce5ff; color: #004085; }
        .language-ru { background-color: #d1ecf1; color: #0c5460; }
    </style>
</head>
<body>
    <div class="container">
        <h1>IMEI Checker Bot - Admin Dashboard</h1>
        <a href="/admin/dashboard" class="refresh">Refresh</a>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{{ total_requests }}</div>
                <div class="stat-label">Total Requests</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ total_paid }}</div>
                <div class="stat-label">Successful Payments</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ unique_users }}</div>
                <div class="stat-label">Unique Users</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${{ "%.2f"|format(total_revenue) }}</div>
                <div class="stat-label">Total Revenue</div>
            </div>
        </div>
        
        <div class="language-stats">
            <h2>Language Preferences</h2>
            {% for lang, count in language_stats %}
            <div class="language-card language-{{ lang }}">
                {% if lang == 'en' %}🇬🇧 English{% elif lang == 'ru' %}🇷🇺 Russian{% else %}{{ lang }}{% endif %}: {{ count }} users
            </div>
            {% endfor %}
        </div>
        
        <h2>Recent IMEI Checks</h2>
        <input type="text" id="searchInput" class="search-box" placeholder="Search by IMEI, username, or user ID...">
        
        <table>
            <thead>
                <tr>
                    <th>IMEI</th>
                    <th>Time & Date</th>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>IMEI Found</th>
                    <th>Payment Status</th>
                    <th>Payeer Client</th>
                    <th>Flow Status</th>
                </tr>
            </thead>
            <tbody id="checksTable">
                {% for check in recent_checks %}
                <tr>
                    <td>{{ check[2] }}</td>
                    <td><span class="timestamp">{{ check[5].strftime('%Y-%m-%d %H:%M:%S') }}</span></td>
                    <td>{{ check[3] }}</td>
                    <td>{{ check[4] or 'N/A' }}</td>
                    <td>
                        {% if check[6] == True %}
                        <span class="status paid">Found</span>
                        {% elif check[6] == False %}
                        <span class="status unpaid">Not Found</span>
                        {% else %}
                        <span class="status">Unknown</span>
                        {% endif %}
                    </td>
                    <td>
                        {% if check[7] == 'paid' %}
                        <span class="status paid">Paid</span>
                        {% elif check[7] == 'failed' %}
                        <span class="status unpaid">Failed</span>
                        {% else %}
                        <span class="status">{{ check[7] }}</span>
                        {% endif %}
                    </td>
                    <td>
                        {% if check[9] %}
                        ID: {{ check[9] }}<br>
                        {% if check[10] %}
                        Email: {{ check[10] }}
                        {% endif %}
                        {% else %}
                        N/A
                        {% endif %}
                    </td>
                    <td>
                        {% if check[11] == 'completed_successfully' %}
                        <span class="flow-status completed">Completed</span>
                        {% elif check[11] in ['api_error', 'api_connection_error', 'unexpected_error'] %}
                        <span class="flow-status error">{{ check[11] }}</span>
                        {% elif check[11] in ['payment_initiated', 'payment_page_success'] %}
                        <span class="flow-status pending">{{ check[11] }}</span>
                        {% else %}
                        <span class="flow-status">{{ check[11] }}</span>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    
    <script>
        // Simple search functionality
        document.getElementById('searchInput').addEventListener('keyup', function() {
            const searchValue = this.value.toLowerCase();
            const table = document.getElementById('checksTable');
            const rows = table.getElementsByTagName('tr');
            
            for (let i = 0; i < rows.length; i++) {
                const imei = rows[i].cells[0].textContent.toLowerCase();
                const userId = rows[i].cells[2].textContent.toLowerCase();
                const username = rows[i].cells[3].textContent.toLowerCase();
                
                if (imei.includes(searchValue) || userId.includes(searchValue) || username.includes(searchValue)) {
                    rows[i].style.display = '';
                } else {
                    rows[i].style.display = 'none';
                }
            }
        });
    </script>
</body>
</html>''')

if __name__ == "__main__":
    try:
        # Import psycopg2.extras for JSON support
        import psycopg2.extras
        
        logger.info("Starting Telegram bot on Railway...")
        
        # Test database connection
        try:
            test_conn = get_db_connection()
            with test_conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                logger.info("Database connection test successful")
            release_db_connection(test_conn)
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            sys.exit(1)
        
        # Set webhook
        logger.info("Setting up Telegram webhook...")
        set_webhook()
        
        # Get port from Railway environment
        port = int(os.environ.get("PORT", 8080))
        logger.info(f"Starting Flask server on port {port}")
        
        # Railway requires 0.0.0.0 to bind to all interfaces
        app.run(
            host="0.0.0.0", 
            port=port,
            debug=False  # Set to False in production
        )
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal startup error: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        logger.info("Cleaning up resources...")
        shutdown_pool()
        logger.info("Shutdown complete")
