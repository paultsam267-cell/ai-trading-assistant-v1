from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import paper_opportunity_monitor as opportunity
import paper_trader as pt
from capital_management import new_simulation_account
from paper_risk import ATHENS_TZ


FIXED_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=ATHENS_TZ)


class MonitorTransactionTests(unittest.TestCase):
    def test_concurrent_cycles_revalidate_duplicate_entry_inside_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "paper_state"
            account_file = state_dir / "paper_account.json"
            positions_file = state_dir / "paper_positions.json"
            trades_file = state_dir / "paper_trade_log.json"
            pt.save_json_file(account_file, new_simulation_account(1000, at=FIXED_NOW))
            pt.save_json_file(positions_file, {})
            pt.save_json_file(trades_file, [])

            candidate = {
                "asset_class": "CRYPTO",
                "instrument_id": "CRYPTO:solana:abc",
                "symbol": "SAFE",
                "name": "Safe Test",
                "signal": "LONG",
                "score": 100.0,
                "price_usd": 10.0,
                "change_h24": 20.0,
                "liquidity_usd": 100000.0,
                "volume_h24": 500000.0,
                "txns": {"h1": {"buys": 20, "sells": 5}},
                "chain": "solana",
                "dex": "test",
                "url": "https://dexscreener.com/solana/test",
                "paper_tradable": True,
            }

            with ExitStack() as stack:
                stack.enter_context(patch.object(pt, "STATE_DIR", state_dir))
                stack.enter_context(patch.object(pt, "ACCOUNT_FILE", account_file))
                stack.enter_context(patch.object(pt, "POSITIONS_FILE", positions_file))
                stack.enter_context(patch.object(pt, "TRADES_FILE", trades_file))
                stack.enter_context(patch.object(opportunity, "scan_crypto_candidates", return_value=[candidate]))
                stack.enter_context(patch.object(opportunity.market_scanner, "scan_market", return_value=[]))
                stack.enter_context(patch.object(pt, "load_latest_intelligence", return_value=None))
                stack.enter_context(patch.object(pt, "send_console_message"))
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(
                        executor.map(
                            lambda _: opportunity.run_opportunity_cycle(allow_entries=True, run_at=FIXED_NOW),
                            range(2),
                        )
                    )

            positions = pt.load_json_file(positions_file, {})
            trades = pt.load_json_file(trades_file, [])
            self.assertEqual(sum(result["paper_entries"] for result in results), 1)
            self.assertEqual(len(positions), 1)
            self.assertEqual(len(trades), 1)


if __name__ == "__main__":
    unittest.main()
