import ccxt
import time
from config import OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSWORD, SYMBOL

def connect_okx():
    return ccxt.okx({
        'apiKey': OKX_API_KEY,
        'secret': OKX_API_SECRET,
        'password': OKX_API_PASSWORD,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })

def fetch_ohlcv(tf='5m', limit=100):
    exchange = connect_okx()
    raw = exchange.fetch_ohlcv(SYMBOL, timeframe=tf, limit=limit)
    return [{'timestamp': i[0], 'open': i[1], 'high': i[2], 'low': i[3], 'close': i[4]} for i in raw]

def fetch_current_price():
    exchange = connect_okx()
    ticker = exchange.fetch_ticker(SYMBOL)
    return ticker['last']

def get_today():
    return time.strftime('%Y-%m-%d')

def sleep_until_next_candle():
    t = time.time()
    sleep_sec = 300 - (t % 300)
    time.sleep(sleep_sec)
