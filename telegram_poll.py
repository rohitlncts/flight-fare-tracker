#!/usr/bin/env python3
"""Poll Telegram for 'check now' commands and trigger fare lookups."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from check_fares import CHECK_NOW_TRIGGERS, run_fare_check, send_telegram

load_dotenv()

OFFSET_FILE = Path(__file__).with_name("bot_offset.json")

HELP_TEXT = (
    "✈️ BLR Flight Fare Bot\n\n"
    "Commands:\n"
    "  /check or check now — run a fare check immediately\n"
    "  /help — show this message\n\n"
    "Monitored routes: Bagdogra, Darbhanga, Purnia\n"
    "Dates: 10–13 Nov 2026\n\n"
    "Scheduled checks also run every 6 hours automatically."
)


def load_offset() -> int:
    if OFFSET_FILE.exists():
        data = json.loads(OFFSET_FILE.read_text())
        return int(data.get("offset", 0))
    return 0


def save_offset(offset: int) -> None:
    OFFSET_FILE.write_text(json.dumps({"offset": offset}, indent=2))


def normalize_command(text: str) -> str:
    return text.strip().lower()


def is_check_command(text: str) -> bool:
    normalized = normalize_command(text)
    return normalized in CHECK_NOW_TRIGGERS or normalized.startswith("/check")


def is_help_command(text: str) -> bool:
    normalized = normalize_command(text)
    return normalized in {"/help", "help", "/start", "start"}


def fetch_updates(token: str, offset: int) -> list[dict]:
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": offset, "timeout": 0},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {body}")
    return body.get("result", [])


def process_updates(token: str, allowed_chat_id: str) -> int:
    allowed = str(allowed_chat_id)
    offset = load_offset()
    updates = fetch_updates(token, offset)
    checks_run = 0

    for update in updates:
        update_id = update["update_id"]
        offset = update_id + 1

        message = update.get("message") or update.get("edited_message")
        if not message:
            continue

        chat_id = str(message["chat"]["id"])
        text = message.get("text", "")

        if chat_id != allowed:
            send_telegram(
                token,
                chat_id,
                "This bot is private. Ask the owner to authorize your chat ID.",
            )
            continue

        if is_help_command(text):
            send_telegram(token, chat_id, HELP_TEXT)
            continue

        if is_check_command(text):
            send_telegram(token, chat_id, "🔍 Checking fares now…")
            run_fare_check(token, chat_id, triggered_by="manual")
            checks_run += 1

    if updates:
        save_offset(offset)

    return checks_run


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return 1

    count = process_updates(token, chat_id)
    print(f"Processed updates; fare checks triggered: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
