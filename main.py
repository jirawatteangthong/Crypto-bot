from strategy import check_ema_cross
from telegram_bot import send_telegram
from trade import execute_trade, monitor_position, load_state, save_state, should_skip_trade
from utils import fetch_candles, get_balance
import time
from datetime import datetime, timedelta

MAX_TRADES_PER_DAY = 3
COOLDOWN_HOURS = 24

def main():
    send_telegram("✅ บอทเริ่มทำงานแล้ว (EMA 50/200 - M15)")

    state = load_state()

    last_trade_date = state.get('last_trade_date')
    daily_trades = state.get('daily_trades', 0)
    sl_count = state.get('sl_count', 0)
    active_order = state.get('active_order', None)
    last_check_balance = time.time()

    while True:
        try:
            now = datetime.utcnow()

            if last_trade_date != now.date().isoformat():
                daily_trades = 0
                sl_count = 0
                last_trade_date = now.date().isoformat()
                state.update({
                    'last_trade_date': last_trade_date,
                    'daily_trades': daily_trades,
                    'sl_count': sl_count,
                    'active_order': None
                })
                save_state(state)

            if daily_trades >= MAX_TRADES_PER_DAY or sl_count >= 2:
                time.sleep(60)
                continue

            if active_order:
                still_active = monitor_position(active_order)
                if not still_active:
                    active_order = None
                    state['active_order'] = None
                    save_state(state)
                time.sleep(10)
                continue

            if time.time() - last_check_balance >= 5 * 3600:
                balance = get_balance()
                send_telegram(f"ℹ️ ตอนนี้บอทกำลังทำงานอยู่\nยอดทุน: {balance} USDT")
                last_check_balance = time.time()

            candles = fetch_candles()
            signal = check_ema_cross(candles)

            if signal and not should_skip_trade(state):
                order = execute_trade(signal)
                if order:
                    active_order = order
                    daily_trades += 1
                    state.update({
                        'daily_trades': daily_trades,
                        'active_order': order
                    })
                    save_state(state)

            time.sleep(15)

        except Exception as e:
            send_telegram(f"[ERROR] {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()