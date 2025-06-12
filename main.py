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
secret = '05A665CEAF8B2161483DF63CB10085D2'   # ใส่ SECRET KEY ของคุณ
password = 'Jirawat1-' # ใส่ Password (ถ้ามี) ของคุณ

symbol = 'BTC/USDT:USDT'
timeframe = '15m'
# order_size จะถูกคำนวณจาก 80% ของพอร์ต
leverage = 25  # *** เปลี่ยน Leverage เป็น 25x ***
tp_value = 500  # TP เป็นค่าคงที่ (USDT)
sl_value = 990  # SL เป็นค่าคงที่ (USDT)
be_profit_trigger_usdt = 350 # กำไร 350 USDT จะเลื่อน SL
be_sl_offset_usdt = 100 # เลื่อน SL ให้ต่ำกว่าราคาเข้าเล็กน้อย 100 USDT (กันค่าธรรมเนียม)

telegram_token = '7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY'
telegram_chat_id = '8104629569'

# === สถานะการเทรด ===
current_position = None  # None, 'long', 'short'
entry_price = None
order_id = None
sl_moved = False
last_ema_state = None  # เก็บสถานะ EMA ก่อนหน้า ('above', 'below')
last_cross_signal = None # เก็บสัญญาณ cross ล่าสุดที่ตรวจพบ
last_daily_report_time = None # เวลาที่ส่งรายงานประจำวันล่าสุด

# === Exchange Setup ===
exchange = ccxt.okx({
    'apiKey': api_key,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})
exchange.set_sandbox_mode(False) # ตั้งเป็น False สำหรับบัญชีจริง

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
        logger.error(f"Telegram error: {e}. Message: {msg}")

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
    global last_ema_state, last_cross_signal
    
    try:
        # ดึงข้อมูลเทียน
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=250)
        closes = [candle[4] for candle in ohlcv]
        
        if len(closes) < 200:
            logger.warning("Not enough data to calculate EMA.")
            return None
        
        # คำนวณ EMA สำหรับเทียนปัจจุบันและก่อนหน้า
        current_ema50 = calculate_ema(closes, 50)
        current_ema200 = calculate_ema(closes, 200)
        prev_ema50 = calculate_ema(closes[:-1], 50)
        prev_ema200 = calculate_ema(closes[:-1], 200)
        
        if None in [current_ema50, current_ema200, prev_ema50, prev_ema200]:
            logger.warning("EMA calculation returned None.")
            return None
        
        # เก็บสถานะปัจจุบัน
        current_ema_position = 'above' if current_ema50 > current_ema200 else 'below'
        
        cross_signal = None
        
        # Golden Cross: EMA50 ตัดขึ้นเหนือ EMA200
        if prev_ema50 <= prev_ema200 and current_ema50 > current_ema200:
            cross_signal = 'long'
            logger.info(f"Golden Cross detected: EMA50({current_ema50:.2f}) > EMA200({current_ema200:.2f})")
        
        # Death Cross: EMA50 ตัดลงใต้ EMA200  
        elif prev_ema50 >= prev_ema200 and current_ema50 < current_ema200:
            cross_signal = 'short'
            logger.info(f"Death Cross detected: EMA50({current_ema50:.2f}) < EMA200({current_ema200:.2f})")
        
        # อัพเดต last_ema_state สำหรับการตรวจสอบครั้งถัดไป
        last_ema_state = current_ema_position
        
        # เพื่อให้เปิดออเดอร์ไวกว่านี้:
        # ส่งสัญญาณเฉพาะเมื่อมีการตัดกันใหม่ และไม่ใช่สัญญาณเดียวกับที่ส่งไปล่าสุด
        if cross_signal and cross_signal != last_cross_signal:
            last_cross_signal = cross_signal
            return cross_signal
        elif not cross_signal:
            # ถ้าไม่มีสัญญาณตัดกัน ให้รีเซ็ต last_cross_signal เพื่อรอสัญญาณใหม่
            last_cross_signal = None
            
        return None # ไม่มีสัญญาณตัดกันใหม่
        
    except Exception as e:
        error_msg = f"❌ Error in check_ema_cross: {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: การตรวจสอบ EMA ล้มเหลว\nรายละเอียด: {error_msg}")
        return None

