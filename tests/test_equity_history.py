from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import paper_risk_monitor as monitor
from paper_risk import ATHENS_TZ


NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=ATHENS_TZ)


class EquityHistoryTests(unittest.TestCase):

    @patch.object(monitor.pt, "save_json_file")
    @patch.object(monitor.pt, "load_json_file")
    def test_first_snapshot_is_written(self, mock_load, mock_save):
        mock_load.return_value = []

        account = {
            "initial_capital": 100.0,
            "cash": 97.5,
            "reserve_cash": 0.0,
        }

        positions = {
            "test": {
                "key": "test",
                "side": "LONG",
                "remaining_cost_basis": 2.5,
                "current_price": 0.0,
                "price_status": "MISSING",
            }
        }

        written = monitor.record_equity_snapshot(
            account,
            positions,
            NOW,
        )

        self.assertTrue(written)
        mock_save.assert_called_once()

        history = mock_save.call_args.args[1]
        self.assertEqual(len(history), 1)
        self.assertAlmostEqual(history[0]["total_equity"], 100.0)
        self.assertAlmostEqual(history[0]["total_pnl"], 0.0)
        self.assertAlmostEqual(history[0]["total_return_pct"], 0.0)

    @patch.object(monitor.pt, "save_json_file")
    @patch.object(monitor.pt, "load_json_file")
    def test_unchanged_equity_inside_interval_is_not_duplicated(
        self,
        mock_load,
        mock_save,
    ):
        mock_load.return_value = [
            {
                "timestamp": (NOW - timedelta(seconds=60)).isoformat(),
                "total_equity": 100.0,
            }
        ]

        account = {
            "initial_capital": 100.0,
            "cash": 100.0,
            "reserve_cash": 0.0,
        }

        written = monitor.record_equity_snapshot(
            account,
            {},
            NOW,
        )

        self.assertFalse(written)
        mock_save.assert_not_called()

    @patch.object(monitor.pt, "save_json_file")
    @patch.object(monitor.pt, "load_json_file")
    def test_equity_change_inside_interval_is_written(
        self,
        mock_load,
        mock_save,
    ):
        mock_load.return_value = [
            {
                "timestamp": (NOW - timedelta(seconds=60)).isoformat(),
                "total_equity": 100.0,
            }
        ]

        account = {
            "initial_capital": 100.0,
            "cash": 99.0,
            "reserve_cash": 0.0,
        }

        written = monitor.record_equity_snapshot(
            account,
            {},
            NOW,
        )

        self.assertTrue(written)

        history = mock_save.call_args.args[1]

        self.assertAlmostEqual(history[-1]["total_equity"], 99.0)
        self.assertAlmostEqual(history[-1]["total_pnl"], -1.0)
        self.assertAlmostEqual(history[-1]["total_return_pct"], -1.0)


if __name__ == "__main__":
    unittest.main()
