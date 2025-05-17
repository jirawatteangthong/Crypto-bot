import ccxt
import requests
from datetime import datetime
from config import api_key, secret, password, symbol

# Setup OKX
exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})
exchange.set_sandbox_mode(False)

def telegram(message):
    from config import telegram_token, telegram_chat_id
    try:
        requests.get(f'https://api.telegram.org/bot{telegram_token}/sendMessage',
                     params={'chat_id': telegram_chat_id, 'text': message})
    except Exception:
        pass

def fetch_candles(tf='15m', limit=100):
    return exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)

def fetch_price():
    return float(exchange.fetch_ticker(symbol)['last'])

def fetch_balance():
    balance = exchange.fetch_balance()
    return balance['total']['USDT']

def cancel_all_orders():
    exchange.cancel_all_orders(symbol)

def format_time():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
