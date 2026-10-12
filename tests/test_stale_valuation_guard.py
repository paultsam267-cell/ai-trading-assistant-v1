from __future__ import annotations

import unittest

from paper_risk import calculate_equity, normalize_position


class StaleValuationGuardTests(unittest.TestCase):

    def test_stale_nonzero_price_uses_cost_basis_not_old_market_price(self):
        account = {"cash": 97.5}

        position = {
            "key": "solana:pumpswap:test",
            "symbol": "TEST",
            "side": "LONG",
            "entry_price": 100.0,
            "quantity": 0.025,
            "initial_quantity": 0.025,
            "remaining_quantity": 0.025,
            "remaining_cost_basis": 2.5,
            "current_price": 200.0,
            "price_status": "STALE",
        }

        normalize_position(position, position["key"])

        equity = calculate_equity(
            account,
            {position["key"]: position},
        )

        self.assertAlmostEqual(equity, 100.0)


if __name__ == "__main__":
    unittest.main()
