import talib
import numpy as np
from utils import fetch_candles

def detect_ema_crossover():
    candles = fetch_candles()
    closes = [c[4] for c in candles]

    ema50 = talib.EMA(np.array(closes), timeperiod=50)
    ema200 = talib.EMA(np.array(closes), timeperiod=200)

    if len(ema50) < 2 or len(ema200) < 2:
        return None

    if ema50[-2] < ema200[-2] and ema50[-1] > ema200[-1]:
        return 'golden'
    elif ema50[-2] > ema200[-2] and ema50[-1] < ema200[-1]:
        return 'death'
    return None
