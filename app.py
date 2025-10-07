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
import time
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
        # Add connection pool settings for better reliability
        engine = create_engine(
            DATABASE_URL, 
            poolclass=NullPool,
            echo=False,
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000"
            }
        )
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
        'help_text': "📋 How to use:\n1. Send your 15-digit IMEI\n2. Click payment button\n3. Get your result\n\n📱 *How to find IMEI:*\n• Dial \\*#06#\n• Settings → About phone → IMEI\n\n⚠️ *IMPORTANT:*\n• This service works ONLY with Apple devices (iPhone, iPad, Apple Watch)\n• Android and other devices are NOT supported\n• No refunds for wrong IMEI numbers!\n\n*Available commands:*\n/start - Start bot\n/myorders - View your order history\n/help - Show this help",
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
        'stats_users_with_orders': "👥 Users who checked IMEI: {}",
        'stats_users_who_paid': "💳 Users who paid: {}",
        'stats_conversion_rate': "📈 Conversion rate: {:.1f}%",
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
        'stats_no_access': "❌ You don't have access to this command.",
        'recent_orders_title': "📋 *Recent Orders*\n\n",
        'order_info': "Order #{}\n👤 User: {}\n📱 IMEI: {}\n💰 Status: {}\n📅 Date: {}\n",
        'no_orders': "No orders found.",
        'user_orders_title': "📋 *Your Order History*\n\n",
        'search_results_title': "*Search Results for IMEI {}:*\n\n",
        'search_usage': "Usage: /search <IMEI>",
        'admin_help': "\n\n*Admin commands:*\n/stats - View statistics\n/orders - View recent orders\n/search <IMEI> - Search for IMEI"
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
        'help_text': "📋 Как использовать:\n1. Отправьте 15-значный IMEI\n2. Нажмите кнопку оплаты\n3. Получите результат\n\n📱 *Как найти IMEI:*\n• Наберите \\*#06#\n• Настройки → О телефоне → IMEI\n\n⚠️ *ВАЖНО:*\n• Этот сервис работает ТОЛЬКО с устройствами Apple (iPhone, iPad, Apple Watch)\n• Android и другие устройства НЕ поддерживаются\n• Возврат за неверный IMEI не предоставляется!\n\n*Доступные команды:*\n/start - Запустить бота\n/myorders - История ваших заказов\n/help - Показать эту справку",
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
        'stats_users_with_orders': "👥 Пользователей проверили IMEI: {}",
        'stats_users_who_paid': "💳 Пользователей оплатили: {}",
        'stats_conversion_rate': "📈 Конверсия: {:.1f}%",
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
        'stats_no_access': "❌ У вас нет доступа к этой команде.",
        'recent_orders_title': "📋 *Последние заказы*\n\n",
        'order_info': "Заказ #{}\n👤 Пользователь: {}\n📱 IMEI: {}\n💰 Статус: {}\n📅 Дата: {}\n",
        'no_orders': "Заказы не найдены.",
        'user_orders_title': "📋 *История ваших заказов*\n\n",
        'search_results_title': "*Результаты поиска IMEI {}:*\n\n",
        'search_usage': "Использование: /search <IMEI>",
        'admin_help': "\n\n*Команды администратора:*\n/stats - Статистика\n/orders - Последние заказы\n/search <IMEI> - Поиск по IMEI"
    }
}

