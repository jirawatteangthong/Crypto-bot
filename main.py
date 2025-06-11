import ccxt
import time
import requests
from datetime import datetime, timedelta
import logging
import threading
import json
import os

# === ตั้งค่า Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === ตั้งค่า ===
api_key = '8f528085-448c-4480-a2b0-d7f72afb38ad'
secret = '05A665CEAF8B2161483DF63CB10085D2'
password = 'Jirawat1-'
symbol = 'BTC/USDT:USDT'
timeframe = '15m'
leverage = 20
tp_value = 500
sl_value = 990
be_profit_trigger = 350
be_sl = 100

telegram_token = '7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY'
telegram_chat_id = '8104629569'

# === ไฟล์เก็บข้อมุล ===
STATS_FILE = 'trading_stats.json'

# === สถานะการเทรด ===
current_position = None  # None, 'long', 'short'
entry_price = None
order_id = None
sl_moved = False
last_ema_cross_time = None  # เพิ่มตัวแปรนี้เพื่อป้องกันการเปิดออเดอร์ซ้ำ
portfolio_balance = 0
last_daily_report = None
initial_balance = 0

# === สถิติการเทรด ===
daily_stats = {
    'date': None,
    'tp_count': 0,
    'sl_count': 0,
    'total_pnl': 0,
    'trades': []
}

# === Exchange Setup ===
exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})
exchange.set_sandbox_mode(False)

# === บันทึกสถิติ ===
def load_daily_stats():
    global daily_stats
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, 'r') as f:
                daily_stats = json.load(f)
    except Exception as e:
        logger.error(f"Load stats error: {e}")
        daily_stats = {
            'date': None,
            'tp_count': 0,
            'sl_count': 0,
            'total_pnl': 0,
            'trades': []
        }

def save_daily_stats():
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump(daily_stats, f)
    except Exception as e:
        logger.error(f"Save stats error: {e}")

def reset_daily_stats():
    global daily_stats
    daily_stats = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'tp_count': 0,
        'sl_count': 0,
        'total_pnl': 0,
        'trades': []
    }
    save_daily_stats()

def add_trade_result(close_type, pnl_usdt):
    global daily_stats
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    if daily_stats['date'] != today:
        reset_daily_stats()
    
    if close_type == 'TP':
        daily_stats['tp_count'] += 1
    elif close_type == 'SL':
        daily_stats['sl_count'] += 1
    
    daily_stats['total_pnl'] += pnl_usdt
    daily_stats['trades'].append({
        'time': datetime.now().strftime('%H:%M:%S'),
        'type': close_type,
        'pnl': pnl_usdt
    })
    
    save_daily_stats()

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

# === ดึงยอดคงเหลือ ===
def get_portfolio_balance():
    global portfolio_balance
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free'] + balance['USDT']['used']
        portfolio_balance = usdt_balance
        return usdt_balance
    except Exception as e:
        logger.error(f"Get balance error: {e}")
        send_telegram(f"⛔️ Error: ไม่สามารถดึงยอดคงเหลือได้\nรายละเอียด: {e}")
        return 0

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

