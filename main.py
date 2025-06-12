import ccxt
import time
import requests
from datetime import datetime, timedelta
import logging

# === ตั้งค่า Logging ===

logging.basicConfig(level=logging.INFO, format=’%(asctime)s - %(levelname)s - %(message)s’)
logger = logging.getLogger(**name**)

# === ตั้งค่า ===

api_key = ‘8f528085-448c-4480-a2b0-d7f72afb38ad’       # ใส่ API KEY
secret = ‘05A665CEAF8B2161483DF63CB10085D2'   # ใส่ SECRET KEY ของคุณ
password = ‘Jirawat1-’ # ใส่ Password (ถ้ามี) ของคุณ

symbol = ‘BTC/USDT:USDT’
timeframe = ‘15m’

# order_size จะถูกคำนวณจาก 80% ของพอร์ต

leverage = 25  # *** เปลี่ยน Leverage เป็น 25x ***
tp_value = 500  # TP เป็นค่าคงที่ (USDT)
sl_value = 990  # SL เป็นค่าคงที่ (USDT)
be_profit_trigger_usdt = 350 # กำไร 350 USDT จะเลื่อน SL
be_sl_offset_usdt = 100 # เลื่อน SL ให้ต่ำกว่าราคาเข้าเล็กน้อย 100 USDT (กันค่าธรรมเนียม)

telegram_token = ‘7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY’
telegram_chat_id = ‘8104629569’

# === สถานะการเทรด ===

current_position = None  # None, ‘long’, ‘short’
entry_price = None
order_id = None
sl_moved = False
last_ema_state = None  # เก็บสถานะ EMA ก่อนหน้า (‘above’, ‘below’)
last_cross_signal = None # เก็บสัญญาณ cross ล่าสุดที่ตรวจพบ
last_daily_report_time = None # เวลาที่ส่งรายงานประจำวันล่าสุด

# === Exchange Setup ===

exchange = ccxt.okx({
‘apiKey’: api_key,
‘secret’: secret,
‘password’: password,
‘enableRateLimit’: True,
‘options’: {‘defaultType’: ‘swap’}
})
exchange.set_sandbox_mode(False) # ตั้งเป็น False สำหรับบัญชีจริง

# === Telegram ===

def send_telegram(msg):
try:
requests.get(
f’https://api.telegram.org/bot{telegram_token}/sendMessage’,
params={‘chat_id’: telegram_chat_id, ‘text’: msg, ‘parse_mode’: ‘HTML’},
timeout=10
)
logger.info(f”Telegram: {msg}”)
except Exception as e:
logger.error(f”Telegram error: {e}. Message: {msg}”)

# === คำนวณ EMA ===

def calculate_ema(prices, period):
if len(prices) < period:
return None

```
# เริ่มด้วย SMA
sma = sum(prices[:period]) / period
ema = sma
multiplier = 2 / (period + 1)

# คำนวณ EMA
for price in prices[period:]:
    ema = (price * multiplier) + (ema * (1 - multiplier))

return ema
```

# === ตรวจสอบการตัดกันของ EMA ===

def check_ema_cross():
global last_ema_state, last_cross_signal

```
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
    send_telegram(f"⛔️ <b>ข้อผิดพลาด EMA</b>\n📊 การตรวจสอบ EMA Cross ล้มเหลว\n🔍 รายละเอียด: {str(e)[:100]}...")
    return None
```

# === ตรวจสอบโพซิชันปัจจุบัน ===

def get_current_position():
try:
positions = exchange.fetch_positions([symbol])
for pos in positions:
if float(pos[‘contracts’]) != 0: # ตรวจสอบ contracts ไม่เท่ากับ 0
return {
‘side’: ‘long’ if float(pos[‘contracts’]) > 0 else ‘short’,
‘size’: abs(float(pos[‘contracts’])),
‘entry_price’: float(pos[‘entryPrice’]),
‘unrealized_pnl’: float(pos[‘unrealizedPnl’]),
‘liquidation_price’: float(pos[‘liquidationPrice’]) if pos.get(‘liquidationPrice’) else None
}
return None
except ccxt.NetworkError as e:
error_msg = f”Network Error: {e}”
logger.error(error_msg)
send_telegram(f”🌐 <b>ปัญหาเครือข่าย</b>\n⚡️ การเชื่อมต่อ API มีปัญหา\n⏱ ลองใหม่ใน 30 วินาที\n🔍 {str(e)[:80]}…”)
time.sleep(30)
return None
except Exception as e:
error_msg = f”Error fetching position: {e}”
logger.error(error_msg)
send_telegram(f”⛔️ <b>ข้อผิดพลาดโพซิชัน</b>\n📊 ไม่สามารถดึงข้อมูลโพซิชันได้\n🔍 {str(e)[:100]}…”)
return None

