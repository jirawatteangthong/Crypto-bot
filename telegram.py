import requests

BOT_TOKEN = '7752789264:AAF-0zdgHsSSYe7PS17ePYThOFP3k7AjxBY'
CHAT_ID = '8104629569'

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message
    }
    try:
        requests.get(url, params=payload)
    except Exception as e:
        print(f"[ERROR TELEGRAM] {e}")
