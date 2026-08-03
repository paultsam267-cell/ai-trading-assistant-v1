import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelligence_radar import (
    allocation_decision,
    analyze_pionex_symbol,
    analyze_whales,
    infer_leaderboard,
)


class PionexRadarTests(unittest.TestCase):
    def test_long_flow_from_taker_book_price_and_oi(self):
        result = analyze_pionex_symbol(
            "SOL_USDT_PERP",
            trades=[
                {"price": "100", "size": "5", "side": "BUY"},
                {"price": "100", "size": "1", "side": "SELL"},
            ],
            depth={"bids": [["100", "8"]], "asks": [["101", "2"]]},
            ticker={"open": "95", "close": "100"},
            index={"nextFundingRate": "0.0002"},
            open_interest=110,
            previous_open_interest=100,
        )
        self.assertEqual(result["direction"], "LONG")
        self.assertIn(result["state"], {"LONG_FLOW", "LONG_CROWDING"})
        self.assertGreater(result["confidence"], 50)

    def test_short_squeeze_risk_blocks_new_short(self):
        result = analyze_pionex_symbol(
            "BTC_USDT_PERP",
            trades=[{"price": "100", "size": "9", "side": "BUY"}, {"price": "100", "size": "1", "side": "SELL"}],
            depth={"bids": [["100", "9"]], "asks": [["101", "1"]]},
            ticker={"open": "98", "close": "100"},
            index={"nextFundingRate": "-0.0012"},
            open_interest=120,
            previous_open_interest=100,
        )
        self.assertEqual(result["state"], "SHORT_SQUEEZE_RISK")
        self.assertTrue(result["block_short"])


class LeaderboardTests(unittest.TestCase):
    def test_rollover_and_early_velocity(self):
        payload = {
            "snapshots": [
                {"captured_at": "2026-07-02T23:58:00+03:00", "bots": [
                    {"bot_id": "old", "pair": "BTC/USDT", "direction": "LONG", "rank": 2, "profit_pct": 30, "lasting_minutes": 600}
                ]},
                {"captured_at": "2026-07-03T00:05:00+03:00", "bots": [
                    {"bot_id": "new", "pair": "SOL/USDT", "direction": "SHORT", "rank": 10, "profit_pct": 4, "lasting_minutes": 7, "leverage": 5}
                ]},
            ]
        }
        result = infer_leaderboard(payload)
        self.assertTrue(result["rollover_detected"])
        self.assertEqual(result["bots"][0]["bot_id"], "new")
        self.assertGreater(result["bots"][0]["early_score"], 20)


class WhaleTests(unittest.TestCase):
    def test_whale_accumulation_and_distribution(self):
        result = analyze_whales([
            {"chain": "solana", "token": "mint1", "direction": "BUY", "weight": 2},
            {"chain": "solana", "token": "mint1", "direction": "BUY", "weight": 1},
            {"chain": "bsc", "token": "0xabc", "direction": "SELL", "weight": 2},
        ])
        self.assertEqual(result["by_chain"]["solana"]["direction"], "LONG")
        self.assertEqual(result["by_chain"]["bsc"]["direction"], "SHORT")


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.account = {"initial_capital": 1000, "cash": 800, "position_size_pct": 2.5}
        self.item_long = {"signal": "LONG", "chain": "solana"}

    def test_no_radar_keeps_base_2_5_percent(self):
        result = allocation_decision(self.item_long, self.account, {}, {})
        self.assertEqual(result["allocation_pct"], 2.5)
        self.assertEqual(result["allocation_usd"], 25)

    def test_strong_alignment_uses_four_percent(self):
        intelligence = {"fusion": {"by_chain": {"solana": {
            "direction": "LONG", "confidence": 80, "whale_score": 0.0,
            "block_long": False, "block_short": True,
        }}}}
        result = allocation_decision(self.item_long, self.account, intelligence, {})
        self.assertEqual(result["allocation_pct"], 4.0)
        self.assertEqual(result["allocation_usd"], 40)

    def test_maximum_requires_whale_confirmation(self):
        intelligence = {"fusion": {"by_chain": {"solana": {
            "direction": "LONG", "confidence": 92, "whale_score": 0.5,
            "block_long": False, "block_short": True,
        }}}}
        result = allocation_decision(self.item_long, self.account, intelligence, {})
        self.assertEqual(result["allocation_pct"], 5.0)
        self.assertEqual(result["allocation_usd"], 50)

    def test_conflicting_high_confidence_blocks(self):
        intelligence = {"fusion": {"by_chain": {"solana": {
            "direction": "SHORT", "confidence": 80, "whale_score": -0.5,
            "block_long": True, "block_short": False,
        }}}}
        result = allocation_decision(self.item_long, self.account, intelligence, {})
        self.assertTrue(result["blocked"])
        self.assertEqual(result["allocation_usd"], 0)

    def test_total_exposure_cap(self):
        positions = {"a": {"remaining_cost_basis": 195}}
        intelligence = {"fusion": {"by_chain": {"solana": {
            "direction": "LONG", "confidence": 92, "whale_score": 0.5,
            "block_long": False, "block_short": True,
        }}}}
        result = allocation_decision(self.item_long, self.account, intelligence, positions)
        self.assertEqual(result["allocation_usd"], 5)


if __name__ == "__main__":
    unittest.main()
