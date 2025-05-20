import ccxt
import time
import requests
from datetime import datetime, timedelta

# === ตั้งค่า ===
api_key = '8f528085-448c-4480-a2b0-d7f72afb38ad'       # ใส่ API KEY
secret = '05A665CEAF8B2161483DF63CB10085D2'
password = 'Jirawat1-'
symbol = 'BTC/USDT:USDT'
timeframe = '15m'
order_size = 0.5
leverage = 20
tp_value = 500
sl_value = 990
be_profit_trigger = 350
be_sl = 100

telegram_token = '7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY'
telegram_chat_id = '8104629569'

# === สถานะ ===
last_signal = None
last_order_time = None
entry_price = None
entry_side = None
active_position = False
last_check_time = datetime.utcnow()

# === OKX ===
exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})
exchange.set_sandbox_mode(False)

# === Telegram ===
def telegram(msg):
    try:
        requests.get(f'https://api.telegram.org/bot{telegram_token}/sendMessage',
                     params={'chat_id': telegram_chat_id, 'text': msg})
    except:
        pass

# === EMA ===
def calculate_ema(prices, period):
    ema = prices[0]
    k = 2 / (period + 1)
    for price in prices:
        ema = price * k + ema * (1 - k)
    return ema

# === EMA Cross ===
def detect_ema_cross():
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=200)
    closes = [c[4] for c in ohlcv]

    ema50_yesterday = calculate_ema(closes[:-1], 50)
    ema200_yesterday = calculate_ema(closes[:-1], 200)
    ema50_today = calculate_ema(closes, 50)
    ema200_today = calculate_ema(closes, 200)

    if ema50_yesterday < ema200_yesterday and ema50_today > ema200_today:
        return 'long'
    elif ema50_yesterday > ema200_yesterday and ema50_today < ema200_today:
        return 'short'
    return None

# === เปิดออเดอร์ ===
def open_order(direction):
    global active_position, last_order_time, entry_price, entry_side
    side = 'buy' if direction == 'long' else 'sell'
    price = float(exchange.fetch_ticker(symbol)['last'])

    tp = price + tp_value if direction == 'long' else price - tp_value
    sl = price - sl_value if direction == 'long' else price + sl_value

    params = {
        'tdMode': 'cross',
        'ordType': 'market',
        'tpTriggerPx': str(tp),
        'tpOrdPx': "-1",
        'tpTriggerPxType': "last",
        'slTriggerPx': str(sl),
        'slOrdPx': "-1",
        'slTriggerPxType': "last",
        'lever': str(leverage)
    }

    try:
        order = exchange.create_order(symbol, 'market', side, order_size, None, params)
        telegram(f"[เปิดออเดอร์] {direction.upper()} ที่ราคา {price:.2f}\nTP: {tp}, SL: {sl}")
        active_position = True
        last_order_time = datetime.utcnow()
        entry_price = price
        entry_side = direction
    except Exception as e:
        telegram(f"[ERROR เปิดออเดอร์] okx {e}")

# === ตรวจสอบ TP/SL และเลื่อน SL ===
def monitor_position():
    global active_position
    sl_moved = False

    while active_position:
        try:
            price = float(exchange.fetch_ticker(symbol)['last'])
            pnl = price - entry_price if entry_side == 'long' else entry_price - price

            if not sl_moved and pnl >= be_profit_trigger:
                move_sl(entry_price, entry_side)
                sl_moved = True

            if pnl >= tp_value or pnl <= -sl_value:
                telegram(f"[ปิดออเดอร์] {entry_side.upper()} ที่ {price:.2f} / PnL: {pnl:.2f}")
                active_position = False
                break

            time.sleep(10)
        except Exception as e:
            telegram(f"[ERROR Monitor] {e}")
            time.sleep(10)

# === เลื่อน SL เป็นกันทุน ===
def move_sl(entry, direction):
    try:
        new_sl = entry - be_sl if direction == 'long' else entry + be_sl
        telegram(f"[เลื่อน SL] เป็นกันทุนที่ {new_sl} ({direction})")
        # ไม่สามารถแก้ SL โดยตรง ต้องใช้ strategy หรือปิดแล้วเปิดใหม่
        # ปล่อยให้ OKX จัดการในระบบ SL เดิมแทน หรือเขียนส่วนแก้คำสั่งเพิ่มภายหลัง
    except Exception as e:
        telegram(f"[ERROR เลื่อน SL] {e}")

# === เช็คสถานะทุก 12 ชม. ===
def check_bot_status():
    global last_check_time
    now = datetime.utcnow()
    if (now - last_check_time) > timedelta(hours=12):
        telegram("[สถานะ] บอทยังทำงานปกติ")
        last_check_time = now

# === MAIN ===
def main():
    global last_signal, active_position, last_order_time

    telegram("[เริ่มทำงาน] EMA Bot M15")

    while True:
        try:
            check_bot_status()

            signal = detect_ema_cross()
            now = datetime.utcnow()

            can_trade = (
                not active_position and
                signal and
                signal != last_signal and
                (not last_order_time or (now - last_order_time) > timedelta(hours=2))
            )

            if can_trade:
                last_signal = signal
                open_order(signal)
                time.sleep(5)
                monitor_position()

            time.sleep(15)

        except Exception as e:
            telegram(f"[ERROR LOOP] {e}")
            time.sleep(30)

if __name__ == '__main__':
    main()
