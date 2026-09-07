#!/usr/bin/env python3
"""Check BLR flight fares and send updates via Telegram."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from fast_flights import FlightQuery, Passengers, create_query, get_flights

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
STATE_FILE = Path(__file__).with_name("last_state.json")

ROUTES = {
    "IXB": "Bagdogra",
    "DBR": "Darbhanga",
    "PXN": "Purnia",
}

DATES = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]

DISCOUNT_HINTS = {
    "IXB": "Cleartrip + Axis AXISCC (~₹1,090 off) or EMTFIRST on EaseMyTrip",
    "DBR": "Cleartrip + Axis AXISCC (~₹1,500 off)",
    "PXN": "Book direct on IndiGo/Star Air; check EMTFIRST on EaseMyTrip",
}

CHECK_NOW_TRIGGERS = {
    "/check",
    "/checknow",
    "check now",
    "check fares",
    "check",
}


def fetch_cheapest(from_airport: str, to_airport: str, date: str) -> dict | None:
    query = create_query(
        flights=[
            FlightQuery(date=date, from_airport=from_airport, to_airport=to_airport)
        ],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        currency="INR",
    )
    try:
        results = get_flights(query)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}

    if not results:
        return None

    best = min(results, key=lambda f: f.price)
    airline = ", ".join(best.airlines) if best.airlines else "Unknown"
    stops = len(best.flights) - 1 if best.flights else 0
    return {
        "price": best.price,
        "airline": airline,
        "stops": stops,
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_telegram(token: str, chat_id: str | int, text: str) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram error: {body}")


def format_inr(amount: int) -> str:
    return f"₹{amount:,}"


def build_message(results: dict, previous: dict, triggered_by: str | None = None) -> str:
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    header = "✈️ BLR fare update"
    if triggered_by == "manual":
        header += " (check now)"
    lines = [f"{header} ({now})", ""]

    best_key = None
    best_price = None

    for code, name in ROUTES.items():
        lines.append(f"📍 {name} ({code})")
        for date in DATES:
            key = f"{code}:{date}"
            entry = results.get(key)
            day = datetime.strptime(date, "%Y-%m-%d").strftime("%d %b")

            if not entry:
                lines.append(f"  {day}: no data")
                continue
            if "error" in entry:
                lines.append(f"  {day}: error ({entry['error'][:60]})")
                continue

            price = entry["price"]
            stop_label = "nonstop" if entry["stops"] == 0 else f"{entry['stops']} stop"
            line = f"  {day}: {format_inr(price)} ({entry['airline']}, {stop_label})"

            old = previous.get(key, {}).get("price")
            if old is not None and price < old:
                line += f"  ↓ {format_inr(old - price)}"
            elif old is not None and price > old:
                line += f"  ↑ {format_inr(price - old)}"

            lines.append(line)

            if best_price is None or price < best_price:
                best_price = price
                best_key = (code, date, entry)

        lines.append(f"  💡 {DISCOUNT_HINTS[code]}")
        lines.append("")

    if best_key:
        code, date, entry = best_key
        day = datetime.strptime(date, "%Y-%m-%d").strftime("%d %b")
        lines.append(
            f"🏆 Best: {ROUTES[code]} on {day} at {format_inr(entry['price'])}"
        )

    lines.append("")
    lines.append("Purnia airport code: PXN (IATA).")
    lines.append("Send /check or 'check now' anytime for a fresh lookup.")
    return "\n".join(lines)


def collect_fares() -> dict[str, dict]:
    results: dict[str, dict] = {}
    for code in ROUTES:
        for date in DATES:
            key = f"{code}:{date}"
            print(f"Checking BLR → {code} on {date}...")
            results[key] = fetch_cheapest("BLR", code, date)
            time.sleep(1.5)
    return results


def run_fare_check(
    token: str,
    chat_id: str | int,
    *,
    triggered_by: str | None = None,
    notify: bool = True,
) -> str:
    previous = load_state()
    results = collect_fares()
    message = build_message(results, previous, triggered_by=triggered_by)
    print("\n" + message + "\n")
    if notify:
        send_telegram(token, chat_id, message)
    save_state(results)
    return message


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token:
        print("Missing TELEGRAM_BOT_TOKEN in .env")
        return 1
    if not chat_id:
        print("Missing TELEGRAM_CHAT_ID in .env")
        print("Message your bot on Telegram, then run: python get_chat_id.py")
        return 1

    run_fare_check(token, chat_id)
    print("Sent to Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