# === ตรวจสอบการตัดกันของ EMA (แก้ไขให้แม่นยำยิ่งขึ้น) ===
def check_ema_cross():
    global last_ema_cross_time
    
    try:
        # ดึงข้อมูลเทียน
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=250)
        if len(ohlcv) < 200:
            logger.warning("ไม่มีข้อมูลเทียนเพียงพอสำหรับการคำนวณ EMA")
            return None
            
        closes = [candle[4] for candle in ohlcv]
        timestamps = [candle[0] for candle in ohlcv]
        
        # คำนวณ EMA สำหรับ 3 เทียนล่าสุด (เพิ่มความแม่นยำ)
        current_ema50 = calculate_ema(closes, 50)
        current_ema200 = calculate_ema(closes, 200)
        prev_ema50 = calculate_ema(closes[:-1], 50)
        prev_ema200 = calculate_ema(closes[:-1], 200)
        prev2_ema50 = calculate_ema(closes[:-2], 50)
        prev2_ema200 = calculate_ema(closes[:-2], 200)
        
        if None in [current_ema50, current_ema200, prev_ema50, prev_ema200, prev2_ema50, prev2_ema200]:
            logger.warning("ไม่สามารถคำนวณ EMA ได้")
            return None
        
        # ตรวจสอบการตัดกัน
        cross_signal = None
        current_candle_time = timestamps[-1]
        
        # Golden Cross: EMA50 ตัดขึ้นเหนือ EMA200
        if (prev2_ema50 <= prev2_ema200 and 
            prev_ema50 <= prev_ema200 and 
            current_ema50 > current_ema200):
            
            # ตรวจสอบว่าไม่ได้เปิดออเดอร์ในเทียนนี้แล้ว
            if last_ema_cross_time != current_candle_time:
                cross_signal = 'long'
                last_ema_cross_time = current_candle_time
                logger.info(f"🔥 Golden Cross detected! EMA50({current_ema50:.1f}) > EMA200({current_ema200:.1f})")
                send_telegram(f"🔥 Golden Cross detected!\nEMA50: {current_ema50:.1f}\nEMA200: {current_ema200:.1f}\nPrice: {closes[-1]:.1f}")
        
        # Death Cross: EMA50 ตัดลงใต้ EMA200
        elif (prev2_ema50 >= prev2_ema200 and 
              prev_ema50 >= prev_ema200 and 
              current_ema50 < current_ema200):
            
            # ตรวจสอบว่าไม่ได้เปิดออเดอร์ในเทียนนี้แล้ว
            if last_ema_cross_time != current_candle_time:
                cross_signal = 'short'
                last_ema_cross_time = current_candle_time
                logger.info(f"🔥 Death Cross detected! EMA50({current_ema50:.1f}) < EMA200({current_ema200:.1f})")
                send_telegram(f"🔥 Death Cross detected!\nEMA50: {current_ema50:.1f}\nEMA200: {current_ema200:.1f}\nPrice: {closes[-1]:.1f}")
        
        # แจ้งข้อมูล EMA ปัจจุบัน (ทุก 5 นาที)
        if int(time.time()) % 300 == 0:  # ทุก 5 นาที
            logger.info(f"EMA Status - 50: {current_ema50:.1f}, 200: {current_ema200:.1f}, Price: {closes[-1]:.1f}")
        
        return cross_signal
        
    except Exception as e:
        logger.error(f"EMA calculation error: {e}")
        send_telegram(f"⛔️ Error: ไม่สามารถคำนวณ EMA ได้\nรายละเอียด: {e}")
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
        send_telegram(f"⛔️ Error: ไม่สามารถดึงข้อมูลโพซิชันได้\nรายละเอียด: {e}")
        return None

# === เปิดออเดอร์พร้อม TP/SL (แก้ไขให้รองรับข้อผิดพลาด) ===
def open_order_with_tpsl(direction):
    global current_position, entry_price, order_id, sl_moved
    
    try:
        # เช็คว่ามีโพซิชันอยู่หรือไม่
        existing_pos = get_current_position()
        if existing_pos:
            logger.info("มีโพซิชันอยู่แล้ว ข้ามการเปิดออเดอร์")
            return False
        
        # ดึงยอดคงเหลือ
        balance = get_portfolio_balance()
        if balance <= 100:  # ต้องมีเงินอย่างน้อย 100 USDT
            send_telegram("⛔️ Error: ยอดคงเหลือไม่เพียงพอ (ต้องมีอย่างน้อย 100 USDT)")
            return False
        
        # คำนวณขนาดออเดอร์ (50% ของพอร์ต)
        use_balance = balance * 0.5
        
        # ดึงราคาปัจจุบัน
        ticker = exchange.fetch_ticker(symbol)
        current_price = float(ticker['last'])
        
        # คำนวณขนาดออเดอร์
        order_size = (use_balance * leverage) / current_price
        
        # ปรับขนาดออเดอร์ให้เป็นไปตามข้อกำหนดของ OKX
        order_size = round(order_size, 6)
        
        if order_size < 0.001:  # ขนาดออเดอร์ขั้นต่ำของ BTC
            send_telegram("⛔️ Error: ขนาดออเดอร์เล็กเกินไป")
            return False
        
        # คำนวณ TP และ SL
        if direction == 'long':
            tp_price = round(current_price + tp_value, 1)
            sl_price = round(current_price - sl_value, 1)
            side = 'buy'
            emoji = '📈'
        else:
            tp_price = round(current_price - tp_value, 1)
            sl_price = round(current_price + sl_value, 1)
            side = 'sell'
            emoji = '📉'
        
        # แจ้งว่ากำลังเปิดออเดอร์
        send_telegram(f"🔄 กำลังเปิดออเดอร์ {direction.upper()}...")
        logger.info(f"Opening {direction} order: size={order_size}, price={current_price}")
        
        # ตั้ง leverage ก่อน
        try:
            exchange.set_leverage(leverage, symbol)
            logger.info(f"Leverage set to {leverage}x")
        except Exception as e:
            logger.warning(f"Set leverage warning: {e}")
        
        # พารามิเตอร์สำหรับ OKX
        params = {
            'tdMode': 'cross',
            'ordType': 'market',
            'tpTriggerPx': str(tp_price),
            'tpOrdPx': '-1',
            'tpTriggerPxType': 'last',
            'slTriggerPx': str(sl_price),
            'slOrdPx': '-1',
            'slTriggerPxType': 'last'
        }
        
        # สร้างออเดอร์
        order = exchange.create_order(symbol, 'market', side, order_size, None, params)
        
        # ตรวจสอบว่าออเดอร์สำเร็จ
        if order and order.get('id'):
            # อัพเดตสถานะ
            current_position = direction
            entry_price = current_price
            order_id = order.get('id')
            sl_moved = False
            
            # ส่งแจ้งเตือน
            message = f"""{emoji} เปิดออเดอร์ {direction.upper()} สำเร็จ!
💰 Entry: {current_price:,.1f} USDT
🎯 TP: {tp_price:,.1f} USDT  
🛡️ SL: {sl_price:,.1f} USDT
📊 ขนาด: {order_size:.6f} BTC
💵 ใช้เงิน: {use_balance:,.1f} USDT ({leverage}x)
Order ID: {order_id}"""
            
            send_telegram(message)
            logger.info(f"Order opened successfully: {direction} at {current_price}")
            return True
        else:
            send_telegram(f"❌ ไม่สามารถเปิดออเดอร์ได้ - ไม่ได้รับ Order ID")
            return False
        
    except ccxt.InsufficientFunds as e:
        send_telegram(f"❌ เงินทุนไม่เพียงพอ: {e}")
        logger.error(f"Insufficient funds: {e}")
        return False
        
    except ccxt.InvalidOrder as e:
        send_telegram(f"❌ ออเดอร์ไม่ถูกต้อง: {e}")
        logger.error(f"Invalid order: {e}")
        return False
        
    except Exception as e:
        send_telegram(f"⛔️ Error: ไม่สามารถเปิดออเดอร์ได้\nรายละเอียด: {e}")
        logger.error(f"Order failed: {e}")
        return False

