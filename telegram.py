import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

def send_message(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    requests.post(url, json=payload)

def notify(msg):
    send_message(msg)
