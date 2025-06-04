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
api_key = '8f528085-448c-4480-a2b0-d7f72afb38ad'       # ใส่ API KEY
secret = '05A665CEAF8B2161483DF63CB10085D2'  # ใส่ secret ของคุณ
password = 'Ji'  # ใส่ password ของคุณ
symbol = 'BTC/USDT:USDT'
timeframe = '15m'
leverage = 20
tp_value = 500
sl_value = 990
be_profit_trigger = 350
be_sl = 100

# === ตั้งค่าการจัดการทุน ===
CAPITAL_USAGE_PERCENT = 0.5  # ใช้ 50% ของยอดคงเหลือปัจจุบันในการเปิดออเดอร์แต่ละไม้

telegram_token = '7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY'
telegram_chat_id = '8104629569'

# === ไฟล์เก็บข้อมูล ===
STATS_FILE = 'trading_stats.json'

# === สถานะการเทรด ===
current_position = None  # None, 'long', 'short'
entry_price = None
order_id = None
sl_moved = False
last_ema_state = None
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
    
    # เช็คว่าเป็นวันใหม่หรือไม่
    if daily_stats['date'] != today:
        reset_daily_stats()
    
    # เพิ่มข้อมูลการเทรด
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
        logger.error("⛔️ Error: ไม่สามารถส่งข้อความ Telegram ได้")

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

# === ตรวจสอบการตัดกันของ EMA (เร็วที่สุด - เช็ค 1-2 เทียน) ===
def check_ema_cross():
    global last_ema_state
    
    try:
        # ดึงข้อมูลเทียน
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=250)
        closes = [candle[4] for candle in ohlcv]
        
        if len(closes) < 200:
            return None
        
        # คำนวณ EMA สำหรับ 2 เทียนล่าสุด (เร็วที่สุด)
        current_ema50 = calculate_ema(closes, 50)
        current_ema200 = calculate_ema(closes, 200)
        prev_ema50 = calculate_ema(closes[:-1], 50)
        prev_ema200 = calculate_ema(closes[:-1], 200)
        
        if None in [current_ema50, current_ema200, prev_ema50, prev_ema200]:
            return None
        
        # ตรวจสอบการตัดกันแบบเร็วที่สุด (ตัดในเทียนล่าสุด)
        cross_signal = None
        
        # Golden Cross: EMA50 ตัดขึ้นเหนือ EMA200 ในเทียนล่าสุด
        if prev_ema50 <= prev_ema200 and current_ema50 > current_ema200:
            cross_signal = 'long'
            logger.info(f"Golden Cross detected (Ultra Fast): EMA50({current_ema50:.2f}) > EMA200({current_ema200:.2f})")
        
        # Death Cross: EMA50 ตัดลงใต้ EMA200 ในเทียนล่าสุด
        elif prev_ema50 >= prev_ema200 and current_ema50 < current_ema200:
            cross_signal = 'short'
            logger.info(f"Death Cross detected (Ultra Fast): EMA50({current_ema50:.2f}) < EMA200({current_ema200:.2f})")
        
        return cross_signal
        
    except Exception as e:
        logger.error(f"EMA calculation error: {e}")
        send_telegram(f"⛔️ Error: ไม่สามารถคำนวณ EMA ได้\nรายละเอียด: {e} | Retry อีกครั้งใน 30 วินาที")
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

