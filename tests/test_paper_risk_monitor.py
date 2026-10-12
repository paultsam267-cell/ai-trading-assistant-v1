from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

import paper_risk_monitor as monitor
from paper_risk import ATHENS_TZ, normalize_position, set_fresh_price


FIXED_NOW = datetime(2026, 10, 10, 8, 0, 0, tzinfo=ATHENS_TZ)
KEY = "solana:pumpswap:test"


def make_account():
    return {
        "initial_capital": 100.0,
        "cash": 97.5,
        "realized_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "reserve_cash": 0.0,
        "reserve_rate_pct": 25.0,
        "reserve_profit_hwm": 0.0,
        "reserve_contributed_total": 0.0,
        "reserve_released_total": 0.0,
    }


def make_position(price=100.0):
    position = {
        "key": KEY,
        "symbol": "TEST",
        "name": "Test Token",
        "chain": "solana",
        "dex": "pumpswap",
        "side": "LONG",
        "entry_price": 100.0,
        "quantity": 0.025,
        "initial_quantity": 0.025,
        "remaining_quantity": 0.025,
        "allocated_usd": 2.5,
        "remaining_cost_basis": 2.5,
        "entry_time": "10/10/2026 07:00:00",
        "url": "https://dexscreener.com/solana/testpair",
    }
    normalize_position(position, KEY)
    set_fresh_price(position, price, FIXED_NOW)
    return position


class ContinuousPaperRiskMonitorTests(unittest.TestCase):

    @patch.object(monitor.pt, "send_console_message")
    @patch.object(monitor.pt, "save_json_file")
    @patch.object(monitor.pt, "apply_profit_reserve")
    @patch.object(monitor.pt, "fetch_open_position_market_data", return_value={})
    @patch.object(monitor.pt, "now_local")
    @patch.object(monitor.pt, "load_json_file")
    @patch.object(monitor.pt, "load_account")
    def test_monitor_does_not_open_new_positions(
        self,
        mock_load_account,
        mock_load_json,
        mock_now,
        mock_fetch,
        mock_reserve,
        mock_save,
        mock_send,
    ):
        mock_now.return_value = FIXED_NOW
        mock_load_account.return_value = make_account()
        mock_load_json.side_effect = [{}, {}, [], []]
        mock_reserve.return_value = {"transferred": 0.0}

        result = monitor.run_risk_cycle()

        self.assertEqual(result["checked_positions"], 0)
        self.assertEqual(result["actions"], 0)
        self.assertEqual(result["alerts"], 0)
        mock_send.assert_not_called()

        # Ο monitor πρέπει να ζητά live refresh χωρίς scanner fallback.
        args, _ = mock_fetch.call_args
        self.assertEqual(args[1], {})

    @patch.object(monitor.pt, "send_console_message")
    @patch.object(monitor.pt, "save_json_file")
    @patch.object(monitor.pt, "apply_profit_reserve")
    @patch.object(monitor.pt, "fetch_open_position_market_data", return_value={KEY: {"price_usd": 100.0}})
    @patch.object(monitor.pt, "now_local")
    @patch.object(monitor.pt, "load_json_file")
    @patch.object(monitor.pt, "load_account")
    def test_no_risk_action_means_no_telegram_alert(
        self,
        mock_load_account,
        mock_load_json,
        mock_now,
        mock_fetch,
        mock_reserve,
        mock_save,
        mock_send,
    ):
        mock_now.return_value = FIXED_NOW
        mock_load_account.return_value = make_account()
        position = make_position(price=100.0)
        mock_load_json.side_effect = [{KEY: position}, {KEY: position}, [], []]
        mock_reserve.return_value = {"transferred": 0.0}

        result = monitor.run_risk_cycle()

        self.assertEqual(result["actions"], 0)
        self.assertEqual(result["alerts"], 0)
        mock_send.assert_not_called()

    @patch.object(monitor.pt, "send_console_message")
    @patch.object(monitor.pt, "save_json_file")
    @patch.object(monitor.pt, "apply_profit_reserve")
    @patch.object(monitor.pt, "now_local")
    @patch.object(monitor.pt, "load_json_file")
    @patch.object(monitor.pt, "load_account")
    def test_fresh_stop_loss_is_executed_and_alerted(
        self,
        mock_load_account,
        mock_load_json,
        mock_now,
        mock_reserve,
        mock_save,
        mock_send,
    ):
        mock_now.return_value = FIXED_NOW
        account = make_account()
        position = make_position(price=100.0)

        mock_load_account.return_value = account
        mock_load_json.side_effect = [{KEY: position}, {KEY: position}, [], []]
        mock_reserve.return_value = {"transferred": 0.0}

        with patch.object(
            monitor.pt,
            "fetch_open_position_market_data",
            return_value={KEY: {"price_usd": 60.0}},
        ):
            result = monitor.run_risk_cycle()

        self.assertEqual(result["actions"], 1)
        self.assertEqual(result["alerts"], 1)
        mock_send.assert_called_once()
        self.assertIn("STOP_LOSS", mock_send.call_args.args[0])

    @patch.object(monitor.pt, "send_console_message")
    @patch.object(monitor.pt, "save_json_file")
    @patch.object(monitor.pt, "apply_profit_reserve")
    @patch.object(monitor.pt, "now_local")
    @patch.object(monitor.pt, "load_json_file")
    @patch.object(monitor.pt, "load_account")
    def test_missing_live_price_produces_no_exit(
        self,
        mock_load_account,
        mock_load_json,
        mock_now,
        mock_reserve,
        mock_save,
        mock_send,
    ):
        mock_now.return_value = FIXED_NOW
        position = make_position(price=100.0)

        mock_load_account.return_value = make_account()
        mock_load_json.side_effect = [{KEY: position}, {KEY: position}, [], []]
        mock_reserve.return_value = {"transferred": 0.0}

        with patch.object(
            monitor.pt,
            "fetch_open_position_market_data",
            return_value={},
        ):
            result = monitor.run_risk_cycle()

        self.assertEqual(result["actions"], 0)
        self.assertEqual(result["alerts"], 0)
        self.assertEqual(result["skipped_exits"], 1)
        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