# === ดึงยอดคงเหลือ USDT ===

def get_balance():
try:
balance = exchange.fetch_balance()
if ‘USDT’ in balance[‘total’]:
return balance[‘total’][‘USDT’]
return 0
except ccxt.NetworkError as e:
error_msg = f”Network Error: {e}”
logger.error(error_msg)
send_telegram(f”🌐 <b>ปัญหาเครือข่าย</b>\n💰 ไม่สามารถดึงยอดคงเหลือได้\n⏱ ลองใหม่ใน 30 วินาที”)
time.sleep(30)
return 0
except Exception as e:
error_msg = f”Error fetching balance: {e}”
logger.error(error_msg)
send_telegram(f”⛔️ <b>ข้อผิดพลาดยอดเงิน</b>\n💰 ไม่สามารถดึงยอดคงเหลือได้\n🔍 {str(e)[:100]}…”)
return 0

# === เปิดออเดอร์พร้อม TP/SL ===

def open_order_with_tpsl(direction):
global current_position, entry_price, order_id, sl_moved

```
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
        send_telegram("⚠️ <b>ยอดเงินไม่เพียงพอ</b>\n💰 ไม่มียอดคงเหลือ USDT เพียงพอสำหรับการเปิดออเดอร์")
        logger.warning("No USDT balance to open order.")
        return False

    # คำนวณขนาดออเดอร์ (BTC) จาก 80% ของพอร์ต Leverage และราคาปัจจุบัน
    calculated_order_size_usdt = available_balance * 0.80 * leverage
    calculated_order_size_btc = calculated_order_size_usdt / current_price

    # ตรวจสอบขั้นต่ำของขนาดออเดอร์
    market = exchange.market(symbol)
    min_amount = market['limits']['amount']['min'] if 'amount' in market['limits'] else 0.00001
    min_notional = market['limits']['cost']['min'] if 'cost' in market['limits'] and market['limits']['cost']['min'] is not None else 10
    
    min_notional_btc_equivalent = min_notional / current_price
    actual_min_order_btc = max(min_amount, min_notional_btc_equivalent)
    
    if calculated_order_size_btc < actual_min_order_btc:
        send_telegram(f"⚠️ <b>ขนาดออเดอร์ต่ำเกินไป</b>\n📊 คำนวณได้: {calculated_order_size_btc:.5f} BTC\n📏 ขั้นต่ำ: {actual_min_order_btc:.5f} BTC\n💵 ต้องการ: {min_notional:.2f} USDT")
        logger.warning(f"Calculated order size {calculated_order_size_btc:.5f} BTC is below minimum {actual_min_order_btc:.5f} BTC")
        return False

    order_size_btc = round(calculated_order_size_btc, 5)

    # คำนวณ TP และ SL
    if direction == 'long':
        tp_price = current_price + tp_value
        sl_price = current_price - sl_value
        side = 'buy'
    else: # direction == 'short'
        tp_price = current_price - tp_value
        sl_price = current_price + sl_value
        side = 'sell'
    
    # พารามิเตอร์สำหรับ OKX
    params = {
        'tdMode': 'cross',
        'ordType': 'market',
        'lever': str(leverage),
        'reduceOnly': False,
        'tpTriggerPx': str(round(tp_price, 2)),
        'tpOrdPx': '-1',
        'tpTriggerPxType': 'last',
        'slTriggerPx': str(round(sl_price, 2)), 
        'slOrdPx': '-1',
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
    
    # ส่งแจ้งเตือนที่สวยงาม
    direction_emoji = "🟢" if direction == "long" else "🔴"
    direction_icon = "📈" if direction == "long" else "📉"
    
    message = f"""{direction_emoji} <b>เข้าซื้อ {direction.upper()}</b> {direction_icon}
```

💰 <b>ราคาเข้า:</b> ${entry_price:,.2f}
🎯 <b>Take Profit:</b> ${tp_price:,.2f} (+${tp_value:,.0f})
🛡️ <b>Stop Loss:</b> ${sl_price:,.2f} (-${sl_value:,.0f})
📊 <b>ขนาด:</b> {order_size_btc:.5f} BTC
💵 <b>มูลค่า:</b> ${calculated_order_size_usdt:,.2f} USDT
⚡️ <b>เลเวอเรจ:</b> {leverage}x
🆔 <b>Order ID:</b> {order_id}

🚀 <b>สถานะ:</b> เข้าสู่ตลาดสำเร็จ!</message>

