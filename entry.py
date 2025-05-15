from utils import fetch_current_price

def check_fibo_entry(fibo):
    price = fetch_current_price()
    if fibo['direction'] == 'long' and price <= fibo['entry']:
        return fibo
    elif fibo['direction'] == 'short' and price >= fibo['entry']:
        return fibo
    return None