# === ตรวจสอบโพซิชันปัจจุบัน ===
def get_current_position():
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            if float(pos['contracts']) != 0: # ตรวจสอบ contracts ไม่เท่ากับ 0
                return {
                    'side': 'long' if float(pos['contracts']) > 0 else 'short',
                    'size': abs(float(pos['contracts'])),
                    'entry_price': float(pos['entryPrice']),
                    'unrealized_pnl': float(pos['unrealizedPnl']),
                    'liquidation_price': float(pos['liquidationPrice']) if 'liquidationPrice' in pos else None
                }
        return None
    except ccxt.NetworkError as e:
        error_msg = f"Network Error: {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: การเชื่อมต่อ API มีปัญหา\nรายละเอียด: {error_msg} | ลองอีกครั้งใน 30 วินาที")
        time.sleep(30)
        return None
    except Exception as e:
        error_msg = f"Error fetching position: {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: ไม่สามารถดึงข้อมูลโพซิชันได้\nรายละเอียด: {error_msg}")
        return None

# === ดึงยอดคงเหลือ USDT ===
def get_balance():
    try:
        balance = exchange.fetch_balance()
        if 'USDT' in balance['total']:
            return balance['total']['USDT']
        return 0
    except ccxt.NetworkError as e:
        error_msg = f"Network Error: {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: การเชื่อมต่อ API มีปัญหา\nรายละเอียด: {error_msg} | ลองอีกครั้งใน 30 วินาที")
        time.sleep(30)
        return 0
    except Exception as e:
        error_msg = f"Error fetching balance: {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: ไม่สามารถดึงยอดคงเหลือได้\nรายละเอียด: {error_msg}")
        return 0

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
        
        # คำนวณขนาดออเดอร์ 80% ของพอร์ต
        available_balance = get_balance()
        if available_balance <= 0:
            send_telegram("⚠️ ไม่มียอดคงเหลือ USDT เพียงพอสำหรับการเปิดออเดอร์")
            logger.warning("No USDT balance to open order.")
            return False

        # คำนวณขนาดออเดอร์ (BTC) จาก 80% ของพอร์ต Leverage และราคาปัจจุบัน
        # (80% ของ balance * leverage) / current_price = quantity
        calculated_order_size_usdt = available_balance * 0.80 * leverage # *** เปลี่ยนเป็น 0.80 ***
        calculated_order_size_btc = calculated_order_size_usdt / current_price

        # ตรวจสอบขั้นต่ำของขนาดออเดอร์ (อาจจะต้องดึงมาจาก exchange info)
        market = exchange.market(symbol)
        # ใช้ Max(min_amount, min_notional_value / current_price) เพื่อให้แน่ใจว่าผ่านทั้ง 2 เกณฑ์
        min_amount = market['limits']['amount']['min'] if 'amount' in market['limits'] else 0.00001 # ค่าเริ่มต้นเผื่อไว้
        min_notional = market['limits']['cost']['min'] if 'cost' in market['limits'] and market['limits']['cost']['min'] is not None else 10 # OKX BTC/USDT notional usually ~100
        
        # คำนวณ min_amount ที่แท้จริงโดยพิจารณา Notional Value
        # ตรวจสอบว่า min_notional_btc_equivalent มีค่าที่สมเหตุสมผลหรือไม่
        min_notional_btc_equivalent = min_notional / current_price
        
        actual_min_order_btc = max(min_amount, min_notional_btc_equivalent)
        
        if calculated_order_size_btc < actual_min_order_btc:
            send_telegram(f"⚠️ ขนาดออเดอร์ที่คำนวณได้ ({calculated_order_size_btc:.5f} BTC) ต่ำกว่าขั้นต่ำ ({actual_min_order_btc:.5f} BTC) ที่ {min_notional:.2f} USDT")
            logger.warning(f"Calculated order size {calculated_order_size_btc:.5f} BTC is below minimum {actual_min_order_btc:.5f} BTC (derived from notional {min_notional:.2f} USDT).")
            return False

        order_size_btc = round(calculated_order_size_btc, 5) # ปัดเศษตามความเหมาะสม

        # คำนวณ TP และ SL
        if direction == 'long':
            tp_price = current_price + tp_value
            sl_price = current_price - sl_value
            side = 'buy'
        else: # direction == 'short'
            tp_price = current_price - tp_value
            sl_price = current_price + sl_value
            side = 'sell'
        
        # พารามิเตอร์สำหรับ OKX (รองรับ TP/SL ในคำสั่งเดียว)
        params = {
            'tdMode': 'cross',
            'ordType': 'market',
            'lever': str(leverage),
            'reduceOnly': False, # ต้องเป็น False ในการเปิดโพซิชันใหม่
            'tpTriggerPx': str(round(tp_price, 2)),
            'tpOrdPx': '-1', # -1 หมายถึง market order เมื่อ TP ถึง
            'tpTriggerPxType': 'last',
            'slTriggerPx': str(round(sl_price, 2)), 
            'slOrdPx': '-1', # -1 หมายถึง market order เมื่อ SL ถึง
            'slTriggerPxType': 'last'
        }
        
        logger.info(f"Attempting to open {direction} order: size={order_size_btc:.5f} BTC, current_price={current_price:.2f}, TP={tp_price:.2f}, SL={sl_price:.2f}")

        # สร้างออเดอร์
        order = exchange.create_order(symbol, 'market', side, order_size_btc, None, params)
        
        # อัพเดตสถานะ
        current_position = direction
        entry_price = current_price
        order_id = order.get('id')
        sl_moved = False
        
        # ส่งแจ้งเตือน
        message = f"""📈 เข้าซื้อ {direction.upper()}
💰 ราคาเข้า: {entry_price:.2f}
🎯 TP: {tp_price:.2f} (+{tp_value:.2f})
🛡️ SL: {sl_price:.2f} (-{sl_value:.2f})
📊 ขนาด: {order_size_btc:.5f} BTC ({calculated_order_size_usdt:.2f} USDT @ {leverage}x)
Order ID: {order_id}"""
        
        send_telegram(message)
        logger.info(f"Order opened successfully: {direction} at {entry_price:.2f}")
        return True
        
    except ccxt.NetworkError as e:
        error_msg = f"Network Error (Order Fail): {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: ไม่สามารถเชื่อมต่อ OKX API (Order Fail)\nรายละเอียด: {error_msg} | Retry อีกครั้งใน 30 วินาที")
        time.sleep(30)
        return False
    except ccxt.ExchangeError as e:
        error_msg = f"Exchange Error (Order Fail): {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: คำสั่งซื้อล้มเหลว\nรายละเอียด: {error_msg}")
        return False
    except Exception as e:
        error_msg = f"❌ เปิดออเดอร์ล้มเหลว: {e}"
        send_telegram(f"⛔️ Error: เปิดออเดอร์ล้มเหลว\nรายละเอียด: {error_msg}")
        logger.error(error_msg)
        return False

