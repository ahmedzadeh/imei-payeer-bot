import os
import logging
from flask import Flask, request, jsonify
import requests
import hashlib
import uuid
import base64
from urllib.parse import urlencode
from datetime import datetime, timedelta
import json
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Float, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from sqlalchemy.pool import NullPool

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TOKEN = os.getenv("TOKEN")
IMEI_API_KEY = os.getenv("IMEI_API_KEY")
PAYEER_MERCHANT_ID = os.getenv("PAYEER_MERCHANT_ID")
PAYEER_SECRET_KEY = os.getenv("PAYEER_SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# Fix PostgreSQL URL for SQLAlchemy (Railway uses postgres://, SQLAlchemy needs postgresql://)
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Constants
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
IMEI_API_URL = "https://proimei.info/en/prepaid/api"
PAYEER_PAYMENT_URL = "https://payeer.com/merchant/"
PRICE = "0.32"
ADMIN_IDS = {2103379072, 6927331058}

# Flask app
app = Flask(__name__)

# Database setup
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(50), unique=True, nullable=False, index=True)
    language = Column(String(10), default='en')
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Order(Base):
    __tablename__ = 'orders'
    
    id = Column(Integer, primary_key=True)
    order_id = Column(String(100), unique=True, nullable=False, index=True)
    user_telegram_id = Column(String(50), nullable=False, index=True)
    imei = Column(String(20), nullable=False)
    status = Column(String(20), default='pending', index=True)  # pending, paid, failed
    amount = Column(Float, default=0.32)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    paid_at = Column(DateTime, nullable=True)
    api_response = Column(Text, nullable=True)