```
    send_telegram(message)
    logger.info(f"Order opened successfully: {direction} at {entry_price:.2f}")
    return True
    
except ccxt.NetworkError as e:
    error_msg = f"Network Error (Order Fail): {e}"
    logger.error(error_msg)
    send_telegram(f"🌐 <b>ปัญหาเครือข่าย (สั่งซื้อ)</b>\n⚡️ ไม่สามารถเชื่อมต่อ OKX API\n⏱ Retry ใน 30 วินาที\n🔍 {str(e)[:80]}...")
    time.sleep(30)
    return False
except ccxt.ExchangeError as e:
    error_msg = f"Exchange Error (Order Fail): {e}"
    logger.error(error_msg)
    send_telegram(f"⛔️ <b>คำสั่งซื้อล้มเหลว</b>\n🏦 ข้อผิดพลาดจาก Exchange\n🔍 {str(e)[:100]}...")
    return False
except Exception as e:
    error_msg = f"❌ เปิดออเดอร์ล้มเหลว: {e}"
    send_telegram(f"⛔️ <b>เปิดออเดอร์ล้มเหลว</b>\n🚫 เกิดข้อผิดพลาดไม่คาดคิด\n🔍 {str(e)[:100]}...")
    logger.error(error_msg)
    return False
```

# === เลื่อน SL เป็นกันทุน ===

def move_sl_to_breakeven():
global sl_moved

```
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
    
    message = f"""🔄 <b>เลื่อน Stop Loss</b> 🛡️
```

✅ ราคาวิ่งได้กำไร +${be_profit_trigger_usdt:,.0f} USDT แล้ว!
🎯 เลื่อน SL ไปที่ Break-even เพื่อป้องกันขาดทุน

📍 <b>ราคาเข้า:</b> ${entry_price:,.2f}
🛡️ <b>SL ใหม่:</b> ${new_sl_price:,.2f}
💚 <b>โพซิชัน:</b> {current_position.upper()}
🔒 <b>สถานะ:</b> ปลอดภัยแล้ว!</message>

```
    send_telegram(message)
    logger.info(f"SL moved to breakeven for {current_position} position at {entry_price:.2f}. New SL: {new_sl_price:.2f}")
    
except Exception as e:
    error_msg = f"Error moving SL to breakeven: {e}"
    logger.error(error_msg)
    send_telegram(f"⛔️ <b>เลื่อน SL ล้มเหลว</b>\n🛡️ ไม่สามารถเลื่อน Stop Loss ได้\n🔍 {str(e)[:100]}...")
```

# === ตรวจสอบโพซิชันและจัดการ SL/TP ===

def monitor_position():
global current_position, entry_price, sl_moved

```
pos_info = get_current_position()

if pos_info:
    # มีโพซิชันอยู่
    if current_position is None:
        # หาก bot เพิ่งเริ่มทำงานและพบโพซิชัน ให้โหลดสถานะ
        current_position = pos_info['side']
        entry_price = pos_info['entry_price']
        sl_moved = False

    current_price = float(exchange.fetch_ticker(symbol)['last'])
    pnl_usdt = pos_info['unrealized_pnl']
    
    # เลื่อน SL เมื่อกำไรถึงเป้าหมาย
    if not sl_moved and pnl_usdt >= be_profit_trigger_usdt:
        move_sl_to_breakeven()
    
    logger.info(f"Position: {current_position}, Entry: {entry_price:.2f}, Current: {current_price:.2f}, PnL: {pnl_usdt:.2f} USDT")
    
else:
    # ไม่มีโพซิชันอยู่ (หรือเพิ่งปิดไป)
    if current_position is not None:
        # โพซิชันถูกปิดแล้ว
        
        # ลองดึงข้อมูล PnL จาก recent orders
        try:
            orders = exchange.fetch_orders(symbol, limit=10)
            recent_closed_order = None
            for order in orders:
                if order['status'] == 'closed' and order['id'] == order_id:
                    recent_closed_order = order
                    break
            
            closed_pnl = 0
            if recent_closed_order and 'info' in recent_closed_order:
                # พยายามดึง PnL จาก order info (อาจแตกต่างกันไปตาม exchange)
                closed_pnl = float(recent_closed_order['info'].get('pnl', 0))
        except:
            closed_pnl = 0
        
        # กำหนดเหตุผลการปิด
        if closed_pnl > 0:
            close_reason = "Take Profit 🎯"
            close_emoji = "✅"
            result_color = "🟢"
        elif closed_pnl < 0:
            close_reason = "Stop Loss 🛡️"
            close_emoji = "❌"
            result_color = "🔴"
        else:
            close_reason = "ปิดอัตโนมัติ 🔄"
            close_emoji = "⚠️"
            result_color = "🟡"

        message = f"""{close_emoji} <b>ปิดโพซิชัน</b> {result_color}
```

