import ccxt
import time
import requests
from datetime import datetime, timedelta

# === ตั้งค่า ===
api_key = '8f528085-448c-4480-a2b0-d7f72afb38ad'
secret = '05A665CEAF8B2161483DF63CB10085D2'
password = 'Jirawat1-'

symbol = 'BTC/USDT:USDT'
timeframe = '15m'
order_size = 0.5
leverage = 20
tp_value = 500
sl_value = 990

telegram_token = '7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY'
telegram_chat_id = '8104629569'

max_trades_per_day = 3
max_sl_per_day = 2

# === ตัวแปรสถานะ ===
trade_count = 0
sl_count = 0
active_position = False
last_trade_day = None
last_check_time = datetime.utcnow()

# === ตั้งค่า OKX ===
exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})
exchange.set_sandbox_mode(False)

# === Telegram แจ้งเตือน ===
def telegram(msg):
    try:
        requests.get(
            f'https://api.telegram.org/bot{telegram_token}/sendMessage',
            params={'chat_id': telegram_chat_id, 'text': msg}
        )
    except Exception as e:
        print(f"ส่ง Telegram ไม่สำเร็จ: {e}")

# === ดึงข้อมูล ===
def get_ohlcv():
    return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)

def calculate_ema_series(prices, period):
    multiplier = 2 / (period + 1)
    ema = prices[0]
    emas = []
    for price in prices:
        ema = (price - ema) * multiplier + ema
        emas.append(ema)
    return emas

# === เช็คจุดตัด EMA ===
def detect_cross():
    candles = get_ohlcv()
    closes = [c[4] for c in candles]

    ema50 = calculate_ema_series(closes, 50)
    ema200 = calculate_ema_series(closes, 200)

    ema50_prev = ema50[-2]
    ema50_now = ema50[-1]
    ema200_prev = ema200[-2]
    ema200_now = ema200[-1]

    if ema50_prev < ema200_prev and ema50_now > ema200_now:
        return 'long'   # Golden Cross
    elif ema50_prev > ema200_prev and ema50_now < ema200_now:
        return 'short'  # Death Cross
    return None

# === เปิดออเดอร์พร้อม TP/SL ===
def open_position(direction):
    global active_position, trade_count

    price = float(exchange.fetch_ticker(symbol)['last'])
    side = 'buy' if direction == 'long' else 'sell'

    tp = price + tp_value if direction == 'long' else price - tp_value
    sl = price - sl_value if direction == 'long' else price + sl_value

    try:
        # ส่งคำสั่งหลัก
        exchange.create_order(symbol, 'market', side, order_size, None, {
            'tdMode': 'cross',
            'lever': str(leverage),
        })

        # ตั้ง TP
        exchange.create_order(symbol, 'limit', 'sell' if direction == 'long' else 'buy', order_size, None, {
            'tdMode': 'cross',
            'tpTriggerPx': str(tp),
            'tpOrdPx': str(tp),
            'ordType': 'limit'
        })

        # ตั้ง SL
        exchange.create_order(symbol, 'limit', 'sell' if direction == 'long' else 'buy', order_size, None, {
            'tdMode': 'cross',
            'slTriggerPx': str(sl),
            'slOrdPx': str(sl),
            'ordType': 'limit'
        })

        telegram(f"เปิดออเดอร์ {direction.upper()} ที่ราคา {price}\nTP: {tp}\nSL: {sl}")
        trade_count += 1
        active_position = True
        return price, direction

    except Exception as e:
        telegram(f"[ERROR เปิดออเดอร์] {e}")
        return None, None

# === เลื่อน SL ไปกันทุน ===
def move_sl_to_breakeven(entry_price, direction):
    try:
        sl = entry_price
        exchange.create_order(symbol, 'limit', 'sell' if direction == 'long' else 'buy', order_size, None, {
            'tdMode': 'cross',
            'slTriggerPx': str(sl),
            'slOrdPx': str(sl),
            'ordType': 'limit'
        })
        telegram(f"เลื่อน SL ไปที่กันทุน: {sl} ({direction})")
    except Exception as e:
        telegram(f"[ERROR เลื่อน SL] {e}")

# === ตรวจสถานะ TP/SL ===
def monitor(entry_price, direction):
    global active_position, sl_count

    sl_moved = False

    while True:
        price = float(exchange.fetch_ticker(symbol)['last'])
        profit = price - entry_price if direction == 'long' else entry_price - price

        if not sl_moved and profit >= 990:
            move_sl_to_breakeven(entry_price, direction)
            sl_moved = True

        if profit >= tp_value or profit <= -sl_value:
            telegram(f"ปิดออเดอร์ {direction.upper()} ที่ราคา {price} / กำไร: {profit:.2f}")
            if profit < 0:
                sl_count += 1
            active_position = False
            break

        time.sleep(10)

# === เช็คสถานะบอททุก 12 ชั่วโมง ===
def check_status():
    global last_check_time
    now = datetime.utcnow()
    if (now - last_check_time) >= timedelta(hours=12):
        telegram(f"บอทยังทำงานอยู่ - {now.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                 f"จำนวนไม้วันนี้: {trade_count} / SL: {sl_count}")
        last_check_time = now

# === MAIN LOOP ===
def main():
    global trade_count, sl_count, active_position, last_trade_day

    telegram("เริ่มทำงาน: EMA Bot (M15)")

    while True:
        try:
            now = datetime.utcnow()
            today = now.date()

            if last_trade_day != today:
                trade_count = 0
                sl_count = 0
                last_trade_day = today

            if trade_count >= max_trades_per_day or sl_count >= max_sl_per_day or active_position:
                check_status()
                time.sleep(60)
                continue

            signal = detect_cross()
            if signal:
                entry_price, direction = open_position(signal)
                if entry_price:
                    monitor(entry_price, direction)

            check_status()
            time.sleep(30)

        except Exception as e:
            telegram(f"[ERROR LOOP] {e}")
            time.sleep(60)

if __name__ == '__main__':
    main()