# Field labels for IMEI results - Updated for new API fields
field_labels = {
    'en': {
        'imei': "*IMEI:*",
        'meid': "*MEID:*",
        'serial': "*Serial Number:*",
        'model': "*Model:*",
        'purchased_in': "*Purchased In:*",
        'purchase_date': "*Estimated Purchase Date:*",
        'valid_purchase': "*Valid Purchase Date:*",
        'registered': "*Registered Device:*",
        'activated': "*Activated:*",
        'phone_support': "*Phone Technical Support:*",
        'warranty': "*Repairs & Service Coverage:*",
        'warranty_start': "*Warranty Start Date:*",
        'warranty_end': "*Warranty End Date:*",
        'warranty_days': "*Warranty Remaining Days:*",
        'find_my': "*Find my iPhone:*",
        'loaner': "*Loaner:*",
        'replaced': "*Is Replaced:*",
        'carrier': "*Carrier Name:*",
        'next_policy': "*Next Activation Policy ID:*",
        'simlock': "*SIM Lock:*"
    },
    'ru': {
        'imei': "*IMEI:*",
        'meid': "*MEID:*",
        'serial': "*Серийный номер:*",
        'model': "*Модель:*",
        'purchased_in': "*Куплено в:*",
        'purchase_date': "*Предполагаемая дата покупки:*",
        'valid_purchase': "*Действительная дата покупки:*",
        'registered': "*Зарегистрированное устройство:*",
        'activated': "*Активировано:*",
        'phone_support': "*Техническая поддержка по телефону:*",
        'warranty': "*Ремонт и обслуживание:*",
        'warranty_start': "*Начало гарантии:*",
        'warranty_end': "*Окончание гарантии:*",
        'warranty_days': "*Осталось дней гарантии:*",
        'find_my': "*Найти iPhone:*",        
        'loaner': "*Подменное устройство:*",
        'replaced': "*Заменено:*",
        'carrier': "*Оператор:*",
        'next_policy': "*ID следующей политики активации:*",
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
        # Enhanced in-memory stats
        total_users = len(user_languages)
        total_orders = len(pending_orders)
        paid_orders = sum(1 for order in pending_orders.values() if order['status'] == 'paid')
        pending_orders_count = sum(1 for order in pending_orders.values() if order['status'] == 'pending')
        
        # Calculate conversion rate
        conversion_rate = (paid_orders / total_orders * 100) if total_orders > 0 else 0
        
        # Users who created orders
        users_with_orders = len(set(order['user_id'] for order in pending_orders.values()))
        
        # Users who paid
        users_who_paid = len(set(order['user_id'] for order in pending_orders.values() if order['status'] == 'paid'))
        
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
            'users_with_orders': users_with_orders,
            'users_who_paid': users_who_paid,
            'total_orders': total_orders,
            'paid_orders': paid_orders,
            'pending_orders': pending_orders_count,
            'conversion_rate': conversion_rate,
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
        
        # Conversion rate
        conversion_rate = (paid_orders / total_orders * 100) if total_orders > 0 else 0
        
        # Users who created orders
        users_with_orders = db.query(Order.user_telegram_id).distinct().count()
        
        # Users who paid
        users_who_paid = db.query(Order.user_telegram_id).filter(Order.status == 'paid').distinct().count()
        
        # Revenue
        total_revenue = paid_orders * float(PRICE)
        
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
            'users_with_orders': users_with_orders,
            'users_who_paid': users_who_paid,
            'total_orders': total_orders,
            'paid_orders': paid_orders,
            'pending_orders': pending_orders_count,
            'conversion_rate': conversion_rate,
            'total_revenue': total_revenue,
            'today_orders': today_orders,
            'today_revenue': today_revenue,
            'last_7_days_orders': last_7_days_orders,
            'last_7_days_revenue': last_7_days_revenue
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return {
            'total_users': 0,
            'users_with_orders': 0,
            'users_who_paid': 0,
            'total_orders': 0,
            'paid_orders': 0,
            'pending_orders': 0,
            'conversion_rate': 0,
            'total_revenue': 0,
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
        help_text = get_text(chat_id, 'help_text')
        if chat_id in ADMIN_IDS:
            help_text += get_text(chat_id, 'admin_help')
        send_message(chat_id, help_text, parse_mode="Markdown")
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
    msg += f"{get_text(chat_id, 'stats_users_with_orders', stats.get('users_with_orders', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_users_who_paid', stats.get('users_who_paid', 0))}\n"
    msg += f"{get_text(chat_id, 'stats_conversion_rate', stats.get('conversion_rate', 0))}\n\n"
    
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

def handle_recent_orders(chat_id, limit=10):
    """Show recent orders - admin only"""
    if chat_id not in ADMIN_IDS:
        send_message(chat_id, get_text(chat_id, 'stats_no_access'))
        return
    
    if not Session:
        # In-memory version
        recent = sorted(pending_orders.items(), 
                       key=lambda x: x[1]['timestamp'], 
                       reverse=True)[:limit]
        
        if not recent:
            send_message(chat_id, get_text(chat_id, 'no_orders'))
            return
        
        msg = get_text(chat_id, 'recent_orders_title')
        for order_id, order in recent:
            msg += get_text(chat_id, 'order_info',
                          order_id[:8],  # Short ID
                          order['user_id'],
                          order['imei'],
                          order['status'],
                          order['timestamp'].strftime('%Y-%m-%d %H:%M'))
            msg += "\n"
        
        send_message(chat_id, msg, parse_mode="Markdown")
    else:
        # Database version
        db = get_db()
        try:
            orders = db.query(Order).order_by(Order.created_at.desc()).limit(limit).all()
            
            if not orders:
                send_message(chat_id, get_text(chat_id, 'no_orders'))
                return
            
            msg = get_text(chat_id, 'recent_orders_title')
            for order in orders:
                msg += get_text(chat_id, 'order_info',
                              order.order_id[:8],  # Short ID
                              order.user_telegram_id,
                              order.imei,
                              order.status,
                              order.created_at.strftime('%Y-%m-%d %H:%M'))
                msg += "\n"
            
            send_message(chat_id, msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error getting recent orders: {e}")
            send_message(chat_id, "Error retrieving orders")
        finally:
            close_db(db)

def handle_my_orders(chat_id):
    """Show user's own order history"""
    if not Session:
        # In-memory version
        my_orders = [(oid, o) for oid, o in pending_orders.items() 
                     if str(o['user_id']) == str(chat_id)]
        my_orders.sort(key=lambda x: x[1]['timestamp'], reverse=True)
        
        if not my_orders:
            send_message(chat_id, get_text(chat_id, 'no_orders'))
            return
        
        msg = get_text(chat_id, 'user_orders_title')
        for order_id, order in my_orders[:20]:  # Last 20 orders
            status_emoji = "✅" if order['status'] == 'paid' else "⏳"
            msg += f"{status_emoji} IMEI: `{order['imei']}`\n"
            msg += f"   Date: {order['timestamp'].strftime('%Y-%m-%d %H:%M')}\n\n"
        
        send_message(chat_id, msg, parse_mode="Markdown")
    else:
        # Database version
        db = get_db()
        try:
            orders = db.query(Order).filter_by(
                user_telegram_id=str(chat_id)
            ).order_by(Order.created_at.desc()).limit(20).all()
            
            if not orders:
                send_message(chat_id, get_text(chat_id, 'no_orders'))
                return
            
            msg = get_text(chat_id, 'user_orders_title')
            for order in orders:
                status_emoji = "✅" if order.status == 'paid' else "⏳"
                msg += f"{status_emoji} IMEI: `{order.imei}`\n"
                msg += f"   Date: {order.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            
            send_message(chat_id, msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error getting user orders: {e}")
            send_message(chat_id, "Error retrieving your orders")
        finally:
            close_db(db)

def search_imei_history(chat_id, imei):
    """Search for specific IMEI in history - admin only"""
    if chat_id not in ADMIN_IDS:
        send_message(chat_id, get_text(chat_id, 'stats_no_access'))
        return
    
    if not Session:
        # In-memory search
        found = [(oid, o) for oid, o in pending_orders.items() 
                 if imei in o['imei']]
        
        if not found:
            send_message(chat_id, f"No orders found for IMEI: {imei}")
            return
        
        msg = get_text(chat_id, 'search_results_title', imei)
        for order_id, order in found:
            msg += f"User: {order['user_id']}\n"
            msg += f"Status: {order['status']}\n"
            msg += f"Date: {order['timestamp'].strftime('%Y-%m-%d %H:%M')}\n\n"
        
        send_message(chat_id, msg, parse_mode="Markdown")
    else:
        # Database search
        db = get_db()
        try:
            orders = db.query(Order).filter(
                Order.imei.like(f'%{imei}%')
            ).order_by(Order.created_at.desc()).all()
            
            if not orders:
                send_message(chat_id, f"No orders found for IMEI: {imei}")
                return
            
            msg = get_text(chat_id, 'search_results_title', imei)
            for order in orders:
                msg += f"User: {order.user_telegram_id}\n"
                msg += f"Status: {order.status}\n"
                msg += f"Date: {order.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                if order.api_response:
                    try:
                        api_data = json.loads(order.api_response)
                        if 'Model' in api_data:
                            msg += f"Model: {api_data['Model']}\n"
                    except:
                        pass
                msg += "\n"
            
            send_message(chat_id, msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error searching IMEI: {e}")
            send_message(chat_id, "Error searching orders")
        finally:
            close_db(db)

def send_imei_result(user_id, imei, order_id):
    """Send IMEI check result to user with retry logic"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Calling IMEI API for: {imei} (attempt {attempt + 1}/{max_retries})")
            
            # Use simlock checker (changed from simlock3)
            params = {
                "api_key": IMEI_API_KEY,
                "checker": "simlock",  # Changed from simlock3 to simlock
                "number": imei
            }
            
            # Make the API request with increased timeout
            res = requests.get(IMEI_API_URL, params=params, timeout=30)
            
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
                        # Format the response with new API fields
                        msg = f"*{get_text(user_id, 'payment_successful')}*\n\n*{get_text(user_id, 'imei_info')}*\n\n"
                        
                        # Format the response with new API fields
                        if 'IMEI' in data:
                            msg += f"📱 {get_field_label(user_id, 'imei')} `{data['IMEI']}`\n"
                        if 'MEID' in data:
                            msg += f"📟 {get_field_label(user_id, 'meid')} `{data['MEID']}`\n"
                        if 'Serial Number' in data:
                            msg += f"🔢 {get_field_label(user_id, 'serial')} `{data['Serial Number']}`\n"
                        if 'Model' in data:
                            msg += f"📱 {get_field_label(user_id, 'model')} `{data['Model']}`\n\n"
                        
                        # Purchase information
                        if 'Purchased In' in data:
                            msg += f"🌍 {get_field_label(user_id, 'purchased_in')} `{data['Purchased In']}`\n"
                        if 'Estimated Purchase Date' in data:
                            msg += f"📅 {get_field_label(user_id, 'purchase_date')} `{data['Estimated Purchase Date']}`\n"
                        if 'Valid Purchase Date' in data:
                            msg += f"✅ {get_field_label(user_id, 'valid_purchase')} `{data['Valid Purchase Date']}`\n\n"
                        
                        # Device status
                        if 'Registered Device' in data:
                            msg += f"📋 {get_field_label(user_id, 'registered')} `{data['Registered Device']}`\n"
                        if 'Activated' in data:
                            msg += f"🔓 {get_field_label(user_id, 'activated')} `{data['Activated']}`\n"
                        if 'Find my iPhone' in data:
                            msg += f"📍 {get_field_label(user_id, 'find_my')} `{data['Find my iPhone']}`\n"
                        if 'Loaner' in data:
                            msg += f"🔄 {get_field_label(user_id, 'loaner')} `{data['Loaner']}`\n"
                        if 'is replaced' in data:
                            msg += f"🔄 {get_field_label(user_id, 'replaced')} `{data['is replaced']}`\n\n"
                        
                        # Support and warranty
                        if 'Phone Technical Support' in data:
                            msg += f"📞 {get_field_label(user_id, 'phone_support')} `{data['Phone Technical Support']}`\n"
                        if 'Repairs & Service Coverage' in data:
                            msg += f"🛠 {get_field_label(user_id, 'warranty')} `{data['Repairs & Service Coverage']}`\n"
                        if 'Warranty Start Date' in data:
                            msg += f"📅 {get_field_label(user_id, 'warranty_start')} `{data['Warranty Start Date']}`\n"
                        if 'Warranty End Date' in data:
                            msg += f"📅 {get_field_label(user_id, 'warranty_end')} `{data['Warranty End Date']}`\n"
                        if 'Warranty Remaining Days' in data:
                            msg += f"⏳ {get_field_label(user_id, 'warranty_days')} `{data['Warranty Remaining Days']}`\n\n"
                        
                        # Carrier and SIM lock
                        if 'Carrier Name' in data:
                            msg += f"📡 {get_field_label(user_id, 'carrier')} `{data['Carrier Name']}`\n"
                        if 'Next Activation Policy ID' in data:
                            msg += f"🔢 {get_field_label(user_id, 'next_policy')} `{data['Next Activation Policy ID']}`\n"
                        if 'SIM Lock' in data:
                            msg += f"🔒 {get_field_label(user_id, 'simlock')} `{data['SIM Lock']}`\n"
                    
                        send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id), parse_mode="Markdown")
                    
                    # Notify admins with full response
                    notify_admins(user_id, imei, data)
                    return  # Success, exit the function
                    
                except json.JSONDecodeError:
                    logger.error("Failed to parse API response as JSON")
                    msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
                    send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
                    notify_admins(user_id, imei, {"error": "Invalid JSON response", "raw": res.text})
                    return
                    
            else:
                logger.error(f"API returned status code: {res.status_code}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                else:
                    msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
                    send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
                    notify_admins(user_id, imei, {"error": f"Status code: {res.status_code}"})
                    return
                
        except requests.Timeout:
            logger.error(f"API request timed out (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
            else:
                msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
                send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
                notify_admins(user_id, imei, {"error": "Timeout after retries"})
                return
            
        except Exception as e:
            logger.error(f"IMEI API error: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
            else:
                msg = f"{get_text(user_id, 'payment_successful')}\n\n{get_text(user_id, 'service_unavailable')}"
                send_message(user_id, msg, reply_markup=quick_action_keyboard(user_id))
                notify_admins(user_id, imei, {"error": str(e)})
                return

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
                elif text.startswith('/orders'):
                    handle_recent_orders(chat_id)
                elif text.startswith('/myorders'):
                    handle_my_orders(chat_id)
                elif text.startswith('/search'):
                    # Extract IMEI from command like "/search 123456789012345"
                    parts = text.split()
                    if len(parts) > 1:
                        search_imei_history(chat_id, parts[1])
                    else:
                        send_message(chat_id, get_text(chat_id, 'search_usage'))
                elif text.startswith('/help'):
                    help_text = get_text(chat_id, 'help_text')
                    if chat_id in ADMIN_IDS:
                        help_text += get_text(chat_id, 'admin_help')
                    send_message(chat_id, help_text, parse_mode="Markdown")
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
            
            # Check if order exists and hasn't been processed yet
            if not Session and order_id in pending_orders:
                # Fallback for in-memory storage
                order = pending_orders[order_id]
                if order['status'] == 'pending':
                    order['status'] = 'paid'
                    send_imei_result(order['user_id'], order['imei'], order_id)
                elif order['status'] == 'paid':
                    logger.info(f"Order {order_id} already processed, skipping")
            elif order:
                if order.status == 'pending':
                    update_order_status(order_id, 'paid')
                    send_imei_result(order.user_telegram_id, order.imei, order_id)
                elif order.status == 'paid':
                    logger.info(f"Order {order_id} already processed, skipping")
        
        # Return proper response to stop Payeer from retrying
        return f"{order_id}|success", 200
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