# === เปิดออเดอร์พร้อม TP/SL (ใช้ 50% ของยอดคงเหลือปัจจุบัน) ===
def open_order_with_tpsl(direction):
    global current_position, entry_price, order_id, sl_moved
    
    try:
        # เช็คว่ามีโพซิชันอยู่หรือไม่
        existing_pos = get_current_position()
        if existing_pos:
            logger.info("มีโพซิชันอยู่แล้ว ข้ามการเปิดออเดอร์")
            return False
        
        # ดึงยอดคงเหลือปัจจุบัน
        balance = get_portfolio_balance()
        if balance <= 0:
            send_telegram("⛔️ Error: ไม่สามารถดึงยอดคงเหลือได้")
            return False
        
        # คำนวณทุนที่จะใช้ (50% ของยอดคงเหลือปัจจุบัน)
        use_balance = balance * CAPITAL_USAGE_PERCENT
        
        # ดึงราคาปัจจุบัน
        ticker = exchange.fetch_ticker(symbol)
        current_price = float(ticker['last'])
        
        # คำนวณขนาดออเดอร์ตาม leverage
        order_size = (use_balance * leverage) / current_price
        order_size = round(order_size, 6)  # ปรับเป็นทศนิยม 6 ตำแหน่ง
        
        # คำนวณ TP และ SL
        if direction == 'long':
            tp_price = current_price + tp_value
            sl_price = current_price - sl_value
            side = 'buy'
            emoji = '📈'
        else:
            tp_price = current_price - tp_value
            sl_price = current_price + sl_value
            side = 'sell'
            emoji = '📉'
        
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
        
        # ส่งแจ้งเตือนแบบใหม่
        message = f"""{emoji} เข้าซื้อ {direction.upper()}
Entry: {current_price:,.0f}
TP: {tp_price:,.0f}
SL: {sl_price:,.0f}
💰 ใช้เงิน: {use_balance:,.1f} USDT ({leverage}x)
💼 จากยอดคงเหลือ: {balance:,.1f} USDT ({CAPITAL_USAGE_PERCENT*100:.0f}%)
📊 ขนาด: {order_size:.6f} BTC"""
        
        send_telegram(message)
        logger.info(f"Order opened: {direction} at {current_price}, size: {order_size}, capital used: {use_balance} from balance: {balance}")
        return True
        
    except Exception as e:
        error_msg = f"⛔️ Error: ไม่สามารถเปิดออเดอร์ได้\nรายละเอียด: {e} | Retry อีกครั้งใน 30 วินาที"
        send_telegram(error_msg)
        logger.error(f"Order failed: {e}")
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
        
        sl_moved = True
        message = f"""🔄 ราคาวิ่ง +{be_profit_trigger} แล้ว → เลื่อน SL ไปที่ราคาเข้า (Break-even)
📍 Entry: {entry_price:,.0f}
🛡️ SL ใหม่: {new_sl:,.0f}"""
        
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
        # เช็คโพซิชันจริง
        pos_info = get_current_position()
        
        if not pos_info:
            # โพซิชันถูกปิดแล้ว - ตรวจสอบว่าปิดด้วยอะไร
            current_price = float(exchange.fetch_ticker(symbol)['last'])
            
            # คำนวณ PnL โดยประมาณ
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
            else:  # short
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
            
            # คำนวณ PnL ใน USDT (ใช้ยอดคงเหลือปัจจุบัน)
            current_balance = get_portfolio_balance()
            use_balance = current_balance * CAPITAL_USAGE_PERCENT
            position_value = (use_balance * leverage) / entry_price
            pnl_usdt = pnl_points * position_value
            
            # ส่งข้อความแจ้งเตือน
            if close_reason in ["TP", "SL"]:
                if pnl_usdt > 0:
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
กำไร: +{abs(pnl_usdt):,.0f} USDT
💰 ทุนที่ใช้: {use_balance:,.1f} USDT"""
                else:
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
ขาดทุน: {pnl_usdt:,.0f} USDT
💰 ทุนที่ใช้: {use_balance:,.1f} USDT"""
            else:
                message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
P&L: {pnl_usdt:,.0f} USDT
💰 ทุนที่ใช้: {use_balance:,.1f} USDT"""
            
            send_telegram(message)
            logger.info(f"Position closed: {close_reason}, PnL: {pnl_usdt:.2f}")
            
            # บันทึกสถิติ
            add_trade_result(close_reason, pnl_usdt)
            
            # รีเซ็ตสถานะ
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
        send_telegram(f"⛔️ Error: ไม่สามารถตรวจสอบโพซิชันได้\nรายละเอียด: {e}")

# === รายงานประจำวัน (พร้อมสถิติ) ===
def daily_report():
    global last_daily_report
    
    now = datetime.now()
    today = now.date()
    
    # เช็คว่าส่งรายงานวันนี้แล้วหรือยัง
    if last_daily_report == today:
        return
    
    try:
        balance = get_portfolio_balance()
        
        # ดึงสถิติประจำวัน
        today_str = now.strftime('%Y-%m-%d')
        if daily_stats['date'] == today_str:
            tp_count = daily_stats['tp_count']
            sl_count = daily_stats['sl_count']
            total_pnl = daily_stats['total_pnl']
        else:
            tp_count = 0
            sl_count = 0
            total_pnl = 0
        
        # คำนวณกำไร/ขาดทุนจากเมื่อเริ่มต้น
        pnl_from_start = balance - initial_balance if initial_balance > 0 else 0
        
        message = f"""📊 รายงานประจำวัน
