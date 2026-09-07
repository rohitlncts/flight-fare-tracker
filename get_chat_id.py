#!/usr/bin/env python3
"""Fetch your Telegram chat ID after you message your bot once."""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("Set TELEGRAM_BOT_TOKEN in .env first.")
    sys.exit(1)

resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=30)
resp.raise_for_status()
data = resp.json()

if not data.get("ok"):
    print("Telegram API error:", data)
    sys.exit(1)

updates = data.get("result", [])
if not updates:
    print("No messages yet.")
    print("1. Open Telegram and find your bot")
    print("2. Send it any message (e.g. 'hi')")
    print("3. Run this script again")
    sys.exit(1)

seen = {}
for item in updates:
    msg = item.get("message") or item.get("edited_message")
    if not msg:
        continue
    chat = msg["chat"]
    chat_id = chat["id"]
    name = chat.get("first_name") or chat.get("title") or "unknown"
    seen[chat_id] = name

print("Found chat ID(s). Add one to .env as TELEGRAM_CHAT_ID:\n")
for chat_id, name in seen.items():
    print(f"  TELEGRAM_CHAT_ID={chat_id}  ({name})")
