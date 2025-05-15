from utils import fetch_ohlcv

def detect_bos_and_fibo():
    candles = fetch_ohlcv('5m', limit=100)
    high = max(c['high'] for c in candles[-20:])
    low = min(c['low'] for c in candles[-20:])

    if candles[-1]['close'] > high:
        direction = 'long'
    elif candles[-1]['close'] < low:
        direction = 'short'
    else:
        return None

    swing_high = high
    swing_low = low

    if direction == 'long':
        entry = swing_low + 0.62 * (swing_high - swing_low)
        sl = swing_low - 0.1 * (swing_high - swing_low)
        tp = entry + (entry - sl)
    else:
        entry = swing_high - 0.62 * (swing_high - swing_low)
        sl = swing_high + 0.1 * (swing_high - swing_low)
        tp = entry - (sl - entry)

    return {
        'direction': direction,
        'entry': entry,
        'tp': tp,
        'sl': sl
    }
