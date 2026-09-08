#!/bin/bash
# Trigger an immediate Telegram poll (check for "check now" messages).
gh workflow run telegram-poll.yml --repo rohitlncts/flight-fare-tracker
echo "Triggered. Usually completes in ~30 seconds."
