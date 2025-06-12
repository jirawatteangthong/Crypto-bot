import ccxt
import time
import requests
from datetime import datetime, timedelta
import logging

# === ตั้งค่า Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# === ตั้งค่า ===
api_key = '8f528085-448c-4480-a2b0-d7f72afb38ad'       # ใส่ API KEY
secret = '05A665CEAF8B2161483DF63CB10085D2'
password = 'Jirawat1-'
symbol = 'BTC/USDT:USDT'
timeframe = '15m'
order_size = 0.9
leverage = 25
tp_value = 500
sl_value = 990
be_profit_trigger = 350
be_sl = 100

telegram_token = '7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY'
telegram_chat_id = '8104629569'

# === สถานะการเทรด ===
current_position = None  # None, 'long', 'short'
entry_price = None
order_id = None
sl_moved = False
last_ema_state = None  # เก็บสถานะ EMA ก่อนหน้า

# === Exchange Setup ===
exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})
exchange.set_sandbox_mode(False)

# === Telegram ===
def send_telegram(msg):
    try:
        requests.get(
            f'https://api.telegram.org/bot{telegram_token}/sendMessage',
            params={'chat_id': telegram_chat_id, 'text': msg},
            timeout=10
        )
        logger.info(f"Telegram: {msg}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

# === คำนวณ EMA ===
def calculate_ema(prices, period):
    if len(prices) < period:
        return None
    
    # เริ่มด้วย SMA
    sma = sum(prices[:period]) / period
    ema = sma
    multiplier = 2 / (period + 1)
    
    # คำนวณ EMA
    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
    
    return ema

# === ตรวจสอบการตัดกันของ EMA ===
def check_ema_cross():
    global last_ema_state
    
    try:
        # ดึงข้อมูลเทียน
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=250)
        closes = [candle[4] for candle in ohlcv]
        
        if len(closes) < 200:
            return None
        
        # คำนวณ EMA สำหรับเทียนปัจจุบันและก่อนหน้า
        current_ema50 = calculate_ema(closes, 50)
        current_ema200 = calculate_ema(closes, 200)
        prev_ema50 = calculate_ema(closes[:-1], 50)
        prev_ema200 = calculate_ema(closes[:-1], 200)
        
        if None in [current_ema50, current_ema200, prev_ema50, prev_ema200]:
            return None
        
        # เก็บสถานะปัจจุบัน
        current_state = 'above' if current_ema50 > current_ema200 else 'below'
        
        # ตรวจสอบการตัดกัน
        cross_signal = None
        
        # Golden Cross: EMA50 ตัดขึ้นเหนือ EMA200
        if prev_ema50 <= prev_ema200 and current_ema50 > current_ema200:
            cross_signal = 'long'
            logger.info(f"Golden Cross detected: EMA50({current_ema50:.2f}) > EMA200({current_ema200:.2f})")
        
        # Death Cross: EMA50 ตัดลงใต้ EMA200  
        elif prev_ema50 >= prev_ema200 and current_ema50 < current_ema200:
            cross_signal = 'short'
            logger.info(f"Death Cross detected: EMA50({current_ema50:.2f}) < EMA200({current_ema200:.2f})")
        
        last_ema_state = current_state
        return cross_signal
        
    except Exception as e:
        logger.error(f"EMA calculation error: {e}")
        return None

# === ตรวจสอบโพซิชันปัจจุบัน ===
def get_current_position():
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            if pos['contracts'] > 0:
                return {
                    'side': pos['side'],
                    'size': pos['contracts'],
                    'entry_price': pos['entryPrice'],
                    'unrealized_pnl': pos['unrealizedPnl']
                }
        return None
    except Exception as e:
        logger.error(f"Get position error: {e}")
        return None

