# ✈️ Flight Fare Tracker

Automated flight ticket price monitoring bot powered by GitHub Actions and Telegram.
Tracks airfares for multiple routes and date periods, notifies on price drops, and supports remote control (run/pause, dynamic routes) directly via Telegram.

---

## 📱 Telegram Commands

Send commands to your Telegram bot ([@NR_Flighttrackerbot](https://t.me/NR_Flighttrackerbot)):

### Monitoring Controls
| Command | Description |
|---|---|
| `/status` | View current status (Active/Paused), active routes, and last check timestamp |
| `/pause` | Suspend automatic 6-hour fare checks |
| `/resume` or `/run` | Resume automatic 6-hour fare checks |
| `/check` or `check now` | Run an immediate on-demand fare check |
| `/help` | Display command guide with examples |

### Dynamic Route Configuration
| Command | Description | Example |
|---|---|---|
| `/routes` or `/list` | List all currently monitored routes and date ranges | `/routes` |
| `/add <SRC> <DST> <START> [END]` | Add or update a monitored route for a period | `/add BLR DEL 2026-11-10 2026-11-15` |
| `/remove <#>` or `/remove <SRC> <DST>` | Remove a monitored route by number or airport pair | `/remove 1` or `/remove BLR DEL` |
| `/clear` | Clear all monitored routes | `/clear` |
| `/reset` | Restore default routes | `/reset` |

---

## ⚙️ Architecture & Features

1. **State Persistence Across Ephemeral Runners**:
   - Configuration (`config.json`), last observed prices (`last_state.json`), and Telegram update offset (`bot_offset.json`) are stored and synchronized on a dedicated GitHub `bot-state` branch via GitHub REST API.
   - Eliminates data loss from runner teardown or Actions cache eviction.

2. **Workflows**:
   - `check-fares.yml`: Scheduled fare checks at 6 AM, 12 PM, 6 PM, 12 AM IST (every 6 hours). Skips automatically when monitoring is paused.
   - `telegram-poll.yml`: Scheduled command poller running every 5 minutes to process incoming Telegram commands.

3. **Immediate Command Execution Feedback**:
   - On every command received, the bot immediately acknowledges with an intent message (e.g. `🤖 Processing: /add ... ⏳ Executing: Validating route details...`) before performing the action and sending the result.

4. **Fare Comparison**:
   - Compares live Google Flights data (via `fast-flights`) against last recorded prices.
   - Highlights price drops (`↓ ₹500`), increases (`↑ ₹200`), airlines, and non-stop vs 1-stop flights.

---

## 🧪 Local Development & Testing

```bash
# Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run unit tests
python -m unittest test_tracker.py

# Run manual fare check
python check_fares.py

# Poll Telegram commands locally
python telegram_poll.py
```