class UserState(Base):
    __tablename__ = 'user_states'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(50), unique=True, nullable=False, index=True)
    state = Column(String(50), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Initialize in-memory storage (always initialize these)
pending_orders = {}
user_languages = {}
user_states = {}

# Initialize database
Session = None
try:
    if DATABASE_URL:
        engine = create_engine(DATABASE_URL, poolclass=NullPool, echo=False)
        Base.metadata.create_all(engine)
        Session = scoped_session(sessionmaker(bind=engine))
        logger.info("Database connected successfully")
    else:
        logger.warning("No DATABASE_URL found, using in-memory storage")
except Exception as e:
    logger.error(f"Database connection failed: {e}")
    logger.info("Falling back to in-memory storage")

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
        'help_text': "📋 How to use:\n1. Send your 15-digit IMEI\n2. Click payment button\n3. Get your result\n\n📱 *How to find IMEI:*\n• Dial \\*#06#\n• Settings → About phone → IMEI\n\n⚠️ No refunds for wrong IMEI numbers!",
        'payment_successful': "✅ Payment successful!",
        'imei_info': "📱 IMEI Info:",
        'imei_not_found': "⚠️ IMEI not found in the database. Please ensure it is correct.",
        'service_unavailable': "❌ Service temporarily unavailable. Please try again later.",
        'check_another': "🔍 Check another IMEI",
        'admin_payment_received': "💰 Payment received!",
        'admin_user': "User",
        'admin_api_response': "API Response",
        'back': "🔙 Back",
        'use_menu': "❗ Please use the menu buttons below.",
        'stats_title': "📊 *Bot Statistics*\n\n",
        'stats_total_users': "👥 Total users: {}",
        'stats_total_orders': "📦 Total orders: {}",
        'stats_paid_orders': "✅ Paid orders: {}",
        'stats_pending_orders': "⏳ Pending orders: {}",
        'stats_revenue': "💰 Total revenue: ${:.2f}",
        'stats_today': "\n📅 *Today's Stats:*\n",
        'stats_today_orders': "📦 Orders today: {}",
        'stats_today_revenue': "💰 Revenue today: ${:.2f}",
        'stats_last_7_days': "\n📈 *Last 7 Days:*\n",
        'stats_7_days_orders': "📦 Orders: {}",
        'stats_7_days_revenue': "💰 Revenue: ${:.2f}",
        'stats_no_access': "❌ You don't have access to this command."
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
        'help_text': "📋 Как использовать:\n1. Отправьте 15-значный IMEI\n2. Нажмите кнопку оплаты\n3. Получите результат\n\n📱 *Как найти IMEI:*\n• Наберите \\*#06#\n• Настройки → О телефоне → IMEI\n\n⚠️ Возврат за неверный IMEI не предоставляется!",
        'payment_successful': "✅ Оплата успешна!",
        'imei_info': "📱 Информация об IMEI:",
        'imei_not_found': "⚠️ IMEI не найден в базе данных. Пожалуйста, убедитесь, что он правильный.",
        'service_unavailable': "❌ Сервис временно недоступен. Пожалуйста, попробуйте позже.",
        'check_another': "🔍 Проверить другой IMEI",
        'admin_payment_received': "💰 Платеж получен!",
        'admin_user': "Пользователь",
        'admin_api_response': "Ответ API",
        'back': "🔙 Назад",
        'use_menu': "❗ Пожалуйста, используйте кнопки меню ниже.",
        'stats_title': "📊 *Статистика бота*\n\n",
        'stats_total_users': "👥 Всего пользователей: {}",
        'stats_total_orders': "📦 Всего заказов: {}",
        'stats_paid_orders': "✅ Оплаченных заказов: {}",
        'stats_pending_orders': "⏳ Ожидающих оплаты: {}",
        'stats_revenue': "💰 Общий доход: ${:.2f}",
        'stats_today': "\n📅 *Статистика за сегодня:*\n",
        'stats_today_orders': "📦 Заказов сегодня: {}",
        'stats_today_revenue': "💰 Доход за сегодня: ${:.2f}",
        'stats_last_7_days': "\n📈 *Последние 7 дней:*\n",
        'stats_7_days_orders': "📦 Заказов: {}",
        'stats_7_days_revenue': "💰 Доход: ${:.2f}",
        'stats_no_access': "❌ У вас нет доступа к этой команде."
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

# Database helper functions
def get_db():
    """Get database session"""
    if Session:
        return Session()
    return None

def close_db(db):
    """Close database session"""
    if db:
        db.close()

def get_or_create_user(telegram_id, language='en'):
    """Get or create user in database"""
    if not Session:
        # Fallback to in-memory storage
        user_languages[str(telegram_id)] = language
        return True
    
    db = get_db()
    try:
        user = db.query(User).filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            user = User(telegram_id=str(telegram_id), language=language)
            db.add(user)
        else:
            user.last_active = datetime.utcnow()
            user.language = language
        db.commit()
        return user
    except Exception as e:
        logger.error(f"Error in get_or_create_user: {e}")
        db.rollback()
        # Fallback to in-memory
        user_languages[str(telegram_id)] = language
        return None
    finally:
        close_db(db)

def get_user_language(telegram_id):
    """Get user language from database"""
    if not Session:
        # Fallback to in-memory storage
        return user_languages.get(str(telegram_id), 'en')
    
    db = get_db()
    try:
        user = db.query(User).filter_by(telegram_id=str(telegram_id)).first()
        return user.language if user else 'en'
    except Exception as e:
        logger.error(f"Error getting user language: {e}")
        return user_languages.get(str(telegram_id), 'en')
    finally:
        close_db(db)

def set_user_state(telegram_id, state):
    """Set user state in database"""
    if not Session:
        # Fallback to in-memory storage
        if state:
            user_states[str(telegram_id)] = state
        else:
            user_states.pop(str(telegram_id), None)
        return
    
    db = get_db()
    try:
        user_state = db.query(UserState).filter_by(telegram_id=str(telegram_id)).first()
        if user_state:
            user_state.state = state
            user_state.updated_at = datetime.utcnow()
        else:
            user_state = UserState(telegram_id=str(telegram_id), state=state)
            db.add(user_state)
        db.commit()
    except Exception as e:
        logger.error(f"Error setting user state: {e}")
        db.rollback()
        # Fallback to in-memory
        if state:
            user_states[str(telegram_id)] = state
        else:
            user_states.pop(str(telegram_id), None)
    finally:
        close_db(db)

def get_user_state(telegram_id):
    """Get user state from database"""
    if not Session:
        # Fallback to in-memory storage
        return user_states.get(str(telegram_id))
    
    db = get_db()
    try:
        user_state = db.query(UserState).filter_by(telegram_id=str(telegram_id)).first()
        return user_state.state if user_state else None
    except Exception as e:
        logger.error(f"Error getting user state: {e}")
        return user_states.get(str(telegram_id))
    finally:
        close_db(db)

def create_order(telegram_id, imei, order_id):
    """Create new order in database"""
    if not Session:
        # Fallback to in-memory storage
        pending_orders[order_id] = {
            'imei': imei,
            'user_id': telegram_id,
            'timestamp': datetime.now(),
            'status': 'pending'
        }
        return True
    
    db = get_db()
    try:
        order = Order(
            order_id=order_id,
            user_telegram_id=str(telegram_id),
            imei=imei,
            amount=float(PRICE)
        )
        db.add(order)
        db.commit()
        return order
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        db.rollback()
        # Fallback to in-memory
        pending_orders[order_id] = {
            'imei': imei,
            'user_id': telegram_id,
            'timestamp': datetime.now(),
            'status': 'pending'
        }
        return None
    finally:
        close_db(db)

def get_order(order_id):
    """Get order from database"""
    if not Session:
        # Fallback to in-memory storage
        return pending_orders.get(order_id)
    
    db = get_db()
    try:
        return db.query(Order).filter_by(order_id=order_id).first()
    except Exception as e:
        logger.error(f"Error getting order: {e}")
        return pending_orders.get(order_id)
    finally:
        close_db(db)

def update_order_status(order_id, status, api_response=None):
    """Update order status in database"""
    if not Session:
        # Fallback to in-memory storage
        if order_id in pending_orders:
            pending_orders[order_id]['status'] = status
            if api_response:
                pending_orders[order_id]['api_response'] = api_response
            return True
        return False
    
    db = get_db()
    try:
        order = db.query(Order).filter_by(order_id=order_id).first()
        if order:
            order.status = status
            if status == 'paid':
                order.paid_at = datetime.utcnow()
            if api_response:
                order.api_response = json.dumps(api_response)
            db.commit()
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating order: {e}")
        db.rollback()
        # Fallback to in-memory
        if order_id in pending_orders:
            pending_orders[order_id]['status'] = status
            if api_response:
                pending_orders[order_id]['api_response'] = api_response
        return False
    finally:
        close_db(db)

def get_stats():
    """Get statistics from database"""
    if not Session:
        # Fallback stats for in-memory storage
        total_users = len(user_languages)
        total_orders = len(pending_orders)
        paid_orders = sum(1 for order in pending_orders.values() if order['status'] == 'paid')
        pending_orders_count = sum(1 for order in pending_orders.values() if order['status'] == 'pending')
        total_revenue = paid_orders * float(PRICE)
        
        today = datetime.now().date()
        today_orders = sum(1 for order in pending_orders.values() 
                          if order['timestamp'].date() == today and order['status'] == 'paid')
        today_revenue = today_orders * float(PRICE)
        
        seven_days_ago = datetime.now() - timedelta(days=7)
        last_7_days_orders = sum(1 for order in pending_orders.values() 
                                if order['timestamp'] > seven_days_ago and order['status'] == 'paid')
        last_7_days_revenue = last_7_days_orders * float(PRICE)
        
        return {
            'total_users': total_users,
            'total_orders': total_orders,
            'paid_orders': paid_orders,
            'pending_orders': pending_orders_count,
            'total_revenue': total_revenue,
            'today_orders': today_orders,
            'today_revenue': today_revenue,
            'last_7_days_orders': last_7_days_orders,
            'last_7_days_revenue': last_7_days_revenue
        }
    
    db = get_db()
    try:
        total_users = db.query(User).count()
        total_orders = db.query(Order).count()
        paid_orders = db.query(Order).filter_by(status='paid').count()
        pending_orders_count = db.query(Order).filter_by(status='pending').count()
        
        # Revenue
        total_revenue = db.query(Order).filter_by(status='paid').count() * float(PRICE)
        
        # Today's stats
        today = datetime.utcnow().date()
        today_orders = db.query(Order).filter(
            Order.status == 'paid',
            Order.paid_at >= datetime.combine(today, datetime.min.time())
        ).count()
        today_revenue = today_orders * float(PRICE)
        
        # Last 7 days
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        last_7_days_orders = db.query(Order).filter(
            Order.status == 'paid',
            Order.paid_at >= seven_days_ago
        ).count()
        last_7_days_revenue = last_7_days_orders * float(PRICE)
        
        return {
            'total_users': total_users,
            'total_orders': total_orders,
            'paid_orders': paid_orders,
            'pending_orders': pending_orders_count,
            'total_revenue': total_revenue,
            'today_orders': today_orders,
            'today_revenue': today_revenue,
            'last_7_days_orders': last_7_days_orders,
            'last_7_days_revenue': last_7_days_revenue
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        # Return in-memory stats as fallback
        return {
            'total_users': len(user_languages),
            'total_orders': len(pending_orders),
            'paid_orders': sum(1 for order in pending_orders.values() if order['status'] == 'paid'),
            'pending_orders': sum(1 for order in pending_orders.values() if order['status'] == 'pending'),
            'total_revenue': sum(1 for order in pending_orders.values() if order['status'] == 'paid') * float(PRICE),
            'today_orders': 0,
            'today_revenue': 0,
            'last_7_days_orders': 0,
            'last_7_days_revenue': 0
        }
    finally:
        close_db(db)

# Helper functions
def get_text(user_id, key, *args):
    lang = get_user_language(user_id)
    text = texts.get(lang, texts['en']).get(key, key)
    return text.format(*args) if args else text

def get_field_label(user_id, field):
    lang = get_user_language(user_id)
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
        result = response.json()
        
        if not result.get('ok'):
            logger.error(f"Failed to send message: {result}")
            # If Markdown parsing failed, try again without parse_mode
            if parse_mode and 'parse entities' in result.get('description', ''):
                logger.info("Retrying without parse_mode")
                data.pop('parse_mode', None)
                response = requests.post(f"{TELEGRAM_API}/sendMessage", json=data)
                result = response.json()
        
        logger.info(f"Message sent: {result}")
        return result
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return None

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
    """Handle language selection"""
    get_or_create_user(chat_id, language)
    
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

def is_button_match(text, button_key, user_id):
    """Check if text matches a button, handling emoji variations"""
    expected_text = get_text(user_id, button_key)
    
    # Direct match
    if text == expected_text:
        return True
    
    # Check if the key words are present (ignoring emojis)
    if button_key == 'help' and ('help' in text.lower() or 'помощь' in text.lower()):
        return True
    elif button_key == 'check_imei' and ('check' in text.lower() or 'imei' in text.lower() or 'проверить' in text.lower()):
        return True
    elif button_key == 'check_another' and ('another' in text.lower() or 'другой' in text.lower()):
        return True
    elif button_key == 'back' and ('back' in text.lower() or 'назад' in text.lower()):
        return True
    
    return False

def handle_text(chat_id, text):
    """Handle text messages"""
    logger.info(f"Handling text from {chat_id}: '{text}'")
    
    # Get user language from database
    user_lang = get_user_language(chat_id)
    if not user_lang:
        handle_start(chat_id)
        return
    
    # Get user state from database
    user_state = get_user_state(chat_id)
    
    # Check for button matches
    if is_button_match(text, 'help', chat_id):
        logger.info(f"Help button detected for user {chat_id}")
        set_user_state(chat_id, None)  # Clear state
        send_message(chat_id, get_text(chat_id, 'help_text'), parse_mode="Markdown")
        return
    
    elif is_button_match(text, 'check_imei', chat_id) or is_button_match(text, 'check_another', chat_id):
        logger.info(f"Check IMEI button detected for user {chat_id}")
        set_user_state(chat_id, "awaiting_imei")
        send_message(chat_id, get_text(chat_id, 'enter_imei'))
        return
    
    elif is_button_match(text, 'back', chat_id):
        logger.info(f"Back button detected for user {chat_id}")
        set_user_state(chat_id, None)  # Clear state
        keyboard = {
            "keyboard": [
                [{"text": get_text(chat_id, 'check_imei')}],
                [{"text": get_text(chat_id, 'help')}]
            ],
            "resize_keyboard": True
        }
        send_message(chat_id, get_text(chat_id, 'welcome'), reply_markup=keyboard)
        return
    
    # Handle IMEI input
    elif user_state == "awaiting_imei":
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
            set_user_state(chat_id, None)
            return
        
        order_id = str(uuid.uuid4())
        
        # Create order in database
        create_order(chat_id, imei, order_id)
        
        # Generate payment URL
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
        set_user_state(chat_id, None)
    
    else:
        # Show main menu
        keyboard = {
            "keyboard": [
                [{"text": get_text(chat_id, 'check_imei')}],
                [{"text": get_text(chat_id, 'help')}]
            ],
            "resize_keyboard": True
        }
        send_message(chat_id, get_text(chat_id, 'use_menu'), reply_markup=keyboard)

def handle_stats(chat_id):
    """Handle /stats command - only for admins"""
    # Check if user is admin
    if chat_id not in ADMIN_IDS:
        send_message(chat_id, get_text(chat_id, 'stats_no_access'))
        return
    
    # Get stats from database
    stats = get_stats()
    
    # Build stats message
    msg = get_text(chat_id, 'stats_title')
    msg += f"{get_text(chat_id, 'stats_total_users', stats.get('total_users', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_total_orders', stats.get('total_orders', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_paid_orders', stats.get('paid_orders', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_pending_orders', stats.get('pending_orders', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_revenue', stats.get('total_revenue', 0))}\n"
    
    msg += get_text(chat_id, 'stats_today')
    msg += f"{get_text(chat_id, 'stats_today_orders', stats.get('today_orders', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_today_revenue', stats.get('today_revenue', 0))}\n"
    
    msg += get_text(chat_id, 'stats_last_7_days')
    msg += f"{get_text(chat_id, 'stats_7_days_orders', stats.get('last_7_days_orders', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_7_days_revenue', stats.get('last_7_days_revenue', 0))}"
    
    send_message(chat_id, msg, parse_mode="Markdown")

def send_imei_result(user_id, imei, order_id):
    """Send IMEI check result to user"""
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
                
                # Update order with API response
                update_order_status(order_id, 'paid', data)
                
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

# Flask routes
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
                elif text.startswith('/stats'):
                    handle_stats(chat_id)
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
        if form.get("m_status") == "success" and order_id:
            order = get_order(order_id)
            if not Session and order_id in pending_orders:
                # Fallback for in-memory storage
                order = pending_orders[order_id]
                if order['status'] == 'pending':
                    order['status'] = 'paid'
                    send_imei_result(order['user_id'], order['imei'], order_id)
            elif order and order.status == 'pending':
                update_order_status(order_id, 'paid')
                send_imei_result(order.user_telegram_id, order.imei, order_id)
        
        return order_id or "OK"
    except Exception as e:
        logger.error(f"Payeer error: {e}")
        return "Error", 500

@app.route("/success")
def success():
    order_id = request.args.get("m_orderid")
    if order_id:
        order = get_order(order_id)
        if not Session and order_id in pending_orders:
            # Fallback for in-memory storage
            order = pending_orders[order_id]
            if order['status'] == 'pending':
                order['status'] = 'paid'
                send_imei_result(order['user_id'], order['imei'], order_id)
        elif order and order.status == 'pending':
            update_order_status(order_id, 'paid')
            send_imei_result(order.user_telegram_id, order.imei, order_id)
    
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

# Add cleanup for database connections
@app.teardown_appcontext
def shutdown_session(exception=None):
    if Session:
        Session.remove()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