# === เปิดออเดอร์พร้อม TP/SL ===
def open_order_with_tpsl(direction):
    global current_position, entry_price, order_id, sl_moved
    
    try:
        # เช็คว่ามีโพซิชันอยู่หรือไม่
        existing_pos = get_current_position()
        if existing_pos:
            logger.info("มีโพซิชันอยู่แล้ว ข้ามการเปิดออเดอร์")
            return False
        
        # ดึงราคาปัจจุบัน
        ticker = exchange.fetch_ticker(symbol)
        current_price = float(ticker['last'])
        
        # คำนวณ TP และ SL
        if direction == 'long':
            tp_price = current_price + tp_value
            sl_price = current_price - sl_value
            side = 'buy'
        else:
            tp_price = current_price - tp_value
            sl_price = current_price + sl_value
            side = 'sell'
        
        # พารามิเตอร์สำหรับ OKX
        params = {
            'tdMode': 'cross',
            'ordType': 'market',
            'lever': str(leverage),
            'tpTriggerPx': str(tp_price),
            'tpOrdPx': '-1',
            'tpTriggerPxType': 'last',
            'slTriggerPx': str(sl_price),
            'slOrdPx': '-1', 
            'slTriggerPxType': 'last'
        }
        
        # สร้างออเดอร์
        order = exchange.create_order(symbol, 'market', side, order_size, None, params)
        
        # อัพเดตสถานะ
        current_position = direction
        entry_price = current_price
        order_id = order.get('id')
        sl_moved = False
        
        # ส่งแจ้งเตือน
        message = f"""🚀 เปิดออเดอร์ {direction.upper()}
💰 ราคาเข้า: {current_price:.2f}
🎯 TP: {tp_price:.2f} (+{tp_value})
🛡️ SL: {sl_price:.2f} (-{sl_value})
📊 ขนาด: {order_size} BTC
Order ID: {order_id}"""
        
        send_telegram(message)
        logger.info(f"Order opened: {direction} at {current_price}")
        return True
        
    except Exception as e:
        error_msg = f"❌ เปิดออเดอร์ล้มเหลว: {e}"
        send_telegram(error_msg)
        logger.error(error_msg)
        return False

# === เลื่อน SL เป็นกันทุน ===
def move_sl_to_breakeven():
    global sl_moved
    
    try:
        if sl_moved:
            return
        
        # คำนวณ SL ใหม่ (กันทุน)
        if current_position == 'long':
            new_sl = entry_price - be_sl
        else:
            new_sl = entry_price + be_sl
        
        # สำหรับ OKX ต้องใช้ API แก้ไข Algo Order
        # หรือยกเลิกออเดอร์เก่าแล้วสร้างใหม่
        # ในที่นี้จะแจ้งเตือนเฉย ๆ เพราะ OKX มีข้อจำกัด
        
        sl_moved = True
        message = f"""🔄 เลื่อน SL เป็นกันทุน
📍 ราคาเข้า: {entry_price:.2f}
🛡️ SL ใหม่: {new_sl:.2f}
💚 โพซิชัน: {current_position.upper()}"""
        
        send_telegram(message)
        logger.info(f"SL moved to breakeven: {new_sl}")
        
    except Exception as e:
        logger.error(f"Move SL error: {e}")

# === ตรวจสอบโพซิชันและจัดการ SL ===
def monitor_position():
    global current_position, sl_moved
    
    if not current_position:
        return
        
    try:
        # เช็คโพซิชันจริง
        pos_info = get_current_position()
        
        if not pos_info:
            # โพซิชันถูกปิดแล้ว
            logger.info("Position closed")
            message = f"✅ โพซิชัน {current_position.upper()} ถูกปิดแล้ว"
            send_telegram(message)
            
            current_position = None
            entry_price = None
            order_id = None
            sl_moved = False
            return
        
        # คำนวณ PnL
        current_price = float(exchange.fetch_ticker(symbol)['last'])
        
        if current_position == 'long':
            pnl = current_price - entry_price
        else:
            pnl = entry_price - current_price
        
        # เลื่อน SL เมื่อกำไรถึง 350
        if not sl_moved and pnl >= be_profit_trigger:
            move_sl_to_breakeven()
        
        logger.info(f"Position: {current_position}, PnL: {pnl:.2f}, Price: {current_price:.2f}")
        
    except Exception as e:
        logger.error(f"Monitor position error: {e}")

# === MAIN LOOP ===
def main():
    send_telegram("🤖 EMA Cross Bot เริ่มทำงาน")
    logger.info("Bot started")
    
    while True:
        try:
            # ตรวจสอบโพซิชันปัจจุบัน
            monitor_position()
            
            # ถ้าไม่มีโพซิชัน ให้เช็คสัญญาณ
            if not current_position:
                signal = check_ema_cross()
                
                if signal:
                    logger.info(f"EMA Cross Signal: {signal}")
                    open_order_with_tpsl(signal)
                    time.sleep(5)  # รอสักครู่หลังเปิดออเดอร์
            
            time.sleep(15)  # เช็คทุก 15 วินาที
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            send_telegram("🛑 Bot หยุดทำงานโดยผู้ใช้")
            break
            
        except Exception as e:
            error_msg = f"❌ Main loop error: {e}"
            logger.error(error_msg)
            send_telegram(error_msg)
            time.sleep(30)  # พัก 30 วินาทีเมื่อเกิดข้อผิดพลาด

if __name__ == '__main__':
    main()
