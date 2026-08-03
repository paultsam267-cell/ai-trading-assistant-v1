import tempfile
import unittest
from pathlib import Path

import pionex_bot_watchlist as pbw


class PionexBotWatchlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.old_paths = (pbw.STATE_DIR, pbw.SNAPSHOT_DIR, pbw.WATCHLIST_FILE, pbw.SUMMARY_FILE)
        pbw.STATE_DIR = base / "intelligence_state"
        pbw.SNAPSHOT_DIR = pbw.STATE_DIR / "pionex_bot_snapshots"
        pbw.WATCHLIST_FILE = pbw.STATE_DIR / "pionex_bot_watchlist.json"
        pbw.SUMMARY_FILE = pbw.STATE_DIR / "pionex_bot_summary.json"

    def tearDown(self):
        pbw.STATE_DIR, pbw.SNAPSHOT_DIR, pbw.WATCHLIST_FILE, pbw.SUMMARY_FILE = self.old_paths
        self.tmp.cleanup()

    def test_creates_files_and_dirs(self):
        pbw.ensure_state()
        self.assertTrue(pbw.STATE_DIR.exists())
        self.assertTrue(pbw.SNAPSHOT_DIR.exists())
        self.assertTrue(pbw.WATCHLIST_FILE.exists())
        self.assertTrue(pbw.SUMMARY_FILE.exists())

    def test_no_snapshots_returns_no_snapshots(self):
        summary = pbw.analyze_snapshots()
        self.assertEqual(summary["status"], "NO_SNAPSHOTS")
        self.assertEqual(summary["action_effect"], "WATCH")

    def test_long_snapshot_analysis(self):
        result = pbw.add_manual_snapshot({"bots": [
            {"pair": "SOL/USDT", "direction": "LONG", "leverage": 20, "roi_percent": 80, "bot_age_text": "1h", "rank": 1},
            {"pair": "RIF/USDT", "direction": "LONG", "leverage": 30, "roi_percent": 90, "bot_age_text": "1h 20m", "rank": 2},
        ]})
        summary = result["summary"]
        self.assertEqual(summary["leaderboard_bias"], "LONG")
        self.assertEqual(summary["long_count"], 2)
        self.assertEqual(summary["short_count"], 0)
        self.assertEqual(summary["action_effect"], "BOOST")

    def test_short_snapshot_analysis(self):
        result = pbw.add_manual_snapshot({"bots": [
            {"pair": "BNB/USDT", "direction": "SHORT", "leverage": 25, "roi_percent": 75, "bot_age_text": "2h", "rank": 1},
            {"pair": "RIF/USDT", "direction": "SHORT", "leverage": 40, "roi_percent": 110, "bot_age_text": "3h", "rank": 2},
        ]})
        summary = result["summary"]
        self.assertEqual(summary["leaderboard_bias"], "SHORT")
        self.assertEqual(summary["short_count"], 2)

    def test_mixed_snapshot_analysis(self):
        result = pbw.add_manual_snapshot({"bots": [
            {"pair": "SOL/USDT", "direction": "LONG", "roi_percent": 40, "bot_age_text": "1h"},
            {"pair": "BNB/USDT", "direction": "SHORT", "roi_percent": 50, "bot_age_text": "1h"},
        ]})
        summary = result["summary"]
        self.assertEqual(summary["leaderboard_bias"], "MIXED")
        self.assertEqual(summary["action_effect"], "WATCH")

    def test_extreme_roi_no_blind_boost(self):
        result = pbw.add_manual_snapshot({"bots": [
            {"pair": "RIF/USDT", "direction": "LONG", "leverage": 50, "roi_percent": 842, "bot_age_text": "8h"},
            {"pair": "ENA/USDT", "direction": "LONG", "leverage": 75, "roi_percent": 443, "bot_age_text": "10h"},
        ]})
        summary = result["summary"]
        self.assertEqual(summary["extreme_roi_count"], 2)
        self.assertNotEqual(summary["action_effect"], "BOOST")

    def test_high_leverage_detected(self):
        result = pbw.add_manual_snapshot({"bots": [
            {"pair": "SOL/USDT", "direction": "LONG", "leverage": "100X", "roi_percent": 20, "bot_age_text": "30m"},
        ]})
        self.assertEqual(result["summary"]["high_leverage_count"], 1)

    def test_missing_fields_do_not_break(self):
        result = pbw.add_manual_snapshot({"bots": [{"pair": "SOL/USDT"}, {}]})
        summary = result["summary"]
        self.assertEqual(summary["total_bots_watched"], 2)
        self.assertEqual(summary["unknown_count"], 2)

    def test_scanner_conflict_reduces_or_blocks(self):
        summary = {
            "leaderboard_bias": "SHORT",
            "action_effect": "BOOST",
            "crowding_state": "NO_CROWDING",
        }
        decision = pbw.scanner_advisory(summary, "LONG")
        self.assertEqual(decision["action_effect"], "REDUCE")


if __name__ == "__main__":
    unittest.main()