# === เลื่อน SL เป็นกันทุน ===
def move_sl_to_breakeven():
    global sl_moved
    
    try:
        if sl_moved:
            return
        
        if current_position == 'long':
            new_sl = entry_price - be_sl
        else:
            new_sl = entry_price + be_sl
        
        sl_moved = True
        message = f"""🔄 เลื่อน SL เป็นกันทุน
📍 Entry: {entry_price:,.1f}
🛡️ SL ใหม่: {new_sl:,.1f}
💰 กำไรปัจจุบัน: +{be_profit_trigger} USDT"""
        
        send_telegram(message)
        logger.info(f"SL moved to breakeven: {new_sl}")
        
    except Exception as e:
        logger.error(f"Move SL error: {e}")

# === ตรวจสอบโพซิชันและจัดการ SL ===
def monitor_position():
    global current_position, sl_moved, entry_price, order_id
    
    if not current_position:
        return
        
    try:
        pos_info = get_current_position()
        
        if not pos_info:
            # โพซิชันถูกปิดแล้ว
            current_price = float(exchange.fetch_ticker(symbol)['last'])
            
            if current_position == 'long':
                pnl_points = current_price - entry_price
                if current_price >= entry_price + tp_value:
                    close_reason = "TP"
                    emoji = "✅"
                elif current_price <= entry_price - sl_value:
                    close_reason = "SL"
                    emoji = "❌"
                else:
                    close_reason = "บังคับปิด"
                    emoji = "🔄"
            else:
                pnl_points = entry_price - current_price
                if current_price <= entry_price - tp_value:
                    close_reason = "TP"
                    emoji = "✅"
                elif current_price >= entry_price + sl_value:
                    close_reason = "SL"
                    emoji = "❌"
                else:
                    close_reason = "บังคับปิด"
                    emoji = "🔄"
            
            # คำนวณ PnL ใน USDT
            position_value = (portfolio_balance * 0.5 * leverage) / entry_price
            pnl_usdt = pnl_points * position_value
            
            message = f"""{emoji} ปิดออเดอร์: {close_reason}
P&L: {pnl_usdt:+,.1f} USDT
ราคาปิด: {current_price:,.1f}"""
            
            send_telegram(message)
            logger.info(f"Position closed: {close_reason}, PnL: {pnl_usdt:.2f}")
            
            add_trade_result(close_reason, pnl_usdt)
            
            # รีเซ็ตสถานะ
            current_position = None
            entry_price = None
            order_id = None
            sl_moved = False
            return
        
        # คำนวณ PnL ปัจจุบัน
        current_price = float(exchange.fetch_ticker(symbol)['last'])
        
        if current_position == 'long':
            pnl = current_price - entry_price
        else:
            pnl = entry_price - current_price
        
        # เลื่อน SL เมื่อกำไรถึงเป้า
        if not sl_moved and pnl >= be_profit_trigger:
            move_sl_to_breakeven()
        
        # แจ้งสถานะ PnL ทุก 5 นาที
        if int(time.time()) % 300 == 0:
            logger.info(f"Position: {current_position}, PnL: {pnl:.1f}, Price: {current_price:.1f}")
        
    except Exception as e:
        logger.error(f"Monitor position error: {e}")

