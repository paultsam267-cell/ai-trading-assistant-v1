"""Deterministic, offline simulation of the demo risk engine."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from paper_risk import ATHENS_TZ, evaluate_position, normalize_position, set_fresh_price


def make_position(key: str, symbol: str, price: float, opened_hours_ago: int = 1):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=ATHENS_TZ)
    position = {
        "key": key,
        "symbol": symbol,
        "chain": "solana",
        "dex": "pumpswap",
        "entry_price": 100.0,
        "quantity": 1.0,
        "allocated_usd": 100.0,
        "entry_time": (now - timedelta(hours=opened_hours_ago)).strftime("%d/%m/%Y %H:%M:%S"),
    }
    normalize_position(position, key)
    set_fresh_price(position, price, now)
    return position


def main():
    now = datetime(2026, 7, 2, 12, 0, tzinfo=ATHENS_TZ)
    account = {"cash": 800.0, "realized_pnl": 0.0, "wins": 0, "losses": 0}
    positions = {
        "solana:pumpswap:winner": make_position("solana:pumpswap:winner", "WINNER", 350.0),
        "solana:pumpswap:loser": make_position("solana:pumpswap:loser", "LOSER", 60.0),
    }
    trades = []
    actions = []
    for key in list(positions):
        actions.extend(evaluate_position(account, positions, trades, key, at=now))
    print(json.dumps({"account": account, "positions": positions, "actions": actions, "trades": trades}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
