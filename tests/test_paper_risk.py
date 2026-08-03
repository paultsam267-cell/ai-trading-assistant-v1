from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app as dashboard_app
from paper_risk import (
    ATHENS_TZ,
    calculate_equity,
    daily_pnl,
    effective_price_status,
    ensure_daily_snapshot,
    evaluate_position,
    execute_sale,
    normalize_position,
    set_fresh_price,
)
from risk_config import RiskSettings


FIXED_NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=ATHENS_TZ)


def make_position(*, entry=100.0, price=100.0, opened_at=None):
    opened_at = opened_at or (FIXED_NOW - timedelta(hours=1))
    position = {
        "key": "solana:pumpswap:test",
        "symbol": "TEST",
        "name": "Test Token",
        "chain": "solana",
        "dex": "pumpswap",
        "entry_price": entry,
        "quantity": 1.0,
        "allocated_usd": entry,
        "entry_time": opened_at.strftime("%d/%m/%Y %H:%M:%S"),
        "url": "https://dexscreener.com/solana/testpair",
    }
    normalize_position(position, position["key"])
    set_fresh_price(position, price, FIXED_NOW)
    return position


class RiskEngineTests(unittest.TestCase):
    def test_stop_loss_full_close(self):
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {"solana:pumpswap:test": make_position(price=60.0)}
        trades = []
        actions = evaluate_position(account, positions, trades, "solana:pumpswap:test", at=FIXED_NOW)
        self.assertEqual(actions[0]["reason"], "STOP_LOSS")
        self.assertNotIn("solana:pumpswap:test", positions)
        self.assertAlmostEqual(account["cash"], 960.0)
        self.assertAlmostEqual(account["realized_pnl"], -40.0)
        self.assertEqual(account["losses"], 1)

    def test_all_take_profit_levels_and_no_duplicate_execution(self):
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {"solana:pumpswap:test": make_position(price=350.0)}
        trades = []
        actions = evaluate_position(account, positions, trades, "solana:pumpswap:test", at=FIXED_NOW)
        self.assertEqual(
            [action["reason"] for action in actions],
            ["TAKE_PROFIT_1", "TAKE_PROFIT_2", "TAKE_PROFIT_3"],
        )
        position = positions["solana:pumpswap:test"]
        self.assertAlmostEqual(position["remaining_quantity"], 0.25)
        self.assertEqual(len(trades), 3)
        self.assertEqual(
            position["take_profit_levels_completed"],
            ["TAKE_PROFIT_1", "TAKE_PROFIT_2", "TAKE_PROFIT_3"],
        )
        second_actions = evaluate_position(account, positions, trades, "solana:pumpswap:test", at=FIXED_NOW)
        self.assertEqual(second_actions, [])
        self.assertEqual(len(trades), 3)

    def test_trailing_stop(self):
        settings = RiskSettings(
            stop_loss_pct=35,
            take_profit_1_pct=1000,
            take_profit_1_sell_pct=25,
            take_profit_2_pct=2000,
            take_profit_2_sell_pct=25,
            take_profit_3_pct=3000,
            take_profit_3_sell_pct=25,
            trailing_stop_activation_pct=100,
            trailing_stop_distance_pct=20,
            max_position_age_hours=9999,
            stale_price_minutes=5,
            max_open_positions=10,
        )
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        position = make_position(price=250.0)
        positions = {position["key"]: position}
        trades = []
        self.assertEqual(
            evaluate_position(account, positions, trades, position["key"], at=FIXED_NOW, settings=settings),
            [],
        )
        self.assertTrue(positions[position["key"]]["trailing_stop_active"])
        set_fresh_price(positions[position["key"]], 190.0, FIXED_NOW + timedelta(minutes=1))
        actions = evaluate_position(
            account,
            positions,
            trades,
            position["key"],
            at=FIXED_NOW + timedelta(minutes=1),
            settings=settings,
        )
        self.assertEqual(actions[0]["reason"], "TRAILING_STOP")
        self.assertNotIn(position["key"], positions)

    def test_max_holding_time(self):
        settings = RiskSettings(
            stop_loss_pct=35,
            take_profit_1_pct=1000,
            take_profit_1_sell_pct=25,
            take_profit_2_pct=2000,
            take_profit_2_sell_pct=25,
            take_profit_3_pct=3000,
            take_profit_3_sell_pct=25,
            trailing_stop_activation_pct=1000,
            trailing_stop_distance_pct=20,
            max_position_age_hours=168,
            stale_price_minutes=5,
            max_open_positions=10,
        )
        old = FIXED_NOW - timedelta(hours=169)
        position = make_position(price=110.0, opened_at=old)
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {position["key"]: position}
        trades = []
        actions = evaluate_position(
            account, positions, trades, position["key"], at=FIXED_NOW, settings=settings
        )
        self.assertEqual(actions[0]["reason"], "MAX_HOLD_TIME")

    def test_partial_sell_cash_realized_and_unrealized(self):
        position = make_position(price=120.0)
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {position["key"]: position}
        trades = []
        result = execute_sale(
            account,
            positions,
            trades,
            position["key"],
            120.0,
            "MANUAL_CLOSE",
            sell_fraction_remaining=0.5,
            at=FIXED_NOW,
        )
        self.assertFalse(result["fully_closed"])
        self.assertAlmostEqual(account["cash"], 960.0)
        self.assertAlmostEqual(account["realized_pnl"], 10.0)
        self.assertAlmostEqual(positions[position["key"]]["remaining_quantity"], 0.5)
        self.assertAlmostEqual(positions[position["key"]]["remaining_cost_basis"], 50.0)
        self.assertAlmostEqual(positions[position["key"]]["unrealized_pnl"], 10.0)

    def test_stale_and_unavailable_prices_do_not_close(self):
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        stale = make_position(price=50.0)
        stale["price_status"] = "STALE"
        positions = {stale["key"]: stale}
        trades = []
        self.assertEqual(evaluate_position(account, positions, trades, stale["key"], at=FIXED_NOW), [])
        self.assertIn(stale["key"], positions)

        unavailable = make_position(price=100.0)
        unavailable["current_price"] = None
        unavailable["last_price_update_at"] = None
        unavailable["price_status"] = "UNAVAILABLE"
        positions = {unavailable["key"]: unavailable}
        self.assertEqual(evaluate_position(account, positions, trades, unavailable["key"], at=FIXED_NOW), [])
        self.assertEqual(effective_price_status(unavailable, FIXED_NOW), "UNAVAILABLE")

    def test_backward_compatibility_old_wallet_record(self):
        old = {
            "entry_price": 2.0,
            "quantity": 5.0,
            "allocated_usd": 10.0,
            "entry_time": "01/07/2026 10:00:00",
        }
        normalize_position(old, "bsc:pancakeswap:old")
        self.assertEqual(old["initial_quantity"], 5.0)
        self.assertEqual(old["remaining_quantity"], 5.0)
        self.assertEqual(old["remaining_cost_basis"], 10.0)
        self.assertEqual(old["price_status"], "UNAVAILABLE")
        self.assertIn("take_profit_levels_completed", old)

    def test_daily_snapshot_uses_athens_calendar_day(self):
        account = {}
        ensure_daily_snapshot(account, 1000.0, FIXED_NOW)
        self.assertEqual(account["day_start_date"], "2026-07-02")
        ensure_daily_snapshot(account, 1100.0, FIXED_NOW + timedelta(hours=2))
        self.assertEqual(account["day_start_equity"], 1000.0)
        result = daily_pnl(account, 1100.0)
        self.assertAlmostEqual(result["pnl"], 100.0)
        self.assertAlmostEqual(result["pct"], 10.0)
        ensure_daily_snapshot(account, 900.0, FIXED_NOW + timedelta(days=1))
        self.assertEqual(account["day_start_date"], "2026-07-03")
        self.assertEqual(account["day_start_equity"], 900.0)

    def test_equity_falls_back_to_cost_basis_only_when_price_unavailable(self):
        account = {"cash": 900.0}
        position = make_position(price=120.0)
        positions = {position["key"]: position}
        self.assertAlmostEqual(calculate_equity(account, positions), 1020.0)
        position["current_price"] = None
        position["price_status"] = "UNAVAILABLE"
        self.assertAlmostEqual(calculate_equity(account, positions), 1000.0)