# === เลื่อน SL เป็นกันทุน ===
def move_sl_to_breakeven():
    global sl_moved
    
    if sl_moved or not current_position or not entry_price:
        return
        
    try:
        pos_info = get_current_position()
        if not pos_info:
            logger.info("ไม่มีโพซิชันอยู่ ไม่สามารถเลื่อน SL ได้")
            return

        # คำนวณ SL ใหม่ (กันทุน + offset เล็กน้อย)
        if current_position == 'long':
            new_sl_price = entry_price + be_sl_offset_usdt
        else: # short
            new_sl_price = entry_price - be_sl_offset_usdt

        sl_moved = True
        message = f"""🔄 ราคาวิ่ง +{be_profit_trigger_usdt} USDT แล้ว → เลื่อน SL ไปที่ราคาเข้า (Break-even)
📍 ราคาเข้า: {entry_price:.2f}
🛡️ SL ใหม่: {new_sl_price:.2f} (กันทุน)
💚 โพซิชัน: {current_position.upper()}"""
        
        send_telegram(message)
        logger.info(f"SL moved to breakeven for {current_position} position at {entry_price:.2f}. New SL: {new_sl_price:.2f}")
        
    except Exception as e:
        error_msg = f"Error moving SL to breakeven: {e}"
        logger.error(error_msg)
        send_telegram(f"⛔️ Error: ไม่สามารถเลื่อน SL ได้\nรายละเอียด: {error_msg}")

