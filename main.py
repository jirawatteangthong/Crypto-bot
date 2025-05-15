import time
from config import MAX_TRADES_PER_DAY
from telegram import send_message
from strategy import detect_bos_and_fibo
from entry import check_fibo_entry
from order import open_trade, close_trade, get_open_trade
from utils import get_today, sleep_until_next_candle

def main():
    send_message("[BOT STARTED] เริ่มทำงานแล้ว")
    trades_today = 0
    trade_date = get_today()

    while True:
        sleep_until_next_candle()

        if get_today() != trade_date:
            trades_today = 0
            trade_date = get_today()

        if trades_today >= MAX_TRADES_PER_DAY:
            continue

        if get_open_trade():
            continue

        fibo = detect_bos_and_fibo()
        if not fibo:
            continue

        signal = check_fibo_entry(fibo)
        if signal:
            open_trade(signal)
            trades_today += 1

        close_trade()

if __name__ == "__main__":
    main()
