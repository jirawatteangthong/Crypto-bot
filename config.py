from utils import exchange, fetch_price, get_balance
from telegram_bot import send_telegram
import json
import os
import time

TRADE_SIZE = 0.5
TP_POINTS = 500
SL_POINTS = 250

STATE_FILE = 'bot_state.json'

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def should_skip_trade(state):
    return state.get('active_order') is not None

def execute_trade(signal):
    direction = signal['direction']
    price = fetch_price()
    side = 'buy' if direction == 'long' else 'sell'

    tp = price + TP_POINTS if direction == 'long' else price - TP_POINTS
    sl = price - SL_POINTS if direction == 'long' else price + SL_POINTS

    params = {
        'tdMode': 'cross',
        'ordType': 'market',
        'tpTriggerPx': tp,
        'tpOrdPx': '-1',
        'slTriggerPx': sl,
        'slOrdPx': '-1'
    }

    try:
        order = exchange.create_order('BTC/USDT:USDT', 'market', side, TRADE_SIZE, None, params)
        send_telegram(f"[ENTRY] {direction.upper()} @ {price}\nSize: {TRADE_SIZE}\nTP: {tp}\nSL: {sl}")
        return {
            'id': order['id'],
            'entry': price,
            'tp': tp,
            'sl': sl,
            'direction': direction,
            'breakeven': False
        }
    except Exception as e:
        send_telegram(f"[ERROR เปิดออเดอร์] {e}")
        return None

def monitor_position(order):
    price = fetch_price()
    direction = order['direction']
    entry = order['entry']
    tp = order['tp']
    sl = order['sl']

    if not order['breakeven']:
        if (direction == 'long' and price >= entry + 250) or (direction == 'short' and price <= entry - 250):
            order['sl'] = entry
            order['breakeven'] = True
            send_telegram("✅ ระบบได้กันทุนแล้ว")

    if direction == 'long':
        if price <= order['sl']:
            send_telegram(f"[STOP LOSS] ปิดที่ {price} ขาดทุน: {round(price - entry, 2)}")
            update_sl_count()
            return False
        elif price >= tp:
            send_telegram(f"[TAKE PROFIT] ปิดที่ {price} กำไร: {round(price - entry, 2)}")
            return False
    else:
        if price >= order['sl']:
            send_telegram(f"[STOP LOSS] ปิดที่ {price} ขาดทุน: {round(entry - price, 2)}")
            update_sl_count()
            return False
        elif price <= tp:
            send_telegram(f"[TAKE PROFIT] ปิดที่ {price} กำไร: {round(entry - price, 2)}")
            return False

    return True

def update_sl_count():
    state = load_state()
    state['sl_count'] = state.get('sl_count', 0) + 1
    save_state(state)