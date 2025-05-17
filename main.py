import time
from datetime import datetime, timedelta
from config import symbol, position_size, cooldown_after_sl_minutes, check_balance_every_hours
from utils import exchange, fetch_price, telegram, fetch_balance, format_time
from strategy import detect_ema_crossover

active_order = None
order_count = 0
sl_hit_count = 0
last_sl_time = None
last_balance_check = datetime.utcnow()

def open_order(direction):
    side = 'buy' if direction == 'long' else 'sell'
    price = fetch_price()
    sl = price - 250 if direction == 'long' else price + 250
    tp = price + 500 if direction == 'long' else price - 500

    params = {
        'tdMode': 'cross',
        'ordType': 'market',
        'stopLoss': {'triggerPrice': round(sl, 2)},
        'takeProfit': {'triggerPrice': round(tp, 2)},
    }

    try:
        order = exchange.create_order(symbol, 'market', side, position_size, None, params)
        telegram(f"เปิด {direction.upper()} ที่ {price}\nTP: {tp}, SL: {sl}")
        return price, tp, sl, direction, order['id']
    except Exception as e:
        telegram(f"[ERROR เปิดออเดอร์] {e}")
        return None

def monitor_trade(entry, tp, sl, direction, order_id):
    global last_sl_time, sl_hit_count, active_order
    moved_to_breakeven = False

    while True:
        price = fetch_price()
        if direction == 'long':
            if not moved_to_breakeven and price >= entry + 250:
                sl = entry
                moved_to_breakeven = True
                telegram("ระบบได้กันทุนแล้ว")
            if price <= sl:
                telegram(f"SL ทำงาน (Long) ที่ {price} - ขาดทุน")
                last_sl_time = datetime.utcnow()
                sl_hit_count += 1
                break
            elif price >= tp:
                telegram(f"TP ถึงเป้า (Long) ที่ {price} - กำไร")
                break
        else:
            if not moved_to_breakeven and price <= entry - 250:
                sl = entry
                moved_to_breakeven = True
                telegram("ระบบได้กันทุนแล้ว")
            if price >= sl:
                telegram(f"SL ทำงาน (Short) ที่ {price} - ขาดทุน")
                last_sl_time = datetime.utcnow()
                sl_hit_count += 1
                break
            elif price <= tp:
                telegram(f"TP ถึงเป้า (Short) ที่ {price} - กำไร")
                break

        time.sleep(10)

    active_order = None

def main():
    global order_count, active_order, sl_hit_count, last_balance_check
    telegram("บอททำงานแล้ว")

    while True:
        now = datetime.utcnow()

        if (now - last_balance_check).total_seconds() >= check_balance_every_hours * 3600:
            balance = fetch_balance()
            telegram(f"เช็คยอด: บอทกำลังทำงาน คุณมี {balance} USDT")
            last_balance_check = now

        if last_sl_time and (now - last_sl_time).total_seconds() < cooldown_after_sl_minutes * 60:
            time.sleep(30)
            continue

        if active_order is None and order_count < 3 and sl_hit_count < 2:
            signal = detect_ema_crossover()
            if signal == 'golden':
                result = open_order('long')
            elif signal == 'death':
                result = open_order('short')
            else:
                result = None

            if result:
                active_order = result
                entry, tp, sl, direction, order_id = result
                order_count += 1
                monitor_trade(entry, tp, sl, direction, order_id)

        elif order_count >= 3 or sl_hit_count >= 2:
            telegram("หยุดเทรดวันนี้ (ครบ 3 ไม้ หรือ SL 2 ไม้)")
            while datetime.utcnow().date() == now.date():
                time.sleep(300)

            # Reset ตอนข้ามวัน
            order_count = 0
            sl_hit_count = 0
            last_sl_time = None
            telegram("เริ่มวันใหม่แล้ว")

        time.sleep(10)

if __name__ == "__main__":
    main()
