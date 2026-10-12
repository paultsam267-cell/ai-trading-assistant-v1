"""Fail-closed GitHub/self-hosted read-only reporting entry point.

This module never imports or calls entry/exit monitors.  It refuses checkout
state because a hosted runner workspace is ephemeral and not authoritative.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


EXPECTED_SIMULATION_ID = "sim-20261010163203-cc77cf"
EXPECTED_INITIAL_CAPITAL = 1000.0


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_authoritative_state() -> Path:
    raw = os.getenv("PAPER_STATE_DIR", "").strip()
    if not raw:
        raise RuntimeError("PAPER_STATE_DIR is required; ephemeral fallback is forbidden")
    state_dir = Path(raw).expanduser().resolve()
    if not state_dir.is_absolute() or not state_dir.is_dir():
        raise RuntimeError("PAPER_STATE_DIR must be an existing persistent absolute directory")

    workspace_raw = os.getenv("GITHUB_WORKSPACE", "").strip()
    if workspace_raw and _is_within(state_dir, Path(workspace_raw).resolve()):
        raise RuntimeError("GitHub checkout state is ephemeral and cannot be authoritative")

    account_path = state_dir / "paper_account.json"
    try:
        account = json.loads(account_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("authoritative paper_account.json is unavailable or invalid") from exc
    if account.get("simulation_id") != EXPECTED_SIMULATION_ID:
        raise RuntimeError("simulation ID differs from the verified checkpoint")
    if float(account.get("initial_capital", 0.0)) != EXPECTED_INITIAL_CAPITAL:
        raise RuntimeError("initial capital differs from the verified €1,000 checkpoint")
    return state_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only authoritative PAPER portfolio report")
    parser.add_argument("--send", action="store_true", help="Send only after bot and chat routing validation")
    args = parser.parse_args()
    validate_authoritative_state()

    # Import only after PAPER_STATE_DIR has passed validation.
    import portfolio_report
    import telegram_reporting

    report = portfolio_report.build_report()
    if not args.send:
        print(report)
        return

    routing = telegram_reporting.routing_status(perform_network_check=True)
    if not routing["bot_identity_verified"] or not routing["chat_routing_verified"]:
        raise RuntimeError("Telegram routing validation failed; no message was sent")
    if not telegram_reporting.send_notification(report):
        raise RuntimeError("Telegram credentials are unavailable; no message was sent")
    print("READ_ONLY_PAPER_REPORT_SENT")


if __name__ == "__main__":
    main()
