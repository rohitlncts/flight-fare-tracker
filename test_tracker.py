#!/usr/bin/env python3
"""Comprehensive unit tests for flight fare tracker state, commands, and formatting."""

import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import check_fares
import state_manager
import telegram_poll


class TestStateManager(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(state_manager.parse_date("2026-11-10"), date(2026, 11, 10))
        self.assertEqual(state_manager.parse_date("10-11-2026"), date(2026, 11, 10))
        self.assertEqual(state_manager.parse_date("10/11/2026"), date(2026, 11, 10))
        self.assertIsNone(state_manager.parse_date("invalid-date"))

    def test_expand_route_dates(self):
        route = {"start_date": "2026-11-10", "end_date": "2026-11-13"}
        dates = state_manager.expand_route_dates(route)
        self.assertEqual(dates, ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"])

        # Single date
        route_single = {"start_date": "2026-11-10"}
        self.assertEqual(state_manager.expand_route_dates(route_single), ["2026-11-10"])

        # Capped to max_days
        route_long = {"start_date": "2026-11-01", "end_date": "2026-11-30"}
        dates_capped = state_manager.expand_route_dates(route_long, max_days=5)
        self.assertEqual(len(dates_capped), 5)
        self.assertEqual(dates_capped[0], "2026-11-01")
        self.assertEqual(dates_capped[-1], "2026-11-05")

        # Explicit dates list
        route_explicit = {"dates": ["2026-11-10", "2026-11-15"]}
        self.assertEqual(state_manager.expand_route_dates(route_explicit), ["2026-11-10", "2026-11-15"])

    def test_get_airport_name(self):
        self.assertEqual(state_manager.get_airport_name("BLR"), "Bengaluru (BLR)")
        self.assertEqual(state_manager.get_airport_name("DEL"), "Delhi (DEL)")
        self.assertEqual(state_manager.get_airport_name("XYZ"), "XYZ")


class TestCheckFares(unittest.TestCase):
    def test_format_inr(self):
        self.assertEqual(check_fares.format_inr(10444), "₹10,444")
        self.assertEqual(check_fares.format_inr(500), "₹500")

    def test_build_message(self):
        results = {
            "BLR:DEL:2026-11-10": {"price": 5000, "airline": "IndiGo", "stops": 0},
            "BLR:DEL:2026-11-11": {"price": 6000, "airline": "Air India", "stops": 1},
        }
        previous = {
            "BLR:DEL:2026-11-10": {"price": 5500},
            "BLR:DEL:2026-11-11": {"price": 5800},
        }
        routes = [{"from": "BLR", "to": "DEL", "start_date": "2026-11-10", "end_date": "2026-11-11"}]
        hints = {"DEL": "Use card discount"}

        msg = check_fares.build_message(
            results, previous, routes, hints, is_paused=False, triggered_by="manual"
        )
        self.assertIn("BLR", msg)
        self.assertIn("DEL", msg)
        self.assertIn("↓ ₹500", msg)
        self.assertIn("↑ ₹200", msg)
        self.assertIn("Cheapest:", msg)
        self.assertIn("🟢 Active", msg)

    def test_build_message_paused(self):
        msg = check_fares.build_message(
            {}, {}, [], {}, is_paused=True, triggered_by=None
        )
        self.assertIn("No routes configured", msg)

    @patch("state_manager.load_config")
    def test_scheduled_check_skips_when_paused(self, mock_load_config):
        mock_load_config.return_value = {"is_paused": True, "routes": []}
        res = check_fares.run_fare_check("dummy_token", "dummy_chat", triggered_by="scheduled", notify=False)
        self.assertIn("PAUSED", res)


class TestTelegramPoll(unittest.TestCase):
    def setUp(self):
        self.config = {
            "is_paused": False,
            "routes": [
                {"from": "BLR", "to": "IXB", "start_date": "2026-11-10", "end_date": "2026-11-13"}
            ],
        }

    def test_handle_status(self):
        msg = telegram_poll.handle_status(self.config, {})
        self.assertIn("Status", msg)
        self.assertIn("🟢 Active", msg)
        self.assertIn("BLR", msg)
        self.assertIn("IXB", msg)

    def test_handle_routes_list(self):
        msg = telegram_poll.handle_routes_list(self.config)
        self.assertIn("Bengaluru (BLR)", msg)
        self.assertIn("Bagdogra (IXB)", msg)

    @patch("state_manager.save_config")
    def test_handle_add_route(self, mock_save):
        msg = telegram_poll.handle_add_route("/add BLR DEL 2026-11-10 2026-11-15", self.config)
        self.assertIn("Successfully Saved 1 Route", msg)
        self.assertEqual(len(self.config["routes"]), 2)
        added = self.config["routes"][1]
        self.assertEqual(added["from"], "BLR")
        self.assertEqual(added["to"], "DEL")
        self.assertEqual(added["start_date"], "2026-11-10")
        self.assertEqual(added["end_date"], "2026-11-15")

    @patch("state_manager.save_config")
    def test_handle_add_route_with_noise_words(self, mock_save):
        msg = telegram_poll.handle_add_route("/add BOM to GOI from 2026-12-01 to 2026-12-05", self.config)
        self.assertIn("Successfully Saved 1 Route", msg)
        self.assertEqual(len(self.config["routes"]), 2)
        added = self.config["routes"][1]
        self.assertEqual(added["from"], "BOM")
        self.assertEqual(added["to"], "GOI")

    @patch("state_manager.save_config")
    def test_handle_add_route_multiline_and_alias(self, mock_save):
        text = "/add PNX BLR 2027-01-05\n/add DBR BLR 2027-01-05"
        msg = telegram_poll.handle_add_route(text, self.config)
        self.assertIn("Successfully Saved 2 Route", msg)
        self.assertIn("mapped PNX → PXN", msg)
        self.assertEqual(len(self.config["routes"]), 3)
        self.assertEqual(self.config["routes"][1]["from"], "PXN")
        self.assertEqual(self.config["routes"][2]["from"], "DBR")

    @patch("state_manager.save_config")
    def test_handle_add_route_invalid(self, mock_save):
        msg = telegram_poll.handle_add_route("/add BOMBAY DELHI", self.config)
        self.assertIn("Error adding route", msg)

    def test_handle_add_route_empty(self):
        msg = telegram_poll.handle_add_route("/add", self.config)
        self.assertIn("How to add routes", msg)

    @patch("state_manager.save_config")
    def test_handle_remove_route(self, mock_save):
        msg = telegram_poll.handle_remove_route("/remove 1", self.config)
        self.assertIn("Removed route #1", msg)
        self.assertEqual(len(self.config["routes"]), 0)

    @patch("telegram_poll.send_telegram")
    def test_acknowledge_command(self, mock_send):
        telegram_poll.acknowledge_command("token", "12345", "/pause", "Suspending checks")
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("Processing:* `/pause`", args[2])
        self.assertIn("Executing:* _Suspending checks_", args[2])


if __name__ == "__main__":
    unittest.main()
