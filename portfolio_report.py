from __future__ import annotations

import sys
from typing import Any, Dict

import paper_trader as pt
import telegram_reporting
from capital_management import total_equity
from paper_risk import calculate_equity, effective_price_status, position_unrealized_pnl


def _load_positions() -> Dict[str, Dict[str, Any]]:
    data = pt.load_json_file(pt.POSITIONS_FILE, {})
    return pt.normalize_positions(data if isinstance(data, dict) else {})


def build_report() -> str:
    """Build a consistent report from one locked snapshot of recorded state."""

    with pt.state_transaction():
        account = pt.load_account()
        positions = _load_positions()
        trades = pt.load_json_file(pt.TRADES_FILE, [])
        if not isinstance(trades, list):
            trades = []

        active_equity = calculate_equity(
            account,
            positions,
            use_cost_basis_when_unavailable=True,
        )
        current_equity = total_equity(active_equity, account)
        if all(effective_price_status(position) == "FRESH" for position in positions.values()):
            unrealized: Any = sum(position_unrealized_pnl(position) for position in positions.values())
        else:
            unrealized = None

        report = telegram_reporting.format_portfolio_report(
            account,
            positions,
            trades,
            current_equity=current_equity,
            unrealized_pnl=unrealized,
            verified_at=str(account.get("last_update") or "") or None,
        )

    if positions:
        details = ["", "OPEN PAPER POSITIONS"]
        for position in positions.values():
            details.extend(
                [
                    f"{position.get('asset_class', 'CRYPTO')} {position.get('symbol', 'N/A')} | {position.get('side', 'LONG')}",
                    f"Entry: {position.get('entry_price', 'N/A')} | Current: {position.get('current_price', 'N/A')}",
                    f"PAPER size: {telegram_reporting.money(position.get('remaining_cost_basis'))}",
                    f"Freshness: {effective_price_status(position)}",
                ]
            )
        report = f"{report}\n" + "\n".join(details)
    return report


def main() -> None:
    report = build_report()
    if "--send" in sys.argv:
        pt.send_console_message(report)
    else:
        print(report)


if __name__ == "__main__":
    main()
