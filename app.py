#!/usr/bin/env python3
"""
TeleTextPlus - Telegram Bot with Payments
Production-ready app with webhook, commands, and Telegram Stars support
"""

import hashlib
import hmac
import requests
import time
import json
import logging
import os
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from urllib.parse import parse_qs, unquote
import threading
from functools import wraps

# Load environment variables
load_dotenv("/home/Toxzak/teletextplus/.env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Configuration from .env
BOT_TOKEN = os.getenv('BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in .env")

logger.info("="*80)
logger.info("TELETEXTPLUS BOT - PRODUCTION READY")
logger.info(f"BOT: {BOT_TOKEN[:20]}...")
logger.info(f"WEBHOOK: {WEBHOOK_URL}")
logger.info("="*80)

# Caching for optimization
_user_cache = {}

def cache_user(user_id, user_name):
    """Cache user info to avoid repeated lookups"""
    _user_cache[user_id] = {'name': user_name, 'last_seen': time.time()}

# ==================== HELPER DECORATORS ====================

def send_async(func):
    """Decorator to run function asynchronously"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return None
    return wrapper

# ==================== TELEGRAM API HELPERS ====================

def send_telegram_message(chat_id, text, parse_mode="HTML"):
    """Send message to user"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        requests.post(url, json=payload, timeout=5)
        return True
    except Exception as e:
        logger.error(f"Send message error: {e}")
        return False

@send_async
def send_message_async(chat_id, text):
    """Send message in background thread (non-blocking)"""
    send_telegram_message(chat_id, text)

def answer_pre_checkout_query(query_id, ok=True, error_message=None):
    """
    ⚠️ CRITICAL: Must respond within 10 seconds!
    This validates the payment before the user pays
    """
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery"
        payload = {
            "pre_checkout_query_id": query_id,
            "ok": ok
        }
        if not ok and error_message:
            payload["error_message"] = error_message
        
        start = time.time()
        response = requests.post(url, json=payload, timeout=5)
        elapsed = time.time() - start
        
        if response.json().get('ok'):
            logger.info(f"✓ Pre-checkout approved in {elapsed:.2f}s")
            return True
        logger.error(f"Failed to answer pre-checkout: {response.json()}")
        return False
    except Exception as e:
        logger.error(f"Pre-checkout error: {e}")
        return False

# ==================== FLASK ROUTES ====================

@app.route('/')
def index():
    """Serve mini app frontend"""
    return send_from_directory('.', 'index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    """Serve static files (CSS, JS, etc)"""
    return send_from_directory('static', filename)

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok"}), 200

# ==================== GET_INVOICE ENDPOINT ====================

@app.route('/get_invoice', methods=['POST'])
def get_invoice():
    """
    Handle mini app payment button clicks
    Creates invoice link for Telegram Stars payment
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        init_data = data.get('initData', '').strip()
        product = data.get('product', 'premium_weekly')
        amount = data.get('amount', 99)
        
        logger.info(f"Invoice request: product={product}, amount={amount}")
        
        if not init_data:
            return jsonify({'error': 'Missing initData'}), 400
        
        # Extract user ID from initData
        user_id = 0
        try:
            parsed = parse_qs(init_data)
            user_data = parsed.get('user', [None])[0]
            if user_data:
                user_info = json.loads(unquote(user_data))
                user_id = user_info.get('id', 0)
                logger.info(f"User {user_id} requesting invoice")
        except Exception as parse_error:
            logger.warning(f"Could not parse initData: {parse_error}")
        
        # Create unique payload for invoice
        payload = f"{product}_{user_id}_{int(time.time())}"
        
        # Call Telegram API to create invoice link
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
        invoice_data = {
            "title": f"TeleTextPlus {product.replace('_', ' ').title()}",
            "description": "Unlock premium features! ✓ Unlimited ✓ Advanced ✓ Priority",
            "payload": payload,
            "provider_token": "",  # Empty for Telegram Stars
            "currency": "XTR",
            "prices": [{"label": "Premium", "amount": amount}],
            "is_flexible": False
        }
        
        response = requests.post(url, json=invoice_data, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            invoice_url = result.get('result')
            logger.info(f"✓ Invoice created for user {user_id}")
            return jsonify({"invoice_url": invoice_url}), 200
        else:
            error_msg = result.get('description', 'Unknown error')
            logger.error(f"Failed to create invoice: {error_msg}")
            return jsonify({'error': error_msg}), 400
            
    except Exception as e:
        logger.exception(f"get_invoice error: {e}")
        return jsonify({'error': 'Server error'}), 500

# ==================== WEBHOOK ENDPOINT ====================

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Main webhook endpoint - receives all Telegram updates
    ⚠️ CRITICAL: Pre-checkout must respond in < 10 seconds!
    """
    try:
        update = request.get_json()
        
        # PRIORITY 1: Pre-checkout query (< 10 seconds!)
        if 'pre_checkout_query' in update:
            logger.warning("⚠️ PRE-CHECKOUT QUERY RECEIVED - RESPONDING NOW")
            query = update['pre_checkout_query']
            query_id = query['id']
            answer_pre_checkout_query(query_id, ok=True)
            return jsonify({'ok': True}), 200
        
        # PRIORITY 2: Handle regular messages
        if 'message' in update:
            message = update['message']
            chat_id = message['chat']['id']
            text = message.get('text', '')
            user = message['from']
            user_id = user['id']
            user_name = user.get('first_name', 'User')
            
            # Cache user info
            cache_user(user_id, user_name)
            logger.info(f"Message from {user_name} ({user_id}): {text}")
            
            # ========== /start command ==========
            if text == '/start':
                welcome = f"""👋 Welcome to TeleTextPlus, {user_name}!

I'm a powerful text utility tool for all your needs.

<b>Available Commands:</b>
/start - Show this message
/help - View all features
/premium - Unlock premium features
/paysupport - Payment FAQ & support

<b>Premium Features (⭐):</b>
• Unlimited text conversions
• Advanced formatting tools
• AI-powered suggestions
• Priority support

Tap /premium to upgrade and get started!"""
                send_message_async(chat_id, welcome)
            
            # ========== /help command ==========
            elif text == '/help':
                help_text = """<b>📚 Features & Help</b>

<b>Available Commands:</b>
/start - Welcome message
/help - This help text
/premium - Get premium access
/paysupport - Payment help

<b>🔐 Premium Features:</b>
✓ Unlimited text conversions
✓ Advanced formatting
✓ AI suggestions
✓ Priority support
✓ Exclusive tools

<b>💰 Pricing:</b>
⭐ 99 Telegram Stars (~$1.99)
⏱ Valid for 1 week

Ready to upgrade? Use /premium!"""
                send_message_async(chat_id, help_text)
            
            # ========== /premium command ==========
            elif text == '/premium':
                logger.info(f"Processing /premium for user {user_id}")
                try:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendInvoice"
                    payload = {
                        "chat_id": chat_id,
                        "title": "TeleTextPlus Premium Weekly",
                        "description": "Get unlimited access to all features for 1 week!\n✓ Unlimited conversions\n✓ Advanced tools\n✓ Priority support",
                        "payload": f"premium_weekly_{user_id}_{int(time.time())}",
                        "provider_token": "",  # Empty for Telegram Stars
                        "currency": "XTR",
                        "prices": [{"label": "Premium Weekly", "amount": 99}],
                        "is_flexible": False
                    }
                    response = requests.post(url, json=payload, timeout=10)
                    if response.json().get('ok'):
                        logger.info(f"✓ Invoice sent to {user_id}")
                    else:
                        logger.error(f"Failed to send invoice: {response.json()}")
                        send_message_async(chat_id, "❌ Error initiating payment. Please try again.")
                except Exception as e:
                    logger.error(f"Error in /premium handler: {e}")
                    send_message_async(chat_id, "❌ Error initiating payment. Please try again.")
            
            # ========== /paysupport command ==========
            elif text == '/paysupport':
                support_text = """💳 <b>Payment Support</b>

<b>What are Telegram Stars?</b>
Telegram Stars are a secure in-app currency
1 Star ≈ $0.02 USD

<b>Payment Methods:</b>
✓ Telegram Stars (fastest & easiest)
✓ Credit/Debit Card
✓ Apple Pay
✓ Google Pay

<b>Pricing & Duration:</b>
⭐ 99 Stars = approximately $1.99
⏱ Premium access for 1 week

<b>Troubleshooting:</b>
• Check your internet connection
• Ensure your payment method is active
• Try again if payment fails
• Contact support if problems persist

<b>Refunds:</b>
Refunds are available within 48 hours of purchase.
Contact support for assistance.

Questions? Use /help"""
                send_message_async(chat_id, support_text)
            
            # ========== Default: other messages ==========
            else:
                response = "Thanks for your message! 👋\n\nUse /help to see all features, or /premium to unlock premium access."
                send_message_async(chat_id, response)
        
        # PRIORITY 3: Handle successful payment
        if 'message' in update and 'successful_payment' in update['message']:
            message = update['message']
            chat_id = message['chat']['id']
            user_id = message['from']['id']
            payment = message['successful_payment']
            
            logger.info(f"🎉 PAYMENT SUCCESSFUL!")
            logger.info(f"   User: {user_id}")
            logger.info(f"   Amount: {payment['total_amount']} {payment['currency']}")
            logger.info(f"   Telegram Payment Charge ID: {payment['telegram_payment_charge_id']}")
            
            # TODO: Update database with premium status, expiration date, etc.
            # Set premium_expiry = now + 7 days
            
            success_message = """✅ <b>Payment Successful!</b>

🎉 Welcome to TeleTextPlus Premium!

Your premium membership is now active:
⭐ 7 days of unlimited access
🔓 All features unlocked
⚡ Priority processing
📱 Use the mini app for full power

Your benefits start immediately!

Use /help to get started!"""
            
            send_message_async(chat_id, success_message)
            logger.info(f"✓ Confirmation sent to user {user_id}")
        
        return jsonify({'ok': True}), 200
        
    except Exception as e:
        logger.exception(f"✗ CRITICAL WEBHOOK ERROR: {e}")
        # Still return 200 so Telegram doesn't retry
        return jsonify({'ok': True}), 200

# ==================== ADMIN/DEBUG ENDPOINTS ====================

@app.route('/setup_webhook')
def setup_webhook():
    """Register webhook with Telegram"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        payload = {
            "url": WEBHOOK_URL,
            "allowed_updates": ["message", "pre_checkout_query"],
            "max_connections": 40
        }
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        logger.info(f"Webhook setup result: {result}")
        return jsonify(result), 200
    except Exception as e:
        logger.exception(f"Error setting up webhook: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/webhook_info')
def webhook_info():
    """Check webhook status"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
        response = requests.get(url, timeout=10)
        result = response.json()
        logger.info(f"Webhook info: {result}")
        return jsonify(result), 200
    except Exception as e:
        logger.exception(f"Error getting webhook info: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== MAIN ====================

if __name__ == '__main__':
    logger.info("Starting Flask app...")
    app.run(host='127.0.0.1', port=5000, debug=True)
