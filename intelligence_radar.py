"""Read-only intelligence radars for the paper-trading assistant.

The module combines:
- Pionex public futures market data (trades, depth, ticker, funding, OI)
- optional manual Pionex leaderboard snapshots for early-bot/rollover learning
- optional whale watchlists (Helius for Solana, Etherscan V2/BscScan-compatible API for BSC)
- dynamic paper allocation from 0% to 5% of active capital

It never submits orders and never needs a wallet private key.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests

from archive_utils import resolve_project_path

ATHENS_TZ = ZoneInfo("Europe/Athens")
STATE_DIR = resolve_project_path("INTELLIGENCE_STATE_DIR", "intelligence_state")
LATEST_FILE = STATE_DIR / "latest_intelligence.json"
HISTORY_FILE = STATE_DIR / "pionex_market_history.json"
OBSERVATION_FILE = STATE_DIR / "observation_log.jsonl"
CONFIG_FILE = resolve_project_path("INTELLIGENCE_CONFIG_FILE", "intelligence_config.json")
LEADERBOARD_FILE = resolve_project_path("PIONEX_LEADERBOARD_FILE", "pionex_leaderboard_snapshots.json")
WHALE_WATCHLIST_FILE = resolve_project_path("WHALE_WATCHLIST_FILE", "whale_watchlist.json")
WHALE_MANUAL_EVENTS_FILE = resolve_project_path("WHALE_MANUAL_EVENTS_FILE", "whale_events.json")
PIONEX_BASE = "https://api.pionex.com"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def now_athens() -> datetime:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ)


def iso_now() -> str:
    return now_athens().isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def default_config() -> Dict[str, Any]:
    return {
        "pionex": {
            "enabled": True,
            "symbols": ["BTC_USDT_PERP", "ETH_USDT_PERP", "SOL_USDT_PERP", "BNB_USDT_PERP"],
            "chain_symbol_map": {
                "solana": "SOL_USDT_PERP",
                "bsc": "BNB_USDT_PERP",
                "global": "BTC_USDT_PERP",
            },
            "trade_limit": 120,
            "depth_limit": 20,
            "request_timeout": 15,
        },
        "allocation": {
            "mode": "DYNAMIC_PERCENT",
            "early_pct": 1.0,
            "normal_pct": 2.5,
            "strong_pct": 4.0,
            "maximum_pct": 5.0,
            "max_total_exposure_pct": 20.0,
            "fixed_early_usd": 2.0,
            "fixed_normal_usd": 5.0,
            "fixed_strong_usd": 10.0,
            "fixed_maximum_usd": 15.0,
        },
        "evaluation": {"minimum_days": 30, "minimum_closed_positions": 50},
        "whales": {"enabled": True, "lookback_hours": 24, "max_transactions_per_wallet": 30},
    }


def load_config() -> Dict[str, Any]:
    cfg = default_config()
    raw = load_json(CONFIG_FILE, {})
    if not isinstance(raw, dict):
        return cfg
    for section, values in raw.items():
        if isinstance(values, dict) and isinstance(cfg.get(section), dict):
            cfg[section].update(values)
        else:
            cfg[section] = values
    return cfg


class PionexPublicClient:
    """Tiny client for documented public Pionex Futures market endpoints."""

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 15):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.update({"User-Agent": "ai-trading-paper-intelligence/1.0"})

    def _get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        response = self.session.get(PIONEX_BASE + path, params=dict(params or {}), timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("result") is False:
            raise RuntimeError(str(payload.get("message") if isinstance(payload, dict) else "Invalid response"))
        data = payload.get("data", {})
        return data if isinstance(data, dict) else {}

    def trades(self, symbol: str, limit: int) -> List[Dict[str, Any]]:
        data = self._get("/api/v1/market/trades", {"symbol": symbol, "limit": limit})
        rows = data.get("trades", [])
        return rows if isinstance(rows, list) else []

    def depth(self, symbol: str, limit: int) -> Dict[str, Any]:
        return self._get("/api/v1/market/depth", {"symbol": symbol, "limit": limit})

    def ticker(self, symbol: str) -> Dict[str, Any]:
        data = self._get("/api/v1/market/tickers", {"symbol": symbol})
        rows = data.get("tickers", [])
        return rows[0] if isinstance(rows, list) and rows else {}

    def index(self, symbol: str) -> Dict[str, Any]:
        data = self._get("/api/v1/market/indexes", {"symbol": symbol})
        rows = data.get("indexes", [])
        return rows[0] if isinstance(rows, list) and rows else {}

    def all_open_interests(self) -> Dict[str, float]:
        data = self._get("/api/v1/market/openInterests")
        rows = data.get("openInterests", [])
        result: Dict[str, float] = {}
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    result[str(row.get("symbol", ""))] = safe_float(row.get("openInterest"))
        return result


def _notional_levels(levels: Any, limit: int = 20) -> float:
    total = 0.0
    if not isinstance(levels, list):
        return total
    for row in levels[:limit]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            total += safe_float(row[0]) * safe_float(row[1])
    return total


def analyze_pionex_symbol(
    symbol: str,
    *,
    trades: Sequence[Mapping[str, Any]],
    depth: Mapping[str, Any],
    ticker: Mapping[str, Any],
    index: Mapping[str, Any],
    open_interest: float,
    previous_open_interest: float,
) -> Dict[str, Any]:
    buy_notional = 0.0
    sell_notional = 0.0
    for trade in trades:
        notional = safe_float(trade.get("price")) * safe_float(trade.get("size"))
        if str(trade.get("side", "")).upper() == "BUY":
            buy_notional += notional
        elif str(trade.get("side", "")).upper() == "SELL":
            sell_notional += notional
    flow_total = buy_notional + sell_notional
    taker_imbalance = (buy_notional - sell_notional) / flow_total if flow_total > 0 else 0.0

    bid_notional = _notional_levels(depth.get("bids"))
    ask_notional = _notional_levels(depth.get("asks"))
    book_total = bid_notional + ask_notional
    book_imbalance = (bid_notional - ask_notional) / book_total if book_total > 0 else 0.0

    open_price = safe_float(ticker.get("open"))
    close_price = safe_float(ticker.get("close"))
    price_change_pct = ((close_price / open_price) - 1.0) * 100.0 if open_price > 0 else 0.0
    funding = safe_float(index.get("nextFundingRate"))
    oi_change_pct = (
        ((open_interest / previous_open_interest) - 1.0) * 100.0
        if previous_open_interest > 0 and open_interest > 0
        else 0.0
    )

    directional_score = (
        taker_imbalance * 42.0
        + book_imbalance * 23.0
        + clamp(price_change_pct / 4.0, -1.0, 1.0) * 20.0
        + clamp(oi_change_pct / 8.0, -1.0, 1.0) * (15.0 if price_change_pct >= 0 else -15.0)
    )
    directional_score = clamp(directional_score, -100.0, 100.0)

    # Crowding comes mainly from one-sided funding plus expanding open interest.
    # A squeeze warning appears when price/taker flow starts moving against that crowded side.
    long_crowding = funding >= 0.0008 and oi_change_pct > 2
    short_crowding = funding <= -0.0008 and oi_change_pct > 2
    short_squeeze_risk = short_crowding and price_change_pct > 0 and taker_imbalance > 0.08
    long_squeeze_risk = long_crowding and price_change_pct < 0 and taker_imbalance < -0.08

    if short_squeeze_risk:
        state = "SHORT_SQUEEZE_RISK"
    elif long_squeeze_risk:
        state = "LONG_SQUEEZE_RISK"
    elif long_crowding:
        state = "LONG_CROWDING"
    elif short_crowding:
        state = "SHORT_CROWDING"
    elif directional_score >= 25:
        state = "LONG_FLOW"
    elif directional_score <= -25:
        state = "SHORT_FLOW"
    else:
        state = "NEUTRAL"

    confidence = clamp(abs(directional_score) + min(abs(oi_change_pct), 10.0) * 1.5, 0.0, 100.0)
    block_long = state in {"LONG_SQUEEZE_RISK", "SHORT_FLOW"}
    block_short = state in {"SHORT_SQUEEZE_RISK", "LONG_FLOW"}
    return {
        "symbol": symbol,
        "state": state,
        "direction": "LONG" if directional_score > 12 else "SHORT" if directional_score < -12 else "NEUTRAL",
        "score": round(directional_score, 2),
        "confidence": round(confidence, 2),
        "taker_buy_notional": round(buy_notional, 4),
        "taker_sell_notional": round(sell_notional, 4),
        "taker_imbalance": round(taker_imbalance, 6),
        "book_imbalance": round(book_imbalance, 6),
        "price_change_pct": round(price_change_pct, 4),
        "open_interest": round(open_interest, 8),
        "open_interest_change_pct": round(oi_change_pct, 4),
        "next_funding_rate": funding,
        "block_long": block_long,
        "block_short": block_short,
    }


def infer_leaderboard(snapshot_payload: Any) -> Dict[str, Any]:
    snapshots = snapshot_payload.get("snapshots", []) if isinstance(snapshot_payload, dict) else []
    valid = [s for s in snapshots if isinstance(s, dict) and isinstance(s.get("bots"), list)]
    if not valid:
        return {"status": "NO_SNAPSHOTS", "rollover_detected": False, "bots": []}
    valid.sort(key=lambda s: str(s.get("captured_at", "")))
    latest = valid[-1]
    previous = valid[-2] if len(valid) >= 2 else None
    latest_ids = {str(b.get("bot_id") or f"{b.get('pair')}:{b.get('direction')}:{b.get('rank')}") for b in latest["bots"] if isinstance(b, dict)}
    prev_ids = {
        str(b.get("bot_id") or f"{b.get('pair')}:{b.get('direction')}:{b.get('rank')}")
        for b in (previous.get("bots", []) if previous else []) if isinstance(b, dict)
    }
    replacement = 1.0 - (len(latest_ids & prev_ids) / max(len(latest_ids | prev_ids), 1)) if previous else 0.0
    latest_durations = [safe_float(b.get("lasting_minutes"), 99999) for b in latest["bots"] if isinstance(b, dict)]
    prev_durations = [safe_float(b.get("lasting_minutes"), 99999) for b in previous.get("bots", []) if isinstance(b, dict)] if previous else []
    median_latest = median(latest_durations) if latest_durations else 99999.0
    median_prev = median(prev_durations) if prev_durations else 99999.0
    date_changed = bool(previous and str(previous.get("captured_at", ""))[:10] != str(latest.get("captured_at", ""))[:10])
    rollover = bool(previous and ((replacement >= 0.6 and median_latest <= 90) or (date_changed and median_latest < median_prev * 0.5)))

    prev_by_id = {
        str(b.get("bot_id") or f"{b.get('pair')}:{b.get('direction')}:{b.get('rank')}"): b
        for b in (previous.get("bots", []) if previous else []) if isinstance(b, dict)
    }
    bots: List[Dict[str, Any]] = []
    for bot in latest["bots"]:
        if not isinstance(bot, dict):
            continue
        bot_id = str(bot.get("bot_id") or f"{bot.get('pair')}:{bot.get('direction')}:{bot.get('rank')}")
        old = prev_by_id.get(bot_id, {})
        rank = int(safe_float(bot.get("rank"), 999))
        old_rank = int(safe_float(old.get("rank"), rank))
        profit = safe_float(bot.get("profit_pct"))
        old_profit = safe_float(old.get("profit_pct"), profit)
        lasting = safe_float(bot.get("lasting_minutes"))
        leverage = safe_float(bot.get("leverage"), 1.0)
        early_score = 0.0
        early_score += clamp((90.0 - lasting) / 90.0, 0.0, 1.0) * 35.0
        early_score += clamp((old_rank - rank) / 20.0, -1.0, 1.0) * 25.0
        early_score += clamp((profit - old_profit) / 30.0, -1.0, 1.0) * 25.0
        early_score -= clamp((leverage - 10.0) / 90.0, 0.0, 1.0) * 25.0
        bots.append({**bot, "bot_id": bot_id, "rank_velocity": old_rank - rank, "profit_velocity": round(profit - old_profit, 4), "early_score": round(clamp(early_score, 0, 100), 2)})
    bots.sort(key=lambda b: safe_float(b.get("early_score")), reverse=True)
    return {
        "status": "OK",
        "captured_at": latest.get("captured_at"),
        "rollover_detected": rollover,
        "replacement_ratio": round(replacement, 4),
        "median_lasting_minutes": round(median_latest, 2),
        "bots": bots[:10],
        "note": "Το δημόσιο leaderboard εισάγεται μόνο ως snapshot. Δεν χρησιμοποιείται μη τεκμηριωμένο scraping.",
    }


def _manual_whale_events() -> List[Dict[str, Any]]:
    payload = load_json(WHALE_MANUAL_EVENTS_FILE, [])
    return payload if isinstance(payload, list) else []


def _helius_events(entry: Mapping[str, Any], api_key: str, timeout: int, limit: int) -> List[Dict[str, Any]]:
    address = str(entry.get("address", "")).strip()
    if not address:
        return []
    url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
    response = requests.get(url, params={"api-key": api_key, "limit": limit}, timeout=timeout)
    response.raise_for_status()
    rows = response.json()
    events: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return events
    for tx in rows:
        if not isinstance(tx, dict):
            continue
        timestamp = tx.get("timestamp")
        for transfer in tx.get("tokenTransfers", []) or []:
            if not isinstance(transfer, dict):
                continue
            direction = None
            if str(transfer.get("toUserAccount", "")) == address:
                direction = "BUY"
            elif str(transfer.get("fromUserAccount", "")) == address:
                direction = "SELL"
            if direction:
                events.append({
                    "chain": "solana", "wallet": address, "label": entry.get("label", ""),
                    "token": str(transfer.get("mint", "")), "direction": direction,
                    "amount": safe_float(transfer.get("tokenAmount")), "timestamp": timestamp,
                    "weight": safe_float(entry.get("weight"), 1.0), "source": "HELIUS",
                })
    return events


def _bscscan_events(entry: Mapping[str, Any], api_key: str, timeout: int, limit: int) -> List[Dict[str, Any]]:
    address = str(entry.get("address", "")).strip().lower()
    if not address:
        return []
    response = requests.get(
        "https://api.etherscan.io/v2/api",
        params={"chainid": "56", "module": "account", "action": "tokentx", "address": address, "page": 1, "offset": limit, "sort": "desc", "apikey": api_key},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("result", []) if isinstance(payload, dict) else []
    events: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return events
    for tx in rows:
        if not isinstance(tx, dict):
            continue
        decimals = int(safe_float(tx.get("tokenDecimal"), 0))
        amount = safe_float(tx.get("value")) / (10 ** decimals if decimals >= 0 else 1)
        to_addr = str(tx.get("to", "")).lower()
        from_addr = str(tx.get("from", "")).lower()
        direction = "BUY" if to_addr == address else "SELL" if from_addr == address else None
        if direction:
            events.append({
                "chain": "bsc", "wallet": address, "label": entry.get("label", ""),
                "token": str(tx.get("contractAddress", "")).lower(), "symbol": tx.get("tokenSymbol", ""),
                "direction": direction, "amount": amount, "timestamp": safe_float(tx.get("timeStamp")),
                "weight": safe_float(entry.get("weight"), 1.0), "source": "ETHERSCAN_V2_BSC",
            })
    return events


def collect_whale_events(config: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    events = list(_manual_whale_events())
    watchlist = load_json(WHALE_WATCHLIST_FILE, {"wallets": []})
    wallets = watchlist.get("wallets", []) if isinstance(watchlist, dict) else []
    status = {"manual_events": len(events), "wallets": len(wallets), "solana_provider": "NOT_CONFIGURED", "bsc_provider": "NOT_CONFIGURED", "errors": []}
    max_tx = int(safe_float(config.get("whales", {}).get("max_transactions_per_wallet"), 30))
    timeout = int(safe_float(config.get("pionex", {}).get("request_timeout"), 15))
    helius_key = os.getenv("HELIUS_API_KEY", "").strip()
    bsc_key = os.getenv("BSCSCAN_API_KEY", "").strip()
    for entry in wallets:
        if not isinstance(entry, dict) or entry.get("enabled", True) is False:
            continue
        chain = str(entry.get("chain", "")).lower()
        try:
            if chain == "solana" and helius_key:
                events.extend(_helius_events(entry, helius_key, timeout, max_tx))
                status["solana_provider"] = "HELIUS_OK"
            elif chain == "bsc" and bsc_key:
                events.extend(_bscscan_events(entry, bsc_key, timeout, max_tx))
                status["bsc_provider"] = "BSC_API_OK"
        except Exception as exc:  # provider failure must never stop paper trading
            status["errors"].append(f"{chain}:{entry.get('label', entry.get('address', ''))}: {exc}")
    return events, status


def analyze_whales(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_chain: Dict[str, float] = {}
    by_token: Dict[str, Dict[str, Any]] = {}
    for event in events:
        chain = str(event.get("chain", "")).lower()
        token = str(event.get("token") or event.get("symbol") or "").lower()
        weight = clamp(safe_float(event.get("weight"), 1.0), 0.1, 5.0)
        direction = str(event.get("direction", "")).upper()
        sign = 1.0 if direction in {"BUY", "ACCUMULATE", "INFLOW"} else -1.0 if direction in {"SELL", "DISTRIBUTE", "OUTFLOW"} else 0.0
        if not sign:
            continue
        by_chain[chain] = by_chain.get(chain, 0.0) + sign * weight
        key = f"{chain}:{token}"
        item = by_token.setdefault(key, {"chain": chain, "token": token, "score_raw": 0.0, "events": 0})
        item["score_raw"] += sign * weight
        item["events"] += 1
    chain_result = {chain: {"score": round(clamp(score / 5.0, -1.0, 1.0), 4), "direction": "LONG" if score > 0.5 else "SHORT" if score < -0.5 else "NEUTRAL"} for chain, score in by_chain.items()}
    token_result: Dict[str, Any] = {}
    for key, item in by_token.items():
        score = clamp(safe_float(item["score_raw"]) / max(safe_float(item["events"]), 1.0), -1.0, 1.0)
        token_result[key] = {**item, "score": round(score, 4), "direction": "LONG" if score > 0.15 else "SHORT" if score < -0.15 else "NEUTRAL"}
    return {"by_chain": chain_result, "by_token": token_result, "event_count": len(events)}


def fuse_chain_regimes(pionex: Mapping[str, Any], whales: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    symbol_map = config.get("pionex", {}).get("chain_symbol_map", {})
    symbols = pionex.get("symbols", {}) if isinstance(pionex, dict) else {}
    whale_chains = whales.get("by_chain", {}) if isinstance(whales, dict) else {}
    result: Dict[str, Any] = {}
    for chain in {"solana", "bsc"}:
        symbol = str(symbol_map.get(chain, symbol_map.get("global", "BTC_USDT_PERP")))
        market = symbols.get(symbol, {}) if isinstance(symbols, dict) else {}
        market_score = safe_float(market.get("score"))
        market_conf = safe_float(market.get("confidence"))
        whale = whale_chains.get(chain, {}) if isinstance(whale_chains, dict) else {}
        whale_score = safe_float(whale.get("score"))
        combined = clamp(market_score * 0.8 + whale_score * 20.0, -100.0, 100.0)
        direction = "LONG" if combined > 15 else "SHORT" if combined < -15 else "NEUTRAL"
        confidence = clamp(market_conf * 0.8 + abs(whale_score) * 20.0, 0.0, 100.0)
        result[chain] = {
            "symbol": symbol,
            "state": market.get("state", "UNAVAILABLE"),
            "direction": direction,
            "confidence": round(confidence, 2),
            "combined_score": round(combined, 2),
            "whale_score": round(whale_score, 4),
            "block_long": bool(market.get("block_long")),
            "block_short": bool(market.get("block_short")),
        }
    return result


def load_latest_intelligence() -> Dict[str, Any]:
    payload = load_json(LATEST_FILE, {})
    return payload if isinstance(payload, dict) else {}


def allocation_decision(item: Mapping[str, Any], account: Mapping[str, Any], intelligence: Optional[Mapping[str, Any]] = None, positions: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    cfg = load_config().get("allocation", {})
    mode = str(cfg.get("mode", "DYNAMIC_PERCENT")).upper()
    initial = max(safe_float(account.get("initial_capital"), 1000.0), 1.0)
    cash = max(safe_float(account.get("cash")), 0.0)
    base_pct = safe_float(account.get("position_size_pct"), safe_float(cfg.get("normal_pct"), 2.5))
    signal = str(item.get("signal", "LONG")).upper()
    side = "SHORT" if signal in {"SHORT", "SHORT_WATCH"} else "LONG"
    chain = str(item.get("chain", "")).lower()
    intel = intelligence or {}
    regime = ((intel.get("fusion") or {}).get("by_chain") or {}).get(chain, {}) if isinstance(intel, Mapping) else {}
    conf = safe_float(regime.get("confidence"))
    direction = str(regime.get("direction", "NEUTRAL")).upper()
    blocked = bool(regime.get("block_short" if side == "SHORT" else "block_long"))
    aligned = direction == side
    conflicting = direction not in {"NEUTRAL", side, ""}
    whale_score = safe_float(regime.get("whale_score"))
    whale_aligned = whale_score > 0.15 if side == "LONG" else whale_score < -0.15

    if not regime:
        pct = base_pct
        level = "BASE_NO_RADAR"
        blocked = False
    elif blocked or (conf >= 65 and conflicting):
        pct = 0.0
        level = "BLOCKED_CONFLICT_OR_SQUEEZE"
    elif aligned and conf >= 88 and whale_aligned:
        pct = safe_float(cfg.get("maximum_pct"), 5.0)
        level = "MAXIMUM_CONFIRMATION"
    elif aligned and conf >= 75:
        pct = safe_float(cfg.get("strong_pct"), 4.0)
        level = "STRONG_CONFIRMATION"
    elif aligned and conf >= 55:
        pct = safe_float(cfg.get("normal_pct"), 2.5)
        level = "CONFIRMED"
    elif conf >= 40:
        pct = safe_float(cfg.get("early_pct"), 1.0)
        level = "EARLY_OR_MIXED"
    else:
        pct = base_pct
        level = "BASE_LOW_DATA"

    fixed_map = {
        "EARLY_OR_MIXED": safe_float(cfg.get("fixed_early_usd"), 2.0),
        "CONFIRMED": safe_float(cfg.get("fixed_normal_usd"), 5.0),
        "STRONG_CONFIRMATION": safe_float(cfg.get("fixed_strong_usd"), 10.0),
        "MAXIMUM_CONFIRMATION": safe_float(cfg.get("fixed_maximum_usd"), 15.0),
    }
    if pct <= 0:
        amount = 0.0
    elif mode == "FIXED_USD":
        amount = fixed_map.get(level, safe_float(cfg.get("fixed_normal_usd"), 5.0))
    else:
        amount = initial * pct / 100.0

    exposure = 0.0
    for pos in (positions or {}).values():
        if isinstance(pos, Mapping):
            exposure += max(safe_float(pos.get("remaining_cost_basis")), 0.0)
    exposure_cap = initial * safe_float(cfg.get("max_total_exposure_pct"), 20.0) / 100.0
    headroom = max(exposure_cap - exposure, 0.0)
    amount = min(max(amount, 0.0), cash, headroom)
    if amount <= 0 and pct > 0 and headroom <= 0:
        level = "BLOCKED_EXPOSURE_LIMIT"
    return {
        "side": side, "chain": chain, "level": level, "allocation_pct": round(pct, 4),
        "allocation_usd": round(amount, 8), "regime_direction": direction or "NEUTRAL",
        "regime_confidence": round(conf, 2), "whale_aligned": whale_aligned,
        "blocked": amount <= 0, "max_total_exposure_pct": safe_float(cfg.get("max_total_exposure_pct"), 20.0),
    }


def append_observation(payload: Mapping[str, Any], closed_positions: int, config: Mapping[str, Any]) -> Dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    date_key = now_athens().date().isoformat()
    record = {"date": date_key, "captured_at": iso_now(), "closed_positions": int(closed_positions), "fusion": payload.get("fusion", {})}
    with OBSERVATION_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    dates = set()
    max_closed = 0
    try:
        with OBSERVATION_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    dates.add(str(row.get("date", "")))
                    max_closed = max(max_closed, int(safe_float(row.get("closed_positions"))))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    min_days = int(safe_float(config.get("evaluation", {}).get("minimum_days"), 30))
    min_closed = int(safe_float(config.get("evaluation", {}).get("minimum_closed_positions"), 50))
    return {"observed_days": len([d for d in dates if d]), "minimum_days": min_days, "closed_positions": max_closed, "minimum_closed_positions": min_closed, "ready_for_shadow_review": len(dates) >= min_days and max_closed >= min_closed}
