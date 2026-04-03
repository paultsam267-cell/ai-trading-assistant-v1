import os
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

message = "AI Trading Assistant V1 test alert successful ✅"

if not BOT_TOKEN:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN")

if not CHAT_ID:
    raise ValueError("Missing TELEGRAM_CHAT_ID")

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
payload = {
    "chat_id": CHAT_ID,
    "text": message
}

response = requests.post(url, data=payload, timeout=30)
print("Status code:", response.status_code)
print("Response:", response.text)

response.raise_for_status()

print("Telegram test alert sent successfully")