# === รายงานประจำวัน ===
def daily_report():
    global last_daily_report
    
    now = datetime.now()
    today = now.date()
    
    if last_daily_report == today:
        return
    
    try:
        balance = get_portfolio_balance()
        
        today_str = now.strftime('%Y-%m-%d')
        if daily_stats['date'] == today_str:
            tp_count = daily_stats['tp_count']
            sl_count = daily_stats['sl_count']
            total_pnl = daily_stats['total_pnl']
        else:
            tp_count = 0
            sl_count = 0
            total_pnl = 0
        
        pnl_from_start = balance - initial_balance if initial_balance > 0 else 0
        
        message = f"""📊 รายงานประจำวัน
🔹 กำไรสุทธิ: {total_pnl:+,.1f} USDT
🔹 TP: {tp_count} | SL: {sl_count}
🔹 คงเหลือ: {balance:,.1f} USDT
🔹 กำไรรวม: {pnl_from_start:+,.1f} USDT
⏱ บอททำงานปกติ ✅
วันที่: {now.strftime('%d/%m/%Y %H:%M')}"""
        
        send_telegram(message)
        last_daily_report = today
        logger.info("Daily report sent")
        
    except Exception as e:
        logger.error(f"Daily report error: {e}")

# === ฟังก์ชันรายงานประจำวันแบบ Background ===
def daily_report_scheduler():
    while True:
        try:
            time.sleep(3600)
            daily_report()
        except Exception as e:
            logger.error(f"Daily report scheduler error: {e}")

# === แจ้งเตือนเมื่อบอทเริ่มทำงาน ===
def send_startup_message():
    global initial_balance
    
    try:
        initial_balance = get_portfolio_balance()
        startup_time = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        
        message = f"""🤖 EMA Cross Bot เริ่มทำงาน
💰 ยอดเริ่มต้น: {initial_balance:,.1f} USDT
⏰ เวลา: {startup_time}
📊 เฟรม: {timeframe} | Leverage: {leverage}x
🎯 TP: {tp_value} | SL: {sl_value}
🔧 ใช้เงิน: 50% ต่อออเดอร์
📈 รอสัญญาณ EMA Cross..."""
        
        send_telegram(message)
        logger.info("Bot started successfully")
        
    except Exception as e:
        logger.error(f"Startup error: {e}")

# === MAIN LOOP ===
def main():
    global portfolio_balance, initial_balance
    
    try:
        load_daily_stats()
        send_startup_message()
        
        daily_thread = threading.Thread(target=daily_report_scheduler, daemon=True)
        daily_thread.start()
        
    except Exception as e:
        send_telegram(f"⛔️ Startup Error: {e}")
        logger.error(f"Startup error: {e}")
        time.sleep(30)
        return
    
    while True:
        try:
            # ตรวจสอบโพซิชันก่อน
            monitor_position()
            
            # ถ้าไม่มีโพซิชัน ให้เช็คสัญญาณ
            if not current_position:
                signal = check_ema_cross()
                
                if signal:
                    logger.info(f"🔥 EMA Cross Signal detected: {signal}")
                    success = open_order_with_tpsl(signal)
                    
                    if success:
                        logger.info(f"Order opened successfully for {signal}")
                        time.sleep(10)  # รอหลังเปิดออเดอร์
                    else:
                        logger.error(f"Failed to open order for {signal}")
                        time.sleep(5)
            
            time.sleep(10)  # เช็คทุก 10 วินาที
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            send_telegram("🛑 Bot หยุดทำงานโดยผู้ใช้")
            break
            
        except ccxt.NetworkError as e:
            logger.error(f"Network error: {e}")
            send_telegram(f"⛔️ Network Error: รอ 30 วินาที...")
            time.sleep(30)
            
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error: {e}")
            send_telegram(f"⛔️ Exchange Error: {e}")
            time.sleep(30)
            
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            send_telegram(f"⛔️ Main Loop Error: {e}")
            time.sleep(30)

if __name__ == '__main__':
    main()
