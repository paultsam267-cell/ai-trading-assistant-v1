"""Continuous multi-asset opportunity orchestration for PAPER TRADING ONLY.

This process scans and may open simulated positions when explicitly enabled.
It never evaluates or executes exits; ``paper_risk_monitor.py`` owns exits.
There is deliberately no broker, exchange-order, or real-trade adapter here.
"""

from __future__ import annotations

import argparse
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

import market_scanner
import paper_trader as pt
import scanner as crypto_scanner
import state_store


PAPER_ONLY = True
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
CRYPTO_ENTRY_THRESHOLD = pt.MIN_ENTRY_SCORE
MARKET_ENTRY_THRESHOLD = pt.MARKET_MIN_ENTRY_SCORE


def normalize_crypto_score(raw_score: Any) -> float:
    """Map the legacy unbounded crypto score monotonically into 0..100.

    The raw score remains available to the existing crypto entry gates.  This
    saturating transform is used only for cross-asset comparison.
    """
    raw = max(pt.safe_float(raw_score), 0.0)
    normalized = 100.0 * (1.0 - math.exp(-raw / 100.0))
    return round(min(max(normalized, 0.0), 100.0), 6)


def with_normalized_scores(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a serializable candidate with explicit raw and normalized scores."""
    row = dict(candidate)
    asset_class = str(row.get("asset_class") or "CRYPTO").upper()
    raw_score = pt.safe_float(row.get("raw_score"), pt.safe_float(row.get("score")))
    if asset_class == "CRYPTO":
        normalized_score = normalize_crypto_score(raw_score)
    else:
        market_score = pt.safe_float(row.get("normalized_score"), raw_score)
        normalized_score = round(min(max(market_score, 0.0), 100.0), 6)
    row["raw_score"] = round(raw_score, 6)
    row["normalized_score"] = normalized_score
    return row


def scan_crypto_candidates() -> List[Dict[str, Any]]:
    """Run the existing crypto scanner pipeline without sending its report."""
    watchlist = crypto_scanner.build_token_watchlist()
    if not watchlist:
        raise RuntimeError("crypto scanner returned no token watchlist")
    raw_pairs: List[Dict[str, Any]] = []
    for chain, tokens in watchlist.items():
        if tokens:
            raw_pairs.extend(crypto_scanner.fetch_pairs_for_chain(chain, tokens))
    if not raw_pairs:
        raise RuntimeError("crypto scanner returned no pairs")
    best_pairs = crypto_scanner.select_best_pairs(raw_pairs)
    candidates = [pair for pair in best_pairs if crypto_scanner.is_candidate(pair)]
    ranked = crypto_scanner.prioritize_report_mix(candidates)
    normalized: List[Dict[str, Any]] = []
    timestamp = pt.now_local().isoformat()
    for pair in ranked:
        base = pair.get("baseToken", {}) if isinstance(pair.get("baseToken"), dict) else {}
        chain = str(pair.get("chainId") or "").lower()
        dex = str(pair.get("dexId") or "").lower()
        token_address = str(base.get("address") or "").strip()
        pair_address = str(pair.get("pairAddress") or "").strip()
        symbol = str(base.get("symbol") or "?").upper()
        raw_score = crypto_scanner.score_pair(pair)
        normalized.append(
            {
                "asset_class": "CRYPTO",
                "instrument_id": f"CRYPTO:{chain}:{token_address or pair_address or symbol}",
                "symbol": symbol,
                "name": str(base.get("name") or symbol),
                "signal": crypto_scanner.classify_candidate(pair),
                "score": raw_score,
                "raw_score": raw_score,
                "normalized_score": normalize_crypto_score(raw_score),
                "price_usd": pt.safe_float(pair.get("priceUsd")),
                "change_h24": pt.safe_float((pair.get("priceChange") or {}).get("h24")),
                "source": "DexScreener",
                "currency": "USD",
                "exchange": dex,
                "market_data_timestamp": timestamp,
                "chain": chain,
                "dex": dex,
                "token_address": token_address,
                "pair_address": pair_address,
                "liquidity_usd": pt.safe_float((pair.get("liquidity") or {}).get("usd")),
                "volume_h24": pt.safe_float((pair.get("volume") or {}).get("h24")),
                "txns": pair.get("txns", {}) if isinstance(pair.get("txns"), dict) else {},
                "url": str(pair.get("url") or ""),
                "paper_tradable": True,
                "analysis_only": False,
            }
        )
    return normalized


def cross_asset_rank(candidate: Mapping[str, Any]) -> float:
    """Return only the bounded score used by the common cross-asset ranking."""
    return with_normalized_scores(candidate)["normalized_score"]


def rank_candidates(candidates: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for candidate in candidates:
        row = with_normalized_scores(candidate)
        row["cross_asset_rank"] = row["normalized_score"]
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            row["normalized_score"],
            str(row.get("instrument_id") or row.get("symbol") or ""),
        ),
        reverse=True,
    )


def run_opportunity_cycle(
    *,
    allow_entries: bool = False,
    notify_failures: bool = False,
    run_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run one isolated scan cycle; no exit function is called or imported."""
    at = run_at or pt.now_local()
    failures: List[str] = []
    crypto_candidates: List[Dict[str, Any]] = []
    market_candidates: List[Dict[str, Any]] = []

    try:
        crypto_candidates = scan_crypto_candidates()
    except Exception as exc:
        failures.append(f"CRYPTO_SCANNER_FAILURE {type(exc).__name__}: {exc}")

    try:
        market_candidates = market_scanner.scan_market(now=at)
    except Exception as exc:
        failures.append(f"MARKET_SCANNER_FAILURE {type(exc).__name__}: {exc}")

    ranked = rank_candidates([*crypto_candidates, *market_candidates])
    entry_messages: List[str] = []

    if allow_entries:
        intelligence = pt.load_latest_intelligence()
        with pt.state_transaction():
            # Re-read under the common lock.  Entry gates are therefore checked
            # against the newest cash, exposure and duplicate-position state.
            account = pt.load_account()
            positions = pt.normalize_positions(pt.load_json_file(pt.POSITIONS_FILE, {}))
            trades = pt.load_json_file(pt.TRADES_FILE, [])
            if not isinstance(trades, list):
                trades = []

            for candidate in ranked:
                if candidate.get("cross_asset_rank", -1.0) < 0:
                    continue
                message = pt.open_demo_position(
                    account,
                    positions,
                    trades,
                    candidate,
                    at,
                    intelligence,
                )
                if message:
                    entry_messages.append(message)

            if entry_messages:
                account["last_update"] = pt.format_local(at)
                pt.save_json_file(pt.POSITIONS_FILE, positions)
                pt.save_json_file(pt.TRADES_FILE, trades)
                pt.save_json_file(pt.ACCOUNT_FILE, account)

        # Telegram/console I/O is intentionally outside the portfolio lock.
        for message in entry_messages:
            pt.send_console_message(message)

    if notify_failures:
        for failure in failures:
            pt.send_console_message(f"PAPER OPPORTUNITY MONITOR\n{failure}")

    return {
        "mode": "PAPER_ENTRY_ENABLED" if allow_entries else "READ_ONLY_NO_ENTRY",
        "crypto_candidates": len(crypto_candidates),
        "market_candidates": len(market_candidates),
        "ranked_candidates": len(ranked),
        "paper_entries": len(entry_messages),
        "failures": failures,
        "exits_executed": 0,
        "real_trades_executed": 0,
        "run_at": pt.format_local(at),
    }


def continuous_monitor(*, interval: int, allow_entries: bool) -> None:
    seconds = max(MIN_INTERVAL_SECONDS, int(interval))
    mode = "PAPER ENTRY ENABLED" if allow_entries else "READ-ONLY / NO-ENTRY"
    print("=== MULTI-ASSET PAPER OPPORTUNITY MONITOR V1 ===")
    print("PAPER TRADING ONLY - real order execution is not implemented")
    print(f"Mode: {mode} | interval={seconds}s")
    print("Exits remain the sole responsibility of paper_risk_monitor.py")
    print("Ctrl+C for graceful shutdown.")
    try:
        while True:
            try:
                result = run_opportunity_cycle(
                    allow_entries=allow_entries,
                    notify_failures=allow_entries,
                )
                print(
                    f"[{result['run_at']}] crypto={result['crypto_candidates']} "
                    f"market={result['market_candidates']} entries={result['paper_entries']} "
                    f"failures={len(result['failures'])}"
                )
            except Exception as exc:
                print(f"[OPPORTUNITY CYCLE ERROR] {type(exc).__name__}: {exc}")
            time.sleep(seconds)
    except KeyboardInterrupt:
        print("\nPaper opportunity monitor stopped safely.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-asset PAPER opportunity monitor")
    parser.add_argument("--once", action="store_true", help="Run one cycle and stop")
    parser.add_argument(
        "--enable-paper-entries",
        action="store_true",
        help="Explicitly allow simulated entries; real trading remains impossible",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("PAPER_OPPORTUNITY_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))),
    )
    args = parser.parse_args()
    try:
        with state_store.monitor_instance_guard(pt.STATE_DIR, "paper_opportunity_monitor"):
            if args.once:
                print(run_opportunity_cycle(allow_entries=args.enable_paper_entries, notify_failures=False))
                return
            continuous_monitor(interval=args.interval, allow_entries=args.enable_paper_entries)
    except state_store.DuplicateMonitorError as exc:
        raise SystemExit(f"FAIL-CLOSED: {exc}") from exc


if __name__ == "__main__":
    main()
