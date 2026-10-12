from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import market_scanner
import paper_trader
import telegram_reporting


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse(self.responses.pop(0))

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse({"ok": True, "result": {"message_id": 1}})


class YahooFreshnessTests(unittest.TestCase):
    def _chart(self, timestamp):
        return {
            "chart": {
                "result": [
                    {
                        "timestamp": [int(timestamp.timestamp())],
                        "meta": {"currency": "USD", "exchangeName": "TEST"},
                        "indicators": {"quote": [{"close": [123.45], "volume": [1000]}]},
                    }
                ]
            }
        }

    def test_http_fetch_time_cannot_replace_stale_market_timestamp(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        stale = now - timedelta(days=10)
        session = FakeSession([self._chart(stale)])
        self.assertIsNone(market_scanner.fetch_current_quote("AAPL", "STOCK", session=session, now=now))

    def test_returned_timestamp_is_actual_yahoo_candle_timestamp(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        candle_at = now - timedelta(hours=1)
        session = FakeSession([self._chart(candle_at)])
        quote = market_scanner.fetch_current_quote("AAPL", "STOCK", session=session, now=now)
        self.assertIsNotNone(quote)
        self.assertEqual(quote["market_data_timestamp"], candle_at.isoformat())


class GitHubExecutionGuardTests(unittest.TestCase):
    def test_paper_trader_rejects_github_actions_cache_state(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "ephemeral workflow/cache state"):
                paper_trader.require_local_paper_execution()

        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}, clear=False):
            paper_trader.require_local_paper_execution()


class TelegramReportingTests(unittest.TestCase):
    def test_portfolio_report_has_required_paper_fields(self):
        report = telegram_reporting.format_portfolio_report(
            {
                "simulation_id": "sim-test",
                "initial_capital": 1000,
                "cash": 1000,
                "reserve_cash": 0,
                "realized_pnl": 0,
                "last_update": "12/10/2026 12:00:00",
            },
            {},
            [],
            current_equity=1000,
            unrealized_pnl=0,
        )
        for expected in (
            telegram_reporting.BRAND,
            telegram_reporting.PAPER_NOTICE,
            "Simulation ID: sim-test",
            "Initial capital: €1,000.00",
            "Current equity: €1,000.00",
            "Available cash: €1,000.00",
            "Open PAPER positions: 0",
            "Total PAPER trades: 0",
            "Total return: 0.00%",
        ):
            self.assertIn(expected, report)

    def test_unknown_trade_metrics_are_explicit_na(self):
        report = telegram_reporting.format_trade_report({"symbol": "AAPL", "side": "LONG"})
        self.assertIn("P/L: N/A", report)
        self.assertIn("Return: N/A", report)
        self.assertIn("Data freshness: N/A", report)

    def test_routing_validation_uses_get_only_and_checks_bot_username(self):
        session = FakeSession(
            [
                {"ok": True, "result": {"username": "PDJD_Nexus_Trading_Bot"}},
                {"ok": True, "result": {"id": 123, "type": "private"}},
            ]
        )
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "secret", "TELEGRAM_CHAT_ID": "123"}):
            status = telegram_reporting.routing_status(session=session, perform_network_check=True)
        self.assertTrue(status["bot_identity_verified"])
        self.assertTrue(status["chat_routing_verified"])
        self.assertEqual(len(session.get_calls), 2)
        self.assertEqual(session.post_calls, [])

    def test_send_adds_branding_without_logging_credentials(self):
        session = FakeSession()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "secret", "TELEGRAM_CHAT_ID": "123"}):
            self.assertTrue(telegram_reporting.send_notification("Activation check", session=session))
        payload = session.post_calls[0][1]["json"]
        self.assertTrue(payload["text"].startswith(telegram_reporting.BRAND))
        self.assertIn(telegram_reporting.PAPER_NOTICE, payload["text"])


if __name__ == "__main__":
    unittest.main()
