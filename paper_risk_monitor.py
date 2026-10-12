"""Continuous Paper Risk Monitor V1.

Παρακολουθεί ΜΟΝΟ ήδη ανοιχτές paper θέσεις.
Δεν σαρώνει για νέες ευκαιρίες και δεν ανοίγει νέες θέσεις.
Δεν εκτελεί πραγματικές συναλλαγές.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, List

import paper_trader as pt
import state_store


MONITOR_SECONDS = max(
    10,
    int(os.getenv("PAPER_RISK_MONITOR_SECONDS", "60")),
)

EQUITY_HISTORY_FILE = pt.STATE_DIR / "equity_history.json"

EQUITY_SNAPSHOT_SECONDS = max(
    60,
    int(os.getenv("PAPER_EQUITY_SNAPSHOT_SECONDS", "300")),
)


def record_equity_snapshot(
    account: Dict[str, Any],
    positions: Dict[str, Dict[str, Any]],
    run_at: datetime,
) -> bool:
    """?????????? ?????????? portfolio equity history."""

    history = pt.load_json_file(EQUITY_HISTORY_FILE, [])

    if not isinstance(history, list):
        history = []

    active_equity = pt.calculate_equity(
        account,
        positions,
        use_cost_basis_when_unavailable=True,
    )

    reserve_cash = pt.safe_float(account.get("reserve_cash"))
    total = pt.total_equity(active_equity, account)
    initial = pt.safe_float(account.get("initial_capital"))

    pnl = total - initial
    return_pct = (
        pnl / initial * 100.0
        if initial > 0
        else 0.0
    )

    if history:
        last = (
            history[-1]
            if isinstance(history[-1], dict)
            else {}
        )

        last_total = pt.safe_float(
            last.get("total_equity")
        )

        last_timestamp = str(
            last.get("timestamp") or ""
        )

        try:
            last_at = datetime.fromisoformat(
                last_timestamp
            )
            elapsed = (
                run_at - last_at
            ).total_seconds()

        except (TypeError, ValueError):
            elapsed = EQUITY_SNAPSHOT_SECONDS

        equity_changed = (
            abs(total - last_total) >= 0.00000001
        )

        if (
            elapsed < EQUITY_SNAPSHOT_SECONDS
            and not equity_changed
        ):
            return False

    history.append(
        {
            "timestamp": run_at.isoformat(),
            "local_time": pt.format_local(run_at),
            "active_equity": round(active_equity, 8),
            "reserve_cash": round(reserve_cash, 8),
            "total_equity": round(total, 8),
            "total_pnl": round(pnl, 8),
            "total_return_pct": round(return_pct, 8),
            "open_positions": len(positions),
        }
    )

    pt.save_json_file(
        EQUITY_HISTORY_FILE,
        history,
    )

    return True


def run_risk_cycle() -> Dict[str, Any]:
    """Εκτελεί έναν μόνο κύκλο risk monitoring."""

    run_at = pt.now_local()

    with pt.state_transaction():
        position_snapshot = pt.normalize_positions(pt.load_json_file(pt.POSITIONS_FILE, {}))

    # Market calls are outside the lock.  An empty map later marks every
    # affected position STALE/UNAVAILABLE and cannot trigger an exit.
    market_map = pt.fetch_open_position_market_data(position_snapshot, {}, run_at)

    alerts: List[str] = []
    actions_count = 0
    with pt.state_transaction():
        # Reload current state and revalidate exits while holding the one common
        # portfolio lock shared with entry writers.
        account = pt.load_account()
        positions = pt.normalize_positions(pt.load_json_file(pt.POSITIONS_FILE, {}))
        trades = pt.load_json_file(pt.TRADES_FILE, [])
        if not isinstance(trades, list):
            trades = []

        transaction_at = pt.now_local()
        skipped_exits = pt.apply_open_position_market_data(positions, market_map, transaction_at)

        active_equity = pt.calculate_equity(account, positions)
        pt.ensure_daily_snapshot(
            account,
            pt.total_equity(active_equity, account),
            transaction_at,
        )

        for key in list(positions.keys()):
            actions = pt.evaluate_position(
                account,
                positions,
                trades,
                key,
                at=transaction_at,
            )

            for action in actions:
                actions_count += 1
                alerts.append(pt.action_message(action))

        reserve_result = pt.apply_profit_reserve(account, at=transaction_at)
        if reserve_result["transferred"] > 0:
            alerts.append(
                "ΑΥΤΟΜΑΤΗ ΜΕΤΑΦΟΡΑ ΣΤΟ ΑΠΟΘΕΜΑΤΙΚΟ\n"
                f"Ποσό: {pt.format_money(reserve_result['transferred'])}\n"
                f"Κανόνας: {pt.safe_float(account.get('reserve_rate_pct')):.0f}% "
                "του νέου peak καθαρού πραγματοποιημένου κέρδους."
            )

        account["last_update"] = pt.format_local(transaction_at)
        pt.save_json_file(pt.POSITIONS_FILE, positions)
        pt.save_json_file(pt.TRADES_FILE, trades)
        pt.save_json_file(pt.ACCOUNT_FILE, account)
        equity_snapshot_written = record_equity_snapshot(account, positions, transaction_at)

    # Telegram μόνο όταν υπάρχει πραγματικό risk action
    # ή μεταφορά στο reserve.
    for alert in alerts:
        pt.send_console_message(alert)

    return {
        "checked_positions": len(positions),
        "actions": actions_count,
        "alerts": len(alerts),
        "skipped_exits": len(skipped_exits),
        "equity_snapshot_written": equity_snapshot_written,
        "run_at": pt.format_local(run_at),
    }


def main() -> None:
    print("=== CONTINUOUS PAPER RISK MONITOR V1 ===")
    print("PAPER TRADING ONLY")
    print("Δεν ανοίγει νέες θέσεις.")
    print(f"Risk heartbeat: κάθε {MONITOR_SECONDS} δευτερόλεπτα")
    print("Ctrl+C για ασφαλή διακοπή.")
    print()

    try:
        with state_store.monitor_instance_guard(pt.STATE_DIR, "paper_risk_monitor"):
            while True:
                try:
                    result = run_risk_cycle()

                    print(
                        f"[{result['run_at']}] "
                        f"positions={result['checked_positions']} "
                        f"actions={result['actions']} "
                        f"alerts={result['alerts']} "
                        f"skipped_exits={result['skipped_exits']}"
                    )

                except Exception as exc:
                    print(
                        "[RISK MONITOR ERROR] "
                        f"{type(exc).__name__}: {exc}"
                    )

                time.sleep(MONITOR_SECONDS)

    except KeyboardInterrupt:
        print()
        print("Continuous Paper Risk Monitor stopped safely.")
    except state_store.DuplicateMonitorError as exc:
        raise SystemExit(f"FAIL-CLOSED: {exc}") from exc


if __name__ == "__main__":
    main()
