from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

from archive_utils import resolve_project_path
from intelligence_radar import (
    HISTORY_FILE, LATEST_FILE, PionexPublicClient, analyze_pionex_symbol, analyze_whales,
    append_observation, atomic_json, collect_whale_events, fuse_chain_regimes,
    infer_leaderboard, load_config, load_json, safe_float,
)

TRADES_FILE = resolve_project_path("PAPER_STATE_DIR", "paper_state") / "paper_trade_log.json"
LEADERBOARD_FILE = resolve_project_path("PIONEX_LEADERBOARD_FILE", "pionex_leaderboard_snapshots.json")


def main() -> int:
    config = load_config()
    p_cfg = config.get("pionex", {})
    previous = load_json(HISTORY_FILE, {})
    previous_oi = previous.get("open_interest", {}) if isinstance(previous, dict) else {}
    pionex_result: Dict[str, Any] = {"status": "DISABLED", "symbols": {}, "errors": []}
    current_oi: Dict[str, float] = {}

    if p_cfg.get("enabled", True):
        client = PionexPublicClient(timeout=int(safe_float(p_cfg.get("request_timeout"), 15)))
        try:
            current_oi = client.all_open_interests()
            pionex_result["status"] = "OK"
        except Exception as exc:
            pionex_result["status"] = "PARTIAL"
            pionex_result["errors"].append(f"open_interest: {exc}")
        for symbol in p_cfg.get("symbols", []):
            try:
                analysis = analyze_pionex_symbol(
                    str(symbol),
                    trades=client.trades(str(symbol), int(safe_float(p_cfg.get("trade_limit"), 120))),
                    depth=client.depth(str(symbol), int(safe_float(p_cfg.get("depth_limit"), 20))),
                    ticker=client.ticker(str(symbol)),
                    index=client.index(str(symbol)),
                    open_interest=safe_float(current_oi.get(str(symbol))),
                    previous_open_interest=safe_float(previous_oi.get(str(symbol))),
                )
                pionex_result["symbols"][str(symbol)] = analysis
            except Exception as exc:
                pionex_result["errors"].append(f"{symbol}: {exc}")
        if not pionex_result["symbols"]:
            pionex_result["status"] = "UNAVAILABLE"

    atomic_json(HISTORY_FILE, {"open_interest": current_oi})
    leaderboard = infer_leaderboard(load_json(LEADERBOARD_FILE, {"snapshots": []}))
    whale_events, whale_provider_status = collect_whale_events(config)
    whales = analyze_whales(whale_events)
    whales["provider_status"] = whale_provider_status
    fusion = {"by_chain": fuse_chain_regimes(pionex_result, whales, config)}
    payload: Dict[str, Any] = {
        "generated_at": __import__("intelligence_radar").iso_now(),
        "mode": "READ_ONLY_PAPER_INTELLIGENCE",
        "pionex": pionex_result,
        "leaderboard": leaderboard,
        "whales": whales,
        "fusion": fusion,
    }
    trades = load_json(TRADES_FILE, [])
    closed = len([t for t in trades if isinstance(t, dict) and str(t.get("type", "")).upper() in {"SELL", "SHORT_CLOSE"}]) if isinstance(trades, list) else 0
    payload["evaluation"] = append_observation(payload, closed, config)
    atomic_json(LATEST_FILE, payload)

    print("INTELLIGENCE RADAR • PAPER ONLY")
    print(f"Pionex status: {pionex_result['status']}")
    for chain, item in fusion["by_chain"].items():
        print(f"{chain}: {item['direction']} | confidence {item['confidence']:.1f} | state {item['state']}")
    print(f"Whale events: {whales['event_count']}")
    print(f"Observation: {payload['evaluation']['observed_days']}/{payload['evaluation']['minimum_days']} days | closed {payload['evaluation']['closed_positions']}/{payload['evaluation']['minimum_closed_positions']}")
    print("Καμία πραγματική εντολή ή συναλλαγή δεν εκτελέστηκε.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
