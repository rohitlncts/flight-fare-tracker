#!/usr/bin/env python3
"""Check flight fares and send updates via Telegram.

Supports dynamic multi-route and date range monitoring with run/pause controls.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from fast_flights import FlightQuery, Passengers, create_query, get_flights

import state_manager

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")

CHECK_NOW_TRIGGERS = {
    "/check",
    "/checknow",
    "check now",
    "check fares",
    "check",
}

PAUSE_TRIGGERS = {
    "/pause",
    "pause",
    "/stop",
    "stop",
}

RESUME_TRIGGERS = {
    "/resume",
    "resume",
    "/run",
    "run",
    "/start_monitor",
    "start monitor",
}

STATUS_TRIGGERS = {
    "/status",
    "status",
}

ROUTES_TRIGGERS = {
    "/routes",
    "routes",
    "/list",
    "list",
}


def fetch_cheapest(from_airport: str, to_airport: str, date: str) -> dict | None:
    """Fetch cheapest flight between two airports for a date using fast-flights."""
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


def send_telegram(token: str, chat_id: str | int, text: str) -> None:
    """Send text message to Telegram chat."""
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
    """Format integer as INR currency."""
    return f"₹{amount:,}"


def build_message(
    results: dict,
    previous: dict,
    routes: list[dict],
    hints: dict[str, str],
    *,
    is_paused: bool,
    triggered_by: str | None = None,
) -> str:
    """Build formatted Telegram fare update message."""
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    header = "✈️ Flight Fare Update"
    if triggered_by == "manual":
        header += " (manual check)"
    lines = [f"{header} ({now})", ""]

    if not routes:
        lines.append("⚠️ No routes configured for monitoring.")
        lines.append("Use /add <SRC> <DST> <START_DATE> [END_DATE] to add routes.")
        return "\n".join(lines)

    best_fare_overall = None
    best_route_overall = None

    for route in routes:
        src = route.get("from", "").strip().upper()
        dst = route.get("to", "").strip().upper()
        route_dates = state_manager.expand_route_dates(route)
        if not src or not dst or not route_dates:
            continue

        src_label = state_manager.get_airport_name(src)
        dst_label = state_manager.get_airport_name(dst)
        lines.append(f"📍 {src_label} → {dst_label}")

        for date_str in route_dates:
            key = f"{src}:{dst}:{date_str}"
            entry = results.get(key)
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                day_label = dt.strftime("%d %b (%a)")
            except ValueError:
                day_label = date_str

            if not entry:
                lines.append(f"  • {day_label}: no data")
                continue
            if "error" in entry:
                err_msg = entry["error"][:50]
                lines.append(f"  • {day_label}: error ({err_msg})")
                continue

            price = entry["price"]
            stop_label = "nonstop" if entry["stops"] == 0 else f"{entry['stops']} stop"
            line = f"  • {day_label}: {format_inr(price)} ({entry['airline']}, {stop_label})"

            # Compare with previous state
            old = previous.get(key, {}).get("price")
            if old is not None:
                if price < old:
                    line += f"  ↓ {format_inr(old - price)}"
                elif price > old:
                    line += f"  ↑ {format_inr(price - old)}"

            lines.append(line)

            if best_fare_overall is None or price < best_fare_overall["price"]:
                best_fare_overall = {
                    "price": price,
                    "date": day_label,
                    "airline": entry["airline"],
                }
                best_route_overall = f"{src} → {dst}"

        # Route discount hint if available
        hint = hints.get(dst) or hints.get(f"{src}:{dst}")
        if hint:
            lines.append(f"  💡 {hint}")
        lines.append("")

    if best_fare_overall and best_route_overall:
        lines.append(
            f"🏆 Cheapest: {best_route_overall} on {best_fare_overall['date']} at {format_inr(best_fare_overall['price'])} ({best_fare_overall['airline']})"
        )
        lines.append("")

    status_str = "⏸ Paused" if is_paused else "🟢 Active (auto-checks every 6h)"
    lines.append(f"Monitoring: {status_str}")
    lines.append("Commands: /pause | /resume | /add | /routes | /help")
    return "\n".join(lines)


def collect_fares(routes: list[dict]) -> dict[str, dict]:
    """Collect fares for all configured routes and their expanded dates."""
    results: dict[str, dict] = {}
    for route in routes:
        src = route.get("from", "").strip().upper()
        dst = route.get("to", "").strip().upper()
        dates = state_manager.expand_route_dates(route)
        for date_str in dates:
            key = f"{src}:{dst}:{date_str}"
            print(f"Checking {src} → {dst} on {date_str}...")
            results[key] = fetch_cheapest(src, dst, date_str)
            time.sleep(1.2)
    return results


def run_fare_check(
    token: str,
    chat_id: str | int,
    *,
    triggered_by: str | None = None,
    notify: bool = True,
) -> str:
    """Run fare check, save state, and optionally notify via Telegram."""
    config = state_manager.load_config()
    is_paused = bool(config.get("is_paused", False))

    # If scheduled run and monitoring is paused, skip check
    if is_paused and triggered_by != "manual":
        msg = "Flight fare monitoring is currently PAUSED. Skipping scheduled check."
        print(f"[check_fares] {msg}")
        return msg

    routes = config.get("routes", [])
    hints = config.get("discount_hints", {})
    previous = state_manager.load_state()

    results = collect_fares(routes)
    message = build_message(
        results,
        previous,
        routes,
        hints,
        is_paused=is_paused,
        triggered_by=triggered_by,
    )
    print("\n" + message + "\n")

    if notify:
        send_telegram(token, chat_id, message)

    # Persist state and timestamp
    merged_state = {**previous, **results}
    state_manager.save_state(merged_state)

    config["last_checked_at"] = datetime.now(IST).isoformat()
    state_manager.save_config(config)

    return message


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token:
        print("Missing TELEGRAM_BOT_TOKEN in .env or environment")
        return 1
    if not chat_id:
        print("Missing TELEGRAM_CHAT_ID in .env or environment")
        return 1

    run_fare_check(token, chat_id)
    print("Sent to Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
