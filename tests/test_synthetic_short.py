from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app as dashboard_app
import paper_trader
from paper_risk import (
    ATHENS_TZ,
    calculate_equity,
    evaluate_position,
    normalize_position,
    position_equity_value,
    position_pnl_pct,
    set_fresh_price,
)
from risk_config import RiskSettings

FIXED_NOW = datetime(2026, 7, 2, 16, 0, 0, tzinfo=ATHENS_TZ)


def make_short(*, entry=100.0, price=100.0, opened_at=None):
    opened_at = opened_at or (FIXED_NOW - timedelta(hours=1))
    position = {
        "key": "solana:pumpswap:shorttest",
        "side": "SHORT",
        "synthetic_short": True,
        "leverage": 1.0,
        "symbol": "SHORTTEST",
        "name": "Short Test Token",
        "chain": "solana",
        "dex": "pumpswap",
        "entry_price": entry,
        "quantity": 1.0,
        "allocated_usd": entry,
        "entry_time": opened_at.strftime("%d/%m/%Y %H:%M:%S"),
        "entry_signal": "SHORT_WATCH",
        "url": "https://dexscreener.com/solana/shortpair",
    }
    normalize_position(position, position["key"])
    set_fresh_price(position, price, FIXED_NOW)
    return position


class SyntheticShortRiskTests(unittest.TestCase):
    def test_short_pnl_and_equity_when_price_falls(self):
        position = make_short(price=70.0)
        self.assertAlmostEqual(position["unrealized_pnl"], 30.0)
        self.assertAlmostEqual(position_pnl_pct(position, 70.0), 30.0)
        self.assertAlmostEqual(position_equity_value(position, 70.0), 130.0)
        self.assertAlmostEqual(calculate_equity({"cash": 900.0}, {position["key"]: position}), 1030.0)

    def test_short_stop_loss_closes_and_releases_margin(self):
        position = make_short(price=125.0)
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {position["key"]: position}
        trades = []
        actions = evaluate_position(account, positions, trades, position["key"], at=FIXED_NOW)
        self.assertEqual(actions[0]["reason"], "SHORT_STOP_LOSS")
        self.assertEqual(actions[0]["side"], "SHORT")
        self.assertNotIn(position["key"], positions)
        self.assertAlmostEqual(account["cash"], 975.0)
        self.assertAlmostEqual(account["realized_pnl"], -25.0)
        self.assertEqual(trades[0]["type"], "SHORT_CLOSE")
        self.assertEqual(account["losses"], 1)

    def test_short_take_profit_levels_and_no_duplicate(self):
        position = make_short(price=40.0)
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {position["key"]: position}
        trades = []
        actions = evaluate_position(account, positions, trades, position["key"], at=FIXED_NOW)
        self.assertEqual(
            [a["reason"] for a in actions],
            ["SHORT_TAKE_PROFIT_1", "SHORT_TAKE_PROFIT_2", "SHORT_TAKE_PROFIT_3"],
        )
        self.assertAlmostEqual(positions[position["key"]]["remaining_quantity"], 0.25)
        self.assertAlmostEqual(account["realized_pnl"], 45.0)
        self.assertEqual(len(trades), 3)
        self.assertEqual(evaluate_position(account, positions, trades, position["key"], at=FIXED_NOW), [])
        self.assertEqual(len(trades), 3)

    def test_short_trailing_stop(self):
        settings = RiskSettings(
            short_stop_loss_pct=50,
            short_take_profit_1_pct=90,
            short_take_profit_2_pct=95,
            short_take_profit_3_pct=99,
            short_trailing_stop_activation_pct=20,
            short_trailing_stop_distance_pct=10,
            short_max_position_age_hours=9999,
        )
        position = make_short(price=70.0)
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {position["key"]: position}
        trades = []
        self.assertEqual(
            evaluate_position(account, positions, trades, position["key"], at=FIXED_NOW, settings=settings),
            [],
        )
        self.assertTrue(positions[position["key"]]["trailing_stop_active"])
        set_fresh_price(positions[position["key"]], 78.0, FIXED_NOW + timedelta(minutes=1))
        actions = evaluate_position(
            account,
            positions,
            trades,
            position["key"],
            at=FIXED_NOW + timedelta(minutes=1),
            settings=settings,
        )
        self.assertEqual(actions[0]["reason"], "SHORT_TRAILING_STOP")
        self.assertNotIn(position["key"], positions)

    def test_short_liquidation_caps_loss_at_margin(self):
        settings = RiskSettings(short_stop_loss_pct=500, short_liquidation_pct=75)
        position = make_short(price=180.0)
        account = {"cash": 900.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {position["key"]: position}
        trades = []
        actions = evaluate_position(
            account, positions, trades, position["key"], at=FIXED_NOW, settings=settings
        )
        self.assertEqual(actions[0]["reason"], "SHORT_LIQUIDATION")
        self.assertAlmostEqual(account["cash"], 920.0)  # 100 margin - 80 loss = 20 returned
        self.assertAlmostEqual(account["realized_pnl"], -80.0)


class SyntheticShortEntryTests(unittest.TestCase):
    def test_short_watch_opens_1x_synthetic_position(self):
        item = {
            "symbol": "BEAR",
            "name": "Bear Token",
            "chain": "solana",
            "dex": "pumpswap",
            "signal": "SHORT_WATCH",
            "score": 70,
            "price_usd": 2.0,
            "liquidity_usd": 100000,
            "volume_h24": 500000,
            "change_h24": -25,
            "txns": {"h1": {"buys": 100, "sells": 160}},
            "url": "https://dexscreener.com/solana/bearpair",
        }
        account = {"cash": 1000.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
        positions = {}
        trades = []
        message = paper_trader.open_demo_position(account, positions, trades, item, FIXED_NOW)
        self.assertIn("SYNTHETIC SHORT", message)
        position = positions[paper_trader.position_key(item)]
        self.assertEqual(position["side"], "SHORT")
        self.assertEqual(position["leverage"], 1.0)
        self.assertAlmostEqual(position["remaining_cost_basis"], 25.0)
        self.assertAlmostEqual(account["cash"], 975.0)
        self.assertEqual(trades[0]["type"], "SHORT_OPEN")

    def test_short_rejects_already_collapsed_token(self):
        item = {
            "symbol": "CRASH",
            "chain": "solana",
            "dex": "pumpswap",
            "signal": "SHORT_WATCH",
            "score": 70,
            "price_usd": 1.0,
            "liquidity_usd": 100000,
            "volume_h24": 500000,
            "change_h24": -85,
            "txns": {"h1": {"buys": 100, "sells": 200}},
        }
        allowed, reason = paper_trader.should_open_short(item, {"cash": 1000}, {})
        self.assertFalse(allowed)
        self.assertIn("καταρρεύσει", reason)


class SyntheticShortDashboardTests(unittest.TestCase):
    def test_dashboard_renders_short_side_and_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            archive = Path(tmp) / "archive"
            state.mkdir()
            archive.mkdir()
            (state / "paper_account.json").write_text(
                json.dumps({"cash": 975.0, "realized_pnl": 0.0}), encoding="utf-8"
            )
            position = make_short(price=90.0)
            (state / "paper_positions.json").write_text(
                json.dumps({position["key"]: position}), encoding="utf-8"
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
                self.assertIn("SHORTTEST", text)
                self.assertIn("Synthetic SHORT Stop Loss", text)
                self.assertIn("LONG / SHORT", text)


class ScannerShortMixTests(unittest.TestCase):
    def test_report_reserves_short_watch_candidate(self):
        import scanner

        def pair(symbol, score_bias, change, buys, sells):
            return {
                "baseToken": {"symbol": symbol},
                "chainId": "solana",
                "liquidity": {"usd": 150000 + score_bias},
                "volume": {"h24": 500000 + score_bias},
                "priceChange": {"h24": change},
                "txns": {"h1": {"buys": buys, "sells": sells}},
                "boosts": {"active": 0},
                "pairCreatedAt": 0,
            }

        candidates = [pair(f"LONG{i}", i * 1000, 50, 200, 100) for i in range(10)]
        short_pair = pair("BEAR", 0, -20, 100, 180)
        candidates.append(short_pair)
        with patch.object(scanner, "MAX_REPORT_ITEMS", 8), patch.object(scanner, "MAX_SHORT_REPORT_ITEMS", 2):
            mixed = scanner.prioritize_report_mix(candidates)
        self.assertIn(short_pair, mixed[:8])


if __name__ == "__main__":
    unittest.main()
