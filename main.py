import ccxt
import time
import requests
from datetime import datetime, timedelta
import logging
import threading

# === ตั้งค่า Logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === ตั้งค่า ===
api_key = '8f528085-448c-4480-a2b0-d7f72afb38ad'       # ใส่ API KEY
secret = '05A665CEAF8B2161483DF63CB10085D2'  # ใส่ secret ของคุณ
password = 'Jirawat1-'  # ใส่ password ของคุณ
symbol = 'BTC/USDT:USDT'
timeframe = '15m'
leverage = 20
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
last_ema_state = None
portfolio_balance = 0
last_daily_report = None

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
        # แจ้งเตือน Error การส่ง Telegram
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

# === ตรวจสอบการตัดกันของ EMA (เร็วขึ้น) ===
def check_ema_cross():
    global last_ema_state
    
    try:
        # ดึงข้อมูลเทียน
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=250)
        closes = [candle[4] for candle in ohlcv]
        
        if len(closes) < 200:
            return None
        
        # คำนวณ EMA สำหรับ 3 เทียนล่าสุด (เพื่อให้เร็วขึ้น)
        current_ema50 = calculate_ema(closes, 50)
        current_ema200 = calculate_ema(closes, 200)
        prev_ema50 = calculate_ema(closes[:-1], 50)
        prev_ema200 = calculate_ema(closes[:-1], 200)
        prev2_ema50 = calculate_ema(closes[:-2], 50)
        prev2_ema200 = calculate_ema(closes[:-2], 200)
        
        if None in [current_ema50, current_ema200, prev_ema50, prev_ema200, prev2_ema50, prev2_ema200]:
            return None
        
        # ตรวจสอบการตัดกันแบบเร็ว (ไม่รอให้ตัดชัดเจน)
        cross_signal = None
        
        # Golden Cross: EMA50 กำลังตัดขึ้นเหนือ EMA200
        if (prev2_ema50 <= prev2_ema200 and 
            prev_ema50 >= prev_ema200 and 
            current_ema50 > current_ema200):
            cross_signal = 'long'
            logger.info(f"Golden Cross detected (Fast): EMA50({current_ema50:.2f}) > EMA200({current_ema200:.2f})")
        
        # Death Cross: EMA50 กำลังตัดลงใต้ EMA200
        elif (prev2_ema50 >= prev2_ema200 and 
              prev_ema50 <= prev_ema200 and 
              current_ema50 < current_ema200):
            cross_signal = 'short'
            logger.info(f"Death Cross detected (Fast): EMA50({current_ema50:.2f}) < EMA200({current_ema200:.2f})")
        
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

# === เปิดออเดอร์พร้อม TP/SL (ใช้ 50% ของพอร์ต) ===
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
        if balance <= 0:
            send_telegram("⛔️ Error: ไม่สามารถดึงยอดคงเหลือได้")
            return False
        
        # คำนวณขนาดออเดอร์ (50% ของพอร์ต)
        use_balance = balance * 1  # ใช้ 50% ของพอร์ต
        
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
📊 ขนาด: {order_size:.6f} BTC"""
        
        send_telegram(message)
        logger.info(f"Order opened: {direction} at {current_price}, size: {order_size}")
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
            
            if current_position == 'long':
                pnl = current_price - entry_price
                if current_price >= entry_price + tp_value:
                    close_reason = "TP"
                    emoji = "✅"
                    pnl_usdt = pnl * exchange.fetch_ticker(symbol)['last'] / leverage  # ประมาณการ
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
กำไร: +{abs(pnl_usdt):,.0f} USDT"""
                elif current_price <= entry_price - sl_value:
                    close_reason = "SL"
                    emoji = "❌"
                    pnl_usdt = pnl * exchange.fetch_ticker(symbol)['last'] / leverage
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
ขาดทุน: {pnl_usdt:,.0f} USDT"""
                else:
                    close_reason = "บังคับปิด"
                    emoji = "🔄"
                    pnl_usdt = pnl * exchange.fetch_ticker(symbol)['last'] / leverage
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
P&L: {pnl_usdt:,.0f} USDT"""
            else:  # short
                pnl = entry_price - current_price
                if current_price <= entry_price - tp_value:
                    close_reason = "TP"
                    emoji = "✅"
                    pnl_usdt = pnl * exchange.fetch_ticker(symbol)['last'] / leverage
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
กำไร: +{abs(pnl_usdt):,.0f} USDT"""
                elif current_price >= entry_price + sl_value:
                    close_reason = "SL"
                    emoji = "❌"
                    pnl_usdt = pnl * exchange.fetch_ticker(symbol)['last'] / leverage
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
ขาดทุน: {pnl_usdt:,.0f} USDT"""
                else:
                    close_reason = "บังคับปิด"
                    emoji = "🔄"
                    pnl_usdt = pnl * exchange.fetch_ticker(symbol)['last'] / leverage
                    message = f"""{emoji} ปิดออเดอร์ด้วย {close_reason}
P&L: {pnl_usdt:,.0f} USDT"""
            
            send_telegram(message)
            logger.info(f"Position closed: {close_reason}")
            
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

# === รายงานประจำวัน ===
def daily_report():
    global last_daily_report
    
    now = datetime.now()
    today = now.date()
    
    # เช็คว่าส่งรายงานวันนี้แล้วหรือยัง
    if last_daily_report == today:
        return
    
    try:
        balance = get_portfolio_balance()
        message = f"""⏱ บอทยังทำงานปกติ ✅
ยอดคงเหลือ: {balance:,.1f} USDT
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

# === MAIN LOOP ===
def main():
    global portfolio_balance
    
    try:
        # เริ่มต้น
        portfolio_balance = get_portfolio_balance()
        send_telegram(f"🤖 EMA Cross Bot เริ่มทำงาน\nยอดคงเหลือ: {portfolio_balance:,.1f} USDT")
        logger.info("Bot started")
        
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
            
            time.sleep(10)  # เช็คทุก 10 วินาที (เร็วขึ้น)
            
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