🏁 <b>ปิดด้วย:</b> {close_reason}
📊 <b>โพซิชัน:</b> {current_position.upper()}
📍 <b>ราคาเข้า:</b> ${entry_price:,.2f}”””

```
        if closed_pnl != 0:
            pnl_sign = "+" if closed_pnl >= 0 else ""
            message += f"\n💰 <b>ผลตอบแทน:</b> {pnl_sign}${closed_pnl:,.2f} USDT"
        
        message += f"\n🕐 <b>เวลา:</b> {datetime.now().strftime('%H:%M:%S')}"

        send_telegram(message)
        logger.info(f"Position {current_position} closed.")
        
        current_position = None
        entry_price = None
        order_id = None
        sl_moved = False
    else:
        logger.info("No active position.")
```

# === ส่งรายงานประจำวัน ===

def daily_report():
global last_daily_report_time

```
current_time = datetime.now()

if last_daily_report_time is None:
    last_daily_report_time = current_time
    return

if (current_time - last_daily_report_time) >= timedelta(days=1):
    balance = get_balance()
    
    message = f"""📊 <b>รายงานประจำวัน</b> 🗓️
```

✅ <b>สถานะบอท:</b> ทำงานปกติ
💰 <b>ยอดคงเหลือ:</b> ${balance:,.2f} USDT
📈 <b>ตลาด:</b> {symbol}
🕐 <b>เวลา:</b> {current_time.strftime(’%d/%m/%Y %H:%M’)}
🤖 <b>Version:</b> EMA Cross Bot v2.0

🔍 <b>การตรวจสอบ:</b> EMA 50/200 Cross
⚡️ <b>เลเวอเรจ:</b> {leverage}x
🎯 <b>TP:</b> ${tp_value} | 🛡️ <b>SL:</b> ${sl_value}</message>

```
    send_telegram(message)
    last_daily_report_time = current_time
    logger.info("Daily report sent.")
```

# === MAIN LOOP ===

def main():
welcome_message = f””“🤖 <b>EMA Cross Trading Bot เริ่มทำงาน</b> 🚀

📊 <b>ตั้งค่า:</b>
• 💹 คู่เทรด: {symbol}
• ⏱ กรอบเวลา: {timeframe}
• ⚡️ เลเวอเรจ: {leverage}x
• 🎯 Take Profit: ${tp_value}
• 🛡️ Stop Loss: ${sl_value}
• 💰 ขนาดออเดอร์: 80% ของพอร์ต

🔍 <b>กลยุทธ์:</b> EMA 50/200 Cross
✅ <b>สถานะ:</b> พร้อมทำงาน!

🌟 ขอให้มีกำไรดีครับ! 🌟</message>

```
send_telegram(welcome_message)
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
                signal_emoji = "🟢📈" if signal == "long" else "🔴📉"
                send_telegram(f"{signal_emoji} <b>ตรวจพบสัญญาณ {signal.upper()}!</b>\n🔄 กำลังเปิดออเดอร์...")
                logger.info(f"EMA Cross Signal: {signal} detected. Attempting to open order.")
                open_order_with_tpsl(signal)
                time.sleep(5)
        
        time.sleep(15)  # เช็คทุก 15 วินาที
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        send_telegram("🛑 <b>Bot หยุดทำงาน</b>\n👤 หยุดโดยผู้ใช้\n🙏 ขอบคุณที่ใช้บริการ!")
        break
        
    except ccxt.DDoSProtection as e:
        error_msg = f"DDoS Protection: {e}"
        logger.error(error_msg)
        send_telegram(f"🛡️ <b>DDoS Protection</b>\n⚡️ ถูกป้องกัน DDoS จาก Exchange\n⏱ Retry ใน 60 วินาที")
        time.sleep(60)
    except ccxt.ExchangeNotAvailable as e:
        error_msg = f"Exchange Not Available: {e}"
        logger.error(error_msg)
        send_telegram(f"🏦 <b>Exchange ไม่พร้อม</b>\n❌ Exchange ไม่พร้อมใช้งาน\n⏱ Retry ใน 60 วินาที")
        time.sleep(60)
    except ccxt.RequestTimeout as e:
        error_msg = f"Request Timeout: {e}"
        logger.error(error_msg)
        send_telegram(f"⏱ <b>Request Timeout</b>\
```