class DashboardCompatibilityTests(unittest.TestCase):
    def test_dashboard_renders_old_position_without_missing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            archive = Path(tmp) / "archive"
            state.mkdir()
            archive.mkdir()
            (state / "paper_account.json").write_text(
                json.dumps({"cash": 975.0, "realized_pnl": 0.0}), encoding="utf-8"
            )
            (state / "paper_positions.json").write_text(
                json.dumps(
                    {
                        "solana:pumpswap:legacy": {
                            "key": "solana:pumpswap:legacy",
                            "symbol": "LEGACY",
                            "chain": "solana",
                            "dex": "pumpswap",
                            "entry_price": 1.0,
                            "quantity": 25.0,
                            "allocated_usd": 25.0,
                            "entry_time": "01/07/2026 10:00:00",
                            "url": "",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (state / "paper_trade_log.json").write_text("[]", encoding="utf-8")

            with patch.object(dashboard_app, "STATE_DIR", state), patch.object(
                dashboard_app, "POSITIONS_FILE", state / "paper_positions.json"
            ), patch.object(dashboard_app, "TRADES_FILE", state / "paper_trade_log.json"), patch.object(
                dashboard_app, "ACCOUNT_FILE", state / "paper_account.json"
            ), patch.object(dashboard_app, "ARCHIVE_DIR", archive), patch.object(
                dashboard_app, "fetch_live_pair", return_value={}
            ):
                client = dashboard_app.app.test_client()
                response = client.get("/")
                self.assertEqual(response.status_code, 200)
                text = response.get_data(as_text=True)
                self.assertIn("LEGACY", text)
                self.assertIn("ΜΗ ΔΙΑΘΕΣΙΜΗ", text)
                self.assertIn("Κανόνες Διαχείρισης Ρίσκου", text)


if __name__ == "__main__":
    unittest.main()
