import ccxt
import requests

api_key = '8f528085-448c-4480-a2b0-d7f72afb38ad'
secret = '05A665CEAF8B2161483DF63CB10085D2'
password = 'Jirawat1-'

exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

def fetch_candles():
    return exchange.fetch_ohlcv('BTC/USDT:USDT', timeframe='15m', limit=210)

def fetch_price():
    return float(exchange.fetch_ticker('BTC/USDT:USDT')['last'])

def get_balance():
    balance = exchange.fetch_balance()
    return balance['total']['USDT']
