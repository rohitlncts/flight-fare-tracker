#!/usr/bin/env python3
"""Poll Telegram for commands: run/pause monitoring, dynamic route config, on-demand checks."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any

import requests
from dotenv import load_dotenv

import state_manager
from check_fares import (
    CHECK_NOW_TRIGGERS,
    PAUSE_TRIGGERS,
    RESUME_TRIGGERS,
    ROUTES_TRIGGERS,
    STATUS_TRIGGERS,
    format_inr,
    run_fare_check,
    send_telegram,
)

load_dotenv()

HELP_TEXT = (
    "✈️ *Flight Fare Tracker Bot*\n\n"
    "*Monitoring Controls:*\n"
    "• `/pause` — Pause automatic scheduled fare checks\n"
    "• `/resume` or `/run` — Resume automatic fare checks\n"
    "• `/status` — View current status and active routes\n"
    "• `/check` — Check fares right now on-demand\n\n"
    "*Route Configuration:*\n"
    "• `/routes` or `/list` — List all configured routes\n"
    "• `/add <SRC> <DST> <START> [END]` — Monitor a route for a period\n"
    "   _Example 1:_ `/add BLR DEL 2026-11-10 2026-11-15`\n"
    "   _Example 2:_ `/add BOM GOI 2026-12-01`\n"
    "• `/remove <SRC> <DST>` or `/remove <#>` — Remove a route\n"
    "   _Example:_ `/remove BLR DEL` or `/remove 1`\n"
    "• `/reset` — Reset to default routes\n"
    "• `/clear` — Clear all monitored routes\n"
    "• `/help` — Show this guide\n\n"
    "_Scheduled checks run automatically every 6 hours when active._"
)


def normalize_command(text: str) -> str:
    return text.strip().lower()


def acknowledge_command(token: str, chat_id: str | int, command: str, action_desc: str) -> None:
    """Send immediate acknowledgement describing what command was received and what is about to execute."""
    display_cmd = command.strip()
    if len(display_cmd) > 50:
        display_cmd = display_cmd[:47] + "…"
    msg = f"🤖 *Processing:* `{display_cmd}`\n⏳ *Executing:* _{action_desc}_"
    try:
        send_telegram(token, chat_id, msg)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram_poll] Warning: Could not send command acknowledgement: {exc}")


def fetch_updates(token: str, offset: int) -> list[dict]:
    """Fetch unhandled updates from Telegram."""
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


def handle_status(config: dict[str, Any], last_state: dict[str, Any]) -> str:
    """Format bot status message."""
    is_paused = config.get("is_paused", False)
    routes = config.get("routes", [])
    last_check = config.get("last_checked_at")

    status_icon = "⏸ Paused" if is_paused else "🟢 Active (running every 6h)"
    lines = [
        "📊 *Flight Fare Bot Status*",
        f"• *Status:* {status_icon}",
        f"• *Monitored Routes:* {len(routes)}",
    ]

    if last_check:
        try:
            dt = datetime.fromisoformat(last_check)
            formatted = dt.strftime("%d %b %Y, %I:%M %p IST")
            lines.append(f"• *Last Check:* {formatted}")
        except Exception:
            lines.append(f"• *Last Check:* {last_check}")
    else:
        lines.append("• *Last Check:* Not checked yet")

    if routes:
        lines.append("\n*Active Routes:*")
        for idx, r in enumerate(routes, start=1):
            src = r.get("from", "").upper()
            dst = r.get("to", "").upper()
            dates = state_manager.expand_route_dates(r)
            s_label = state_manager.get_airport_name(src)
            d_label = state_manager.get_airport_name(dst)
            if dates:
                first_d = datetime.strptime(dates[0], "%Y-%m-%d").strftime("%d %b")
                last_d = datetime.strptime(dates[-1], "%Y-%m-%d").strftime("%d %b")
                date_str = f"{first_d} to {last_d} ({len(dates)} days)" if len(dates) > 1 else first_d
            else:
                date_str = "no dates"
            lines.append(f"{idx}. {s_label} → {d_label} ({date_str})")
    else:
        lines.append("\n⚠️ No routes currently configured.")
        lines.append("Use `/add <SRC> <DST> <START> [END]` to configure.")

    lines.append("\n_Commands: /pause | /resume | /routes | /check | /help_")
    return "\n".join(lines)


def handle_routes_list(config: dict[str, Any]) -> str:
    """Format list of monitored routes."""
    routes = config.get("routes", [])
    if not routes:
        return (
            "📋 *No routes configured.*\n\n"
            "Add routes using:\n"
            "`/add <SRC> <DST> <START_DATE> [END_DATE]`\n"
            "e.g. `/add BLR DEL 2026-11-10 2026-11-15`"
        )

    lines = [f"📋 *Monitored Routes ({len(routes)} configured):*\n"]
    for idx, r in enumerate(routes, start=1):
        src = r.get("from", "").upper()
        dst = r.get("to", "").upper()
        dates = state_manager.expand_route_dates(r)
        s_name = state_manager.get_airport_name(src)
        d_name = state_manager.get_airport_name(dst)

        if dates:
            d_start = datetime.strptime(dates[0], "%Y-%m-%d").strftime("%d %b %Y")
            d_end = datetime.strptime(dates[-1], "%Y-%m-%d").strftime("%d %b %Y")
            date_summary = f"{d_start} to {d_end} ({len(dates)} dates)" if len(dates) > 1 else d_start
        else:
            date_summary = "No valid dates"

        lines.append(f"*{idx}. {s_name} → {d_name}*")
        lines.append(f"   🗓 {date_summary}")

    lines.append("\n• Add: `/add <SRC> <DST> <START> [END]`")
    lines.append("• Remove: `/remove <number>` or `/remove <SRC> <DST>`")
    return "\n".join(lines)


AIRPORT_ALIASES: dict[str, str] = {
    "PNX": "PXN",  # Common typo for Purnia (PXN)
    "BGL": "BLR",  # Common typo for Bengaluru
    "PNA": "PNQ",  # Common typo for Pune
}


def parse_single_add_line(line: str) -> tuple[dict | None, str | None, str | None]:
    """Parse one line of add route input.

    Returns (route_dict, note, error_message).
    """
    cleaned = re.sub(r"^/add\b", "", line, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\b(to|from)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("->", " ").replace("—", " ")
    tokens = [t.strip() for t in cleaned.split() if t.strip()]

    if not tokens:
        return None, None, None

    if len(tokens) < 3:
        return None, None, f"Need at least SRC DST START_DATE in: `{line[:30]}`"

    src_raw = tokens[0].upper()
    dst_raw = tokens[1].upper()

    note_parts = []
    src = AIRPORT_ALIASES.get(src_raw, src_raw)
    dst = AIRPORT_ALIASES.get(dst_raw, dst_raw)
    if src != src_raw:
        note_parts.append(f"mapped {src_raw} → {src}")
    if dst != dst_raw:
        note_parts.append(f"mapped {dst_raw} → {dst}")

    if len(src) != 3 or not src.isalpha() or len(dst) != 3 or not dst.isalpha():
        return None, None, f"Invalid airport code in `{line[:30]}`. Use 3-letter IATA codes."

    if src == dst:
        return None, None, f"Source and destination must be different in `{line[:30]}`."

    start_date = state_manager.parse_date(tokens[2])
    if not start_date:
        return None, None, f"Invalid start date `{tokens[2]}` (use YYYY-MM-DD or DD-MM-YYYY)."

    end_date = start_date
    if len(tokens) >= 4:
        parsed_end = state_manager.parse_date(tokens[3])
        if not parsed_end:
            return None, None, f"Invalid end date `{tokens[3]}` (use YYYY-MM-DD or DD-MM-YYYY)."
        end_date = parsed_end

    if end_date < start_date:
        return None, None, f"End date cannot be before start date in `{line[:30]}`."

    max_days = 14
    days_count = (end_date - start_date).days + 1
    if days_count > max_days:
        end_date = start_date + timedelta(days=max_days - 1)
        note_parts.append(f"limited to {max_days} days")

    start_iso = start_date.strftime("%Y-%m-%d")
    end_iso = end_date.strftime("%Y-%m-%d")
    note = ", ".join(note_parts) if note_parts else None

    route_info = {
        "from": src,
        "to": dst,
        "start_date": start_iso,
        "end_date": end_iso,
        "_start_obj": start_date,
        "_end_obj": end_date,
    }
    return route_info, note, None


def handle_add_route(text: str, config: dict[str, Any]) -> str:
    """Parse and add one or multiple routes (supports multi-line input)."""
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]

    # If user sent just '/add' or 'add' alone
    if len(lines) == 1 and lines[0].lower() in {"/add", "add"}:
        return (
            "ℹ️ *How to add routes:*\n\n"
            "Format:\n"
            "`/add <SRC> <DST> <START_DATE> [END_DATE]`\n\n"
            "Examples:\n"
            "• `/add BLR DEL 2026-11-10 2026-11-15`\n"
            "• `/add BOM GOI 2026-12-01`\n"
            "• `/add PXN BLR 2027-01-05` (Purnia to BLR)\n\n"
            "💡 _You can also paste multiple /add lines at once!_"
        )

    routes = config.setdefault("routes", [])
    added_list: list[str] = []
    errors: list[str] = []

    for line in lines:
        if line.lower() in {"/add", "add"}:
            continue
        r_info, note, err = parse_single_add_line(line)
        if err:
            errors.append(err)
            continue
        if not r_info:
            continue

        src = r_info["from"]
        dst = r_info["to"]
        start_iso = r_info["start_date"]
        end_iso = r_info["end_date"]
        start_obj = r_info["_start_obj"]
        end_obj = r_info["_end_obj"]

        updated = False
        for existing in routes:
            if existing.get("from", "").upper() == src and existing.get("to", "").upper() == dst:
                existing["start_date"] = start_iso
                existing["end_date"] = end_iso
                existing.pop("dates", None)
                updated = True
                break

        if not updated:
            routes.append({
                "from": src,
                "to": dst,
                "start_date": start_iso,
                "end_date": end_iso,
            })

        s_name = state_manager.get_airport_name(src)
        d_name = state_manager.get_airport_name(dst)
        d_start_fmt = start_obj.strftime("%d %b %Y")
        d_end_fmt = end_obj.strftime("%d %b %Y")
        period_str = f"{d_start_fmt} to {d_end_fmt}" if start_obj != end_obj else d_start_fmt
        day_count = (end_obj - start_obj).days + 1

        item_text = f"📍 *{s_name} → {d_name}*\n   🗓 Period: {period_str} ({day_count} date{'s' if day_count > 1 else ''})"
        if note:
            item_text += f"\n   _{note}_"
        added_list.append(item_text)

    if not added_list and errors:
        return "⚠️ *Error adding route:*\n" + "\n".join(f"• {e}" for e in errors)

    state_manager.save_config(config)

    res_lines = [f"✅ *Successfully Saved {len(added_list)} Route(s)!*\n"]
    for i, itm in enumerate(added_list, 1):
        prefix = f"{i}. " if len(added_list) > 1 else ""
        res_lines.append(f"{prefix}{itm}")

    if errors:
        res_lines.append("\n⚠️ *Warnings/Errors:*")
        for e in errors:
            res_lines.append(f"• {e}")

    res_lines.append(f"\nTotal monitored routes: *{len(routes)}*.")
    res_lines.append("Send `/routes` to view all or `/check` to test fares now.")
    return "\n".join(res_lines)


def handle_remove_route(text: str, config: dict[str, Any]) -> str:
    """Parse and remove a route by index or by SRC DST."""
    cleaned = re.sub(r"^/remove\b", "", text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\b(to|from)\b", " ", cleaned, flags=re.IGNORECASE)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]

    routes = config.get("routes", [])
    if not routes:
        return "⚠️ No routes currently configured."

    if not tokens:
        return "⚠️ Specify route number or airports to remove.\nExample: `/remove 1` or `/remove BLR DEL`."

    # Check if index number
    if len(tokens) == 1 and tokens[0].isdigit():
        idx = int(tokens[0])
        if 1 <= idx <= len(routes):
            removed = routes.pop(idx - 1)
            state_manager.save_config(config)
            s = state_manager.get_airport_name(removed.get("from", ""))
            d = state_manager.get_airport_name(removed.get("to", ""))
            return f"🗑 Removed route #{idx}: *{s} → {d}*.\nRemaining routes: {len(routes)}."
        return f"⚠️ Invalid route number #{idx}. You have {len(routes)} routes. Use `/routes` to see list."

    # Otherwise match SRC DST
    if len(tokens) >= 2:
        src = tokens[0].upper()
        dst = tokens[1].upper()
        initial_len = len(routes)
        config["routes"] = [
            r for r in routes
            if not (r.get("from", "").upper() == src and r.get("to", "").upper() == dst)
        ]
        if len(config["routes"]) < initial_len:
            state_manager.save_config(config)
            return f"🗑 Removed route *{src} → {dst}*.\nRemaining routes: {len(config['routes'])}."
        return f"⚠️ No route matching *{src} → {dst}* found. Use `/routes` to see active routes."

    return "⚠️ Usage: `/remove <number>` or `/remove <SRC> <DST>`."


def process_updates(token: str, allowed_chat_id: str) -> int:
    """Process all incoming Telegram commands."""
    allowed = str(allowed_chat_id)
    offset = state_manager.load_offset()
    updates = fetch_updates(token, offset)
    checks_run = 0

    for update in updates:
        update_id = update["update_id"]
        offset = update_id + 1

        message = update.get("message") or update.get("edited_message")
        if not message:
            continue

        chat_id = str(message["chat"]["id"])
        text = message.get("text", "").strip()
        if not text:
            continue

        if chat_id != allowed:
            send_telegram(
                token,
                chat_id,
                "This bot is private. Contact the owner to authorize your chat ID.",
            )
            continue

        norm = normalize_command(text)
        cmd = norm.split()[0] if norm else ""

        # Load fresh config and state
        config = state_manager.load_config()
        last_state = state_manager.load_state()

        # 1. Help / Start
        if norm in {"/help", "help", "/start", "start"}:
            acknowledge_command(token, chat_id, text, "Loading command list and usage guide…")
            send_telegram(token, chat_id, HELP_TEXT)
            continue

        # 2. Status
        if norm in STATUS_TRIGGERS or cmd == "/status":
            acknowledge_command(token, chat_id, text, "Querying bot monitoring state, active routes, and last check timestamp…")
            reply = handle_status(config, last_state)
            send_telegram(token, chat_id, reply)
            continue

        # 3. Pause
        if norm in PAUSE_TRIGGERS or cmd in {"/pause", "/stop"}:
            acknowledge_command(token, chat_id, text, "Suspending automatic scheduled 6-hour fare checks…")
            config["is_paused"] = True
            state_manager.save_config(config)
            send_telegram(
                token,
                chat_id,
                "⏸ *Flight Fare Monitoring Paused*\n\n"
                "Automatic 6-hour checks are suspended.\n"
                "• Send `/resume` to turn monitoring back on.\n"
                "• Send `/check` to run an on-demand check anytime.",
            )
            continue

        # 4. Resume / Run
        if norm in RESUME_TRIGGERS or cmd in {"/resume", "/run", "/start_monitor"}:
            acknowledge_command(token, chat_id, text, "Reactivating automated 6-hour fare checks…")
            config["is_paused"] = False
            state_manager.save_config(config)
            route_count = len(config.get("routes", []))
            send_telegram(
                token,
                chat_id,
                f"▶️ *Flight Fare Monitoring Resumed*\n\n"
                f"Automatic checks are active (running every 6 hours).\n"
                f"Currently monitoring *{route_count} route(s)*.\n\n"
                "Send `/routes` to view or `/check` to test now.",
            )
            continue

        # 5. List Routes
        if norm in ROUTES_TRIGGERS or cmd in {"/routes", "/list"}:
            acknowledge_command(token, chat_id, text, "Retrieving all active monitored routes and date periods…")
            reply = handle_routes_list(config)
            send_telegram(token, chat_id, reply)
            continue

        # 6. Add Route
        if cmd == "/add" or norm.startswith("add "):
            acknowledge_command(token, chat_id, text, "Validating route details and updating flight monitoring configuration…")
            reply = handle_add_route(text, config)
            send_telegram(token, chat_id, reply)
            continue

        # 7. Remove Route
        if cmd == "/remove" or norm.startswith("remove "):
            acknowledge_command(token, chat_id, text, "Locating and removing requested route from monitoring list…")
            reply = handle_remove_route(text, config)
            send_telegram(token, chat_id, reply)
            continue

        # 8. Reset to default routes
        if norm in {"/reset", "reset"}:
            acknowledge_command(token, chat_id, text, "Resetting flight routes back to default configuration (Bagdogra, Darbhanga, Purnia)…")
            config["routes"] = [
                {
                    "from": "BLR",
                    "to": "IXB",
                    "start_date": "2026-11-10",
                    "end_date": "2026-11-13",
                },
                {
                    "from": "BLR",
                    "to": "DBR",
                    "start_date": "2026-11-10",
                    "end_date": "2026-11-13",
                },
                {
                    "from": "BLR",
                    "to": "PXN",
                    "start_date": "2026-11-10",
                    "end_date": "2026-11-13",
                },
            ]
            state_manager.save_config(config)
            send_telegram(
                token,
                chat_id,
                "🔄 *Reset to Default Routes*\n\n"
                "Restored Bagdogra (IXB), Darbhanga (DBR), and Purnia (PXN) for 10–13 Nov 2026.",
            )
            continue

        # 9. Clear all routes
        if norm in {"/clear", "clear"}:
            acknowledge_command(token, chat_id, text, "Clearing all monitored flight routes from configuration…")
            config["routes"] = []
            state_manager.save_config(config)
            send_telegram(
                token,
                chat_id,
                "🗑 *All Monitored Routes Cleared*\n\n"
                "No routes are currently tracked. Add new routes with `/add <SRC> <DST> <START> [END]`.",
            )
            continue

        # 10. Check Now (Manual)
        if norm in CHECK_NOW_TRIGGERS or cmd == "/check" or cmd == "/checknow":
            acknowledge_command(token, chat_id, text, "Triggering immediate on-demand fare check across all configured routes…")
            run_fare_check(token, chat_id, triggered_by="manual")
            checks_run += 1
            continue

        # Fallback for unrecognized command
        send_telegram(
            token,
            chat_id,
            f"❓ Unrecognized command: `{text[:40]}`\n\nSend `/help` for available commands.",
        )

    if updates:
        state_manager.save_offset(offset)

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