# === ตรวจสอบโพซิชันและจัดการ SL/TP ===
def monitor_position():
    global current_position, entry_price, sl_moved
    
    pos_info = get_current_position()

    if pos_info:
        # มีโพซิชันอยู่
        if current_position is None:
            # หาก bot เพิ่งเริ่มทำงานและพบโพซิชัน ให้โหลดสถานะ
            current_position = pos_info['side']
            entry_price = pos_info['entry_price']
            sl_moved = False # สมมติว่ายังไม่เลื่อนเมื่อเริ่มใหม่

        current_price = float(exchange.fetch_ticker(symbol)['last'])
        pnl_usdt = pos_info['unrealized_pnl'] # PnL เป็น USDT
        
        # เลื่อน SL เมื่อกำไรถึงเป้าหมาย (be_profit_trigger_usdt)
        if not sl_moved and pnl_usdt >= be_profit_trigger_usdt:
            move_sl_to_breakeven()
        
        logger.info(f"Position: {current_position}, Entry: {entry_price:.2f}, Current: {current_price:.2f}, PnL: {pnl_usdt:.2f} USDT")
        
    else:
        # ไม่มีโพซิชันอยู่ (หรือเพิ่งปิดไป)
        if current_position is not None:
            # โพซิชันถูกปิดแล้ว
            
            # ดึง PnL สุดท้ายจากสถานะก่อนหน้า (หากมี) หรือพยายามหาจาก history
            # สำหรับ OKX, การดึง PnL จากออเดอร์ที่ปิดไปแล้วอาจต้องใช้ fetch_orders
            # หรือ fetch_my_trades โดยดูที่ 'status' และ 'info' ของแต่ละรายการ

            # นี่คือตัวอย่างที่สมมติว่าเราสามารถรู้ PnL ได้ (คุณอาจต้องปรับให้เข้ากับข้อมูลจริงที่ดึงมาได้)
            # ในกรณีที่ไม่มีข้อมูล PnL ที่ชัดเจนจาก API ทันทีหลังปิด เราจะแจ้งเพียงว่าปิดแล้ว
            
            # สมมติว่าเรามีข้อมูล PnL จากการปิด (คุณต้องแทนที่ด้วยการดึงจริง)
            # ตัวอย่างเช่น:
            # closed_pnl = some_function_to_get_last_closed_order_pnl()
            closed_pnl = 0 # ค่าเริ่มต้น
            
            # การระบุว่าปิดด้วย TP, SL หรือบังคับปิด
            message_prefix = "✅ ปิดออเดอร์ด้วย"
            if closed_pnl > 0: # สมมติว่า PnL เป็นบวกคือ TP
                close_reason = "TP"
            elif closed_pnl < 0: # สมมติว่า PnL เป็นลบคือ SL
                close_reason = "SL"
            else:
                close_reason = "บังคับปิด" # หรืออื่นๆ เช่น liquidation
            
            message = f"{message_prefix} {close_reason}\n"
            if closed_pnl != 0:
                message += f"กำไร/ขาดทุน: {'+' if closed_pnl >= 0 else ''}{closed_pnl:.2f} USDT"
            else:
                message += f"โพซิชัน {current_position.upper()} ถูกปิดแล้ว" # กรณีไม่รู้ PnL ที่ชัดเจน

            send_telegram(message)
            logger.info(f"Position {current_position} closed.")
            
            current_position = None
            entry_price = None
            order_id = None
            sl_moved = False
        else:
            logger.info("No active position.")

