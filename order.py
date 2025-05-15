from utils import connect_okx
from config import SYMBOL, TRADE_SIZE
from telegram import notify
trade = None

def open_trade(signal):
    global trade
    exchange = connect_okx()
    side = 'buy' if signal['direction'] == 'long' else 'sell'
    pos_side = 'long' if side == 'buy' else 'short'

    params = {'posSide': pos_side}
    exchange.create_market_order(SYMBOL, side, TRADE_SIZE, params)

    trade = {
        'direction': signal['direction'],
        'entry': signal['entry'],
        'tp': signal['tp'],
        'sl': signal['sl']
    }

    notify(f"[ENTRY] {side.upper()} @ {signal['entry']}\nTP: {signal['tp']}\nSL: {signal['sl']}")

def close_trade():
    global trade
    if not trade:
        return

    price = fetch_current_price()
    if trade['direction'] == 'long':
        if price >= trade['tp'] or price <= trade['sl']:
            result = "TP" if price >= trade['tp'] else "SL"
    else:
        if price <= trade['tp'] or price >= trade['sl']:
            result = "TP" if price <= trade['tp'] else "SL"
    else:
        return

    notify(f"[CLOSE] {result} @ {price}")
    trade = None

def get_open_trade():
    return trade
