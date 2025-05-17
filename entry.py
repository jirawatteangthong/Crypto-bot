import talib
import numpy as np

def check_ema_cross(candles):
    closes = [c[4] for c in candles]
    ema50 = talib.EMA(np.array(closes), timeperiod=50)
    ema200 = talib.EMA(np.array(closes), timeperiod=200)

    if ema50[-2] < ema200[-2] and ema50[-1] > ema200[-1]:
        return {'direction': 'long', 'price': closes[-1]}
    elif ema50[-2] > ema200[-2] and ema50[-1] < ema200[-1]:
        return {'direction': 'short', 'price': closes[-1]}
    return None