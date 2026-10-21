from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

import paper_trader as pt
from paper_risk import ATHENS_TZ


FIXED_NOW = datetime(2026, 10, 10, 8, 0, 0, tzinfo=ATHENS_TZ)


class AssetIdentityStorageTests(unittest.TestCase):

    @patch.object(pt, "allocation_decision")
    @patch.object(pt, "should_open_position")
    def test_new_position_stores_token_and_pair_identity(
        self,
        mock_should_open,
        mock_allocation,
    ):
        mock_should_open.return_value = (True, "TEST ALLOWED")
        mock_allocation.return_value = {
            "allocation_usd": 2.5,
            "allocation_pct": 2.5,
            "level": "TEST",
            "regime_direction": "NEUTRAL",
            "regime_confidence": 0.0,
        }

        account = {"cash": 100.0}
        positions = {}
        trades = []

        item = {
            "chain": "solana",
            "dex": "pumpswap",
            "symbol": "TEST",
            "name": "Test Token",
            "signal": "LONG",
            "price_usd": 1.0,
            "score": 100.0,
            "url": "https://dexscreener.com/solana/testpair",
            "token_address": "TOKEN_MINT_123",
            "pair_address": "PAIR_ABC_456",
        }

        pt.open_demo_position(
            account,
            positions,
            trades,
            item,
            FIXED_NOW,
            {},
        )

        self.assertEqual(len(positions), 1)

        position = next(iter(positions.values()))

        self.assertEqual(position["token_address"], "TOKEN_MINT_123")
        self.assertEqual(position["pair_address"], "PAIR_ABC_456")


if __name__ == "__main__":
    unittest.main()