🔹 กำไรสุทธิ: {total_pnl:+,.0f} USDT
🔹 SL: {sl_count} ครั้ง
🔹 TP: {tp_count} ครั้ง
🔹 คงเหลือ: {balance:,.1f} USDT
🔹 ทุนต่อเทรดถัดไป: {balance * CAPITAL_USAGE_PERCENT:,.1f} USDT ({CAPITAL_USAGE_PERCENT*100:.0f}%)
⏱ บอทยังทำงานปกติ ✅
วันที่: {now.strftime('%d/%m/%Y %H:%M')}"""
        
        send_telegram(message)
        last_daily_report = today
        logger.info("Daily report sent")
        
    except Exception as e:
        logger.error(f"Daily report error: {e}")
        send_telegram(f"⛔️ Error: ไม่สามารถส่งรายงานประจำวันได้\nรายละเอียด: {e}")

# === ฟังก์ชันรายงานประจำวันแบบ Background ===
def daily_report_scheduler():
    while True:
        try:
            time.sleep(3600)  # เช็คทุกชั่วโมง
            daily_report()
        except Exception as e:
            logger.error(f"Daily report scheduler error: {e}")

# === แจ้งเตือนเมื่อบอทเริ่มทำงาน ===
def send_startup_message():
    global initial_balance
    
    try:
        initial_balance = get_portfolio_balance()
        startup_time = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        
        message = f"""🔄 บอทเริ่มทำงาน
🤖 EMA Cross Trading Bot
💼 ยอดคงเหลือ: {initial_balance:,.1f} USDT
💰 ทุนต่อเทรดแรก: {initial_balance * CAPITAL_USAGE_PERCENT:,.1f} USDT ({CAPITAL_USAGE_PERCENT*100:.0f}%)
⏰ เวลาเริ่ม: {startup_time}
📊 เฟรม: {timeframe} | Leverage: {leverage}x
🎯 TP: {tp_value} | SL: {sl_value}
🔧 ใช้เงิน: {CAPITAL_USAGE_PERCENT*100:.0f}% ของยอดคงเหลือปัจจุบัน
📈 รอสัญญาณ EMA Cross..."""
        
        send_telegram(message)
        logger.info(f"Startup message sent - Using {CAPITAL_USAGE_PERCENT*100:.0f}% of current balance per trade")
        
    except Exception as e:
        logger.error(f"Startup message error: {e}")

# === MAIN LOOP ===
def main():
    global portfolio_balance, initial_balance
    
    try:
        # โหลดสถิติ
        load_daily_stats()
        
        # ส่งข้อความเริ่มต้น
        send_startup_message()
        
        # เริ่ม Daily Report Scheduler
        daily_thread = threading.Thread(target=daily_report_scheduler, daemon=True)
        daily_thread.start()
        
    except Exception as e:
        error_msg = f"⛔️ Error: ไม่สามารถเชื่อมต่อ OKX API\nรายละเอียด: {e} | Retry อีกครั้งใน 30 วินาที"
        send_telegram(error_msg)
        logger.error(f"Startup error: {e}")
        time.sleep(30)
        return
    
    while True:
        try:
            # ตรวจสอบโพซิชันปัจจุบัน
            monitor_position()
            
            # ถ้าไม่มีโพซิชัน ให้เช็คสัญญาณ
            if not current_position:
                signal = check_ema_cross()
                
                if signal:
                    logger.info(f"EMA Cross Signal: {signal}")
                    success = open_order_with_tpsl(signal)
                    if success:
                        time.sleep(5)  # รอสักครู่หลังเปิดออเดอร์
            
            time.sleep(8)  # เช็คทุก 8 วินาที (เร็วที่สุด)
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            send_telegram("🛑 Bot หยุดทำงานโดยผู้ใช้")
            break
            
        except ccxt.NetworkError as e:
            error_msg = f"⛔️ Error: ไม่สามารถเชื่อมต่อ OKX API\nรายละเอียด: Network Timeout | Retry อีกครั้งใน 30 วินาที"
            logger.error(error_msg)
            send_telegram(error_msg)
            time.sleep(30)
            
        except ccxt.ExchangeError as e:
            error_msg = f"⛔️ Error: OKX Exchange Error\nรายละเอียด: {e} | Retry อีกครั้งใน 30 วินาที"
            logger.error(error_msg)
            send_telegram(error_msg)
            time.sleep(30)
            
        except Exception as e:
            error_msg = f"⛔️ Error: Main loop error\nรายละเอียด: {e} | Retry อีกครั้งใน 30 วินาที"
            logger.error(error_msg)
            send_telegram(error_msg)
            time.sleep(30)

if __name__ == '__main__':
    main()
