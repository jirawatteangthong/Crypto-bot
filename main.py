import time
import datetime
from okx_trade import OKXTrade
from strategy import calculate_ema_series
from telegram_bot import send_telegram_message

SYMBOL = "BTC-USDT-SWAP"
LEVERAGE = 20
ORDER_SIZE = 0.5
TP_AMOUNT = 500
SL_AMOUNT = 990

MAX_TRADES_PER_DAY = 3
MAX_SL_PER_DAY = 2

TRADE_STATE = {
    "today": None,
    "count": 0,
    "sl_count": 0,
    "has_position": False,
}

okx = OKXTrade()
okx.set_leverage(SYMBOL, LEVERAGE)

last_ping = time.time()  # เพิ่มตัวแปรเก็บเวลาสุดท้ายที่เช็คบอท

def is_new_day():
    today = datetime.date.today()
    if TRADE_STATE["today"] != today:
        TRADE_STATE["today"] = today
        TRADE_STATE["count"] = 0
        TRADE_STATE["sl_count"] = 0
        TRADE_STATE["has_position"] = False
        return True
    return False

def detect_cross(closes):
    ema50 = calculate_ema_series(closes, 50)
    ema200 = calculate_ema_series(closes, 200)

    if len(ema50) < 2 or len(ema200) < 2 or ema50[-2] is None or ema200[-2] is None:
        return None

    ema50_prev = ema50[-2]
    ema50_now = ema50[-1]
    ema200_prev = ema200[-2]
    ema200_now = ema200[-1]

    if ema50_prev < ema200_prev and ema50_now > ema200_now:
        return "golden"
    elif ema50_prev > ema200_prev and ema50_now < ema200_now:
        return "death"
    return None

def trade_signal():
    candles = okx.get_candles(SYMBOL, "15m", 200)
    closes = [float(c[4]) for c in candles]
    return detect_cross(closes)

def open_order(direction):
    price = okx.get_last_price(SYMBOL)
    tp = price + TP_AMOUNT if direction == "long" else price - TP_AMOUNT
    sl = price - SL_AMOUNT if direction == "long" else price + SL_AMOUNT
    side = "buy" if direction == "long" else "sell"

    order = okx.place_order(SYMBOL, ORDER_SIZE, side, tp=tp, sl=sl)
    if order:
        TRADE_STATE["has_position"] = True
        TRADE_STATE["count"] += 1
        send_telegram_message(f"เปิดออเดอร์ {direction.upper()} ที่ราคา {price}\nTP: {tp}, SL: {sl}")
    else:
        send_telegram_message("เปิดออเดอร์ไม่สำเร็จ")

def monitor_position():
    position = okx.get_position(SYMBOL)
    if not position:
        return

    entry = float(position["avgPx"])
    current = okx.get_last_price(SYMBOL)
    direction = "long" if float(position["posSide"]) == "long" else "short"

    pnl = (current - entry) * LEVERAGE if direction == "long" else (entry - current) * LEVERAGE

    # กันทุน
    if not position.get("breakeven_moved", False):
        if abs(current - entry) >= 250:
            okx.move_sl_to_entry(SYMBOL, entry)
            send_telegram_message("เลื่อน SL ไปกันทุนแล้ว")
            position["breakeven_moved"] = True

    # ปิดออเดอร์
    if float(position["unrealizedPnl"]) >= TP_AMOUNT:
        okx.close_position(SYMBOL)
        send_telegram_message(f"TP ที่ราคา {current}")
        TRADE_STATE["has_position"] = False

    elif float(position["unrealizedPnl"]) <= -SL_AMOUNT:
        okx.close_position(SYMBOL)
        send_telegram_message(f"SL ที่ราคา {current}")
        TRADE_STATE["has_position"] = False
        TRADE_STATE["sl_count"] += 1

def main_loop():
    global last_ping
    send_telegram_message("บอทเริ่มทำงานแล้ว")

    while True:
        try:
            is_new_day()

            # ส่งสถานะบอททุก 5 ชั่วโมง
            now = time.time()
            if now - last_ping >= 5 * 60 * 60:
                send_telegram_message(
                    f"บอทยังทำงานอยู่ - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"จำนวนออเดอร์วันนี้: {TRADE_STATE['count']} / SL: {TRADE_STATE['sl_count']}"
                )
                last_ping = now

            if TRADE_STATE["count"] >= MAX_TRADES_PER_DAY or TRADE_STATE["sl_count"] >= MAX_SL_PER_DAY:
                time.sleep(60)
                continue

            if not TRADE_STATE["has_position"]:
                signal = trade_signal()
                if signal == "golden":
                    open_order("long")
                elif signal == "death":
                    open_order("short")

            else:
                monitor_position()

            time.sleep(30)

        except Exception as e:
            send_telegram_message(f"เกิดข้อผิดพลาด: {str(e)}")
            time.sleep(60)

if __name__ == "__main__":
    main_loop()