# === ส่งรายงานประจำวัน ===
def daily_report():
    global last_daily_report_time
    
    current_time = datetime.now()
    
    if last_daily_report_time is None:
        last_daily_report_time = current_time # ตั้งค่าครั้งแรก
        return # ไม่ส่งทันที

    if (current_time - last_daily_report_time) >= timedelta(days=1):
        balance = get_balance()
        message = f"⏱ บอทยังทำงานปกติ ✅\nยอดคงเหลือ: {balance:.2f} USDT"
        send_telegram(message)
        last_daily_report_time = current_time # อัพเดตเวลาที่ส่งล่าสุด
        logger.info("Daily report sent.")

# === MAIN LOOP ===
def main():
    send_telegram("🤖 EMA Cross Bot เริ่มทำงาน")
    logger.info("Bot started.")
    
    # กำหนดเวลาสำหรับ daily report ครั้งแรก
    global last_daily_report_time
    last_daily_report_time = datetime.now()

    while True:
        try:
            # ส่งรายงานประจำวัน
            daily_report()

            # ตรวจสอบโพซิชันปัจจุบัน
            monitor_position()
            
            # ถ้าไม่มีโพซิชัน ให้เช็คสัญญาณ
            if not current_position:
                signal = check_ema_cross()
                
                if signal:
                    logger.info(f"EMA Cross Signal: {signal} detected. Attempting to open order.")
                    open_order_with_tpsl(signal)
                    time.sleep(5)  # รอสักครู่หลังเปิดออเดอร์เพื่อให้ API อัปเดตสถานะ
            
            time.sleep(15)  # เช็คทุก 15 วินาที
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            send_telegram("🛑 Bot หยุดทำงานโดยผู้ใช้")
            break
            
        except ccxt.DDoSProtection as e:
            error_msg = f"DDoS Protection: {e}"
            logger.error(error_msg)
            send_telegram(f"⛔️ Error: ถูกป้องกัน DDoS\nรายละเอียด: {error_msg} | Retry อีกครั้งใน 60 วินาที")
            time.sleep(60)
        except ccxt.ExchangeNotAvailable as e:
            error_msg = f"Exchange Not Available: {e}"
            logger.error(error_msg)
            send_telegram(f"⛔️ Error: Exchange ไม่พร้อมใช้งาน\nรายละเอียด: {error_msg} | Retry อีกครั้งใน 60 วินาที")
            time.sleep(60)
        except ccxt.RequestTimeout as e:
            error_msg = f"Request Timeout: {e}"
            logger.error(error_msg)
            send_telegram(f"⛔️ Error: API Request Timeout\nรายละเอียด: {error_msg} | Retry อีกครั้งใน 30 วินาที")
            time.sleep(30)
        except ccxt.NetworkError as e:
            error_msg = f"Network Error: {e}"
            logger.error(error_msg)
            send_telegram(f"⛔️ Error: การเชื่อมต่อ API มีปัญหา\nรายละเอียด: {error_msg} | Retry อีกครั้งใน 30 วินาที")
            time.sleep(30)
        except Exception as e:
            error_msg = f"❌ Main loop error: {e}"
            logger.error(error_msg)
            send_telegram(f"⛔️ Error: ข้อผิดพลาดใน Main Loop\nรายละเอียด: {error_msg} | Retry อีกครั้งใน 30 วินาที")
            time.sleep(30)

if __name__ == '__main__':
    main()

