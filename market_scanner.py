"""Yahoo-backed multi-asset market scanner for PAPER TRADING ONLY.

The module contains no broker integration and no order-execution code.  It
turns completed Yahoo hourly candles into a small, stable candidate contract.
All network and payload failures are fail-closed: no price is invented and no
candidate is returned when the latest candle is missing or stale.
"""

from __future__ import annotations

import math
import os
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import quote

import requests


YAHOO_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
REQUEST_TIMEOUT = int(os.getenv("MARKET_REQUEST_TIMEOUT", "20"))
YAHOO_RANGE = os.getenv("MARKET_YAHOO_RANGE", "1mo")
YAHOO_INTERVAL = os.getenv("MARKET_YAHOO_INTERVAL", "1h")
MIN_USABLE_HISTORY = max(24, int(os.getenv("MARKET_MIN_USABLE_HISTORY", "24")))

ALLOWED_ASSET_CLASSES = frozenset({"STOCK", "ETF", "METAL", "INDEX", "FX"})
TRADABLE_PAPER_ASSET_CLASSES = frozenset({"STOCK", "ETF", "METAL", "FX"})

# A single, centrally declared and easily extendable V1 universe.
MARKET_UNIVERSE: Sequence[Mapping[str, Any]] = (
    {"asset_class": "STOCK", "symbol": "AAPL", "name": "Apple Inc.", "paper_tradable": True},
    {"asset_class": "STOCK", "symbol": "MSFT", "name": "Microsoft Corp.", "paper_tradable": True},
    {"asset_class": "STOCK", "symbol": "NVDA", "name": "NVIDIA Corp.", "paper_tradable": True},
    {"asset_class": "STOCK", "symbol": "AMZN", "name": "Amazon.com Inc.", "paper_tradable": True},
    {"asset_class": "STOCK", "symbol": "META", "name": "Meta Platforms Inc.", "paper_tradable": True},
    {"asset_class": "STOCK", "symbol": "GOOGL", "name": "Alphabet Inc.", "paper_tradable": True},
    {"asset_class": "STOCK", "symbol": "TSLA", "name": "Tesla Inc.", "paper_tradable": True},
    {"asset_class": "ETF", "symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "paper_tradable": True},
    {"asset_class": "ETF", "symbol": "QQQ", "name": "Invesco QQQ Trust", "paper_tradable": True},
    {"asset_class": "ETF", "symbol": "DIA", "name": "SPDR Dow Jones Industrial Average ETF", "paper_tradable": True},
    {"asset_class": "ETF", "symbol": "IWM", "name": "iShares Russell 2000 ETF", "paper_tradable": True},
    {"asset_class": "METAL", "symbol": "GC=F", "name": "Gold Futures", "paper_tradable": True},
    {"asset_class": "METAL", "symbol": "SI=F", "name": "Silver Futures", "paper_tradable": True},
    {"asset_class": "INDEX", "symbol": "^GSPC", "name": "S&P 500 Index", "paper_tradable": False},
    {"asset_class": "INDEX", "symbol": "^NDX", "name": "NASDAQ 100 Index", "paper_tradable": False},
    {"asset_class": "INDEX", "symbol": "^DJI", "name": "Dow Jones Industrial Average", "paper_tradable": False},
    {"asset_class": "FX", "symbol": "EURUSD=X", "name": "EUR/USD", "paper_tradable": True},
    {"asset_class": "FX", "symbol": "GBPUSD=X", "name": "GBP/USD", "paper_tradable": True},
    {"asset_class": "FX", "symbol": "USDJPY=X", "name": "USD/JPY", "paper_tradable": True},
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def stable_instrument_id(symbol: str) -> str:
    return f"YAHOO:{str(symbol).strip().upper()}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def freshness_limit_seconds(asset_class: str) -> int:
    asset = str(asset_class).upper()
    if asset in {"STOCK", "ETF", "INDEX"}:
        return int(os.getenv("MARKET_EQUITY_FRESH_SECONDS", str(36 * 60 * 60)))
    return int(os.getenv("MARKET_CONTINUOUS_FRESH_SECONDS", str(6 * 60 * 60)))


def is_market_timestamp_fresh(
    timestamp: Any,
    asset_class: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    try:
        if isinstance(timestamp, (int, float)):
            value = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        else:
            text = str(timestamp or "").replace("Z", "+00:00")
            value = datetime.fromisoformat(text)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            value = value.astimezone(timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        return False
    reference = now or _utc_now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age = (reference.astimezone(timezone.utc) - value).total_seconds()
    return -300 <= age <= freshness_limit_seconds(asset_class)


def fetch_yahoo_chart(
    symbol: str,
    *,
    session: Any = SESSION,
    timeout: int = REQUEST_TIMEOUT,
    range_value: str = YAHOO_RANGE,
    interval: str = YAHOO_INTERVAL,
) -> Optional[Dict[str, Any]]:
    """Fetch and validate a Yahoo chart payload without fabricating values."""
    try:
        response = session.get(
            f"{YAHOO_CHART_BASE}/{quote(str(symbol), safe='')}",
            params={"range": range_value, "interval": interval},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        chart = payload.get("chart") if isinstance(payload, dict) else None
        results = chart.get("result") if isinstance(chart, dict) else None
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return None
        result = results[0]
        timestamps = result.get("timestamp")
        indicators = result.get("indicators")
        quotes = indicators.get("quote") if isinstance(indicators, dict) else None
        if not isinstance(timestamps, list) or not isinstance(quotes, list) or not quotes:
            return None
        quote_row = quotes[0] if isinstance(quotes[0], dict) else None
        closes = quote_row.get("close") if isinstance(quote_row, dict) else None
        if not isinstance(closes, list) or len(closes) != len(timestamps):
            return None
        return result
    except (requests.RequestException, ValueError, TypeError):
        return None


def _usable_candles(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    timestamps = result.get("timestamp", [])
    indicators = result.get("indicators", {})
    quotes = indicators.get("quote", []) if isinstance(indicators, dict) else []
    quote_row = quotes[0] if isinstance(quotes, list) and quotes and isinstance(quotes[0], dict) else {}
    closes = quote_row.get("close", [])
    volumes = quote_row.get("volume", [])
    candles: List[Dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps if isinstance(timestamps, list) else []):
        close = safe_float(closes[index] if index < len(closes) else None)
        if close <= 0:
            continue
        volume_raw = volumes[index] if isinstance(volumes, list) and index < len(volumes) else None
        volume = safe_float(volume_raw) if volume_raw is not None else None
        try:
            timestamp_value = int(timestamp)
        except (TypeError, ValueError):
            continue
        candles.append({"timestamp": timestamp_value, "close": close, "volume": volume})
    return candles


def _pct_change(current: float, previous: float) -> float:
    return ((current / previous) - 1.0) * 100.0 if current > 0 and previous > 0 else 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def score_market_candles(
    candles: Sequence[Mapping[str, Any]],
    asset_class: str,
) -> Optional[Dict[str, Any]]:
    """Deterministic, backward-looking score based only on completed candles."""
    if len(candles) < MIN_USABLE_HISTORY:
        return None
    closes = [safe_float(row.get("close")) for row in candles]
    if any(value <= 0 for value in closes):
        return None

    short_span = 6 if str(asset_class).upper() in {"STOCK", "ETF", "INDEX"} else 8
    medium_span = min(20, len(closes) - 1)
    short_momentum = _pct_change(closes[-1], closes[-1 - short_span])
    medium_momentum = _pct_change(closes[-1], closes[-1 - medium_span])
    recent_returns = [_pct_change(closes[index], closes[index - 1]) for index in range(len(closes) - 11, len(closes))]
    consistency = sum(1 for value in recent_returns if value > 0) / max(len(recent_returns), 1)
    volatility = statistics.pstdev(recent_returns) if len(recent_returns) > 1 else 0.0

    volumes = [row.get("volume") for row in candles]
    valid_volumes = [safe_float(value) for value in volumes if value is not None and safe_float(value) >= 0]
    volume_confirmation = 0.0
    if len(valid_volumes) >= 16:
        recent_average = statistics.fmean(valid_volumes[-4:])
        previous_average = statistics.fmean(valid_volumes[-16:-4])
        if previous_average > 0:
            ratio = recent_average / previous_average
            direction = 1.0 if short_momentum >= 0 else -1.0
            volume_confirmation = _clamp((ratio - 1.0) * 8.0 * direction, -5.0, 5.0)

    bars_24h = 7 if str(asset_class).upper() in {"STOCK", "ETF", "INDEX"} else 24
    comparison_index = max(0, len(closes) - 1 - min(bars_24h, len(closes) - 1))
    change_h24 = _pct_change(closes[-1], closes[comparison_index])
    chase_limit = 10.0 if str(asset_class).upper() in {"STOCK", "ETF"} else 8.0
    if str(asset_class).upper() == "FX":
        chase_limit = 3.0
    anti_chasing_ok = abs(change_h24) <= chase_limit
    chase_penalty = max(abs(change_h24) - chase_limit, 0.0) * 2.5

    bullish_score = 50.0
    bullish_score += _clamp(short_momentum, -5.0, 5.0) * 3.0
    bullish_score += _clamp(medium_momentum, -10.0, 10.0) * 2.0
    bullish_score += (consistency - 0.5) * 20.0
    bullish_score += volume_confirmation
    bullish_score -= max(volatility - 4.0, 0.0) * 2.0
    if change_h24 > chase_limit:
        bullish_score -= chase_penalty
    elif change_h24 < -chase_limit:
        bullish_score += chase_penalty
    bullish_score = _clamp(bullish_score, 0.0, 100.0)

    if bullish_score >= 65.0 and short_momentum > 0.25 and medium_momentum > 0.5 and consistency >= 0.55 and anti_chasing_ok:
        signal = "LONG"
        score = bullish_score
    elif bullish_score <= 35.0 and short_momentum < -0.25 and medium_momentum < -0.5 and consistency <= 0.45 and anti_chasing_ok:
        signal = "SHORT_WATCH"
        score = 100.0 - bullish_score
    else:
        signal = "NEUTRAL"
        score = 50.0

    return {
        "signal": signal,
        "score": round(_clamp(score, 0.0, 100.0), 2),
        "bullish_score": round(bullish_score, 2),
        "short_momentum_pct": round(short_momentum, 4),
        "medium_momentum_pct": round(medium_momentum, 4),
        "trend_consistency": round(consistency, 4),
        "recent_volatility_pct": round(volatility, 4),
        "volume_confirmation": round(volume_confirmation, 4),
        "change_h24": round(change_h24, 4),
        "anti_chasing_ok": anti_chasing_ok,
        "usable_history": len(candles),
    }


def candidate_from_chart(
    instrument: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    asset_class = str(instrument.get("asset_class", "")).upper()
    symbol = str(instrument.get("symbol", "")).upper()
    if asset_class not in ALLOWED_ASSET_CLASSES or not symbol:
        return None
    candles = _usable_candles(result)
    scored = score_market_candles(candles, asset_class)
    if not scored or not candles:
        return None
    last = candles[-1]
    timestamp = datetime.fromtimestamp(last["timestamp"], tz=timezone.utc).isoformat()
    if not is_market_timestamp_fresh(timestamp, asset_class, now=now):
        return None
    meta = result.get("meta", {}) if isinstance(result.get("meta"), dict) else {}
    return {
        "asset_class": asset_class,
        "instrument_id": stable_instrument_id(symbol),
        "symbol": symbol,
        "name": str(instrument.get("name") or meta.get("longName") or meta.get("shortName") or symbol),
        "signal": scored["signal"],
        "score": scored["score"],
        "price_usd": round(safe_float(last.get("close")), 10),
        "change_h24": scored["change_h24"],
        "source": "Yahoo Chart",
        "currency": str(meta.get("currency") or ("USD" if symbol.endswith("=X") else "USD")),
        "exchange": str(meta.get("exchangeName") or meta.get("fullExchangeName") or "YAHOO"),
        "market_data_timestamp": timestamp,
        "paper_tradable": bool(instrument.get("paper_tradable", asset_class in TRADABLE_PAPER_ASSET_CLASSES)),
        "analysis_only": asset_class == "INDEX" or not bool(instrument.get("paper_tradable", True)),
        **{key: value for key, value in scored.items() if key not in {"signal", "score", "change_h24"}},
    }


def fetch_market_candidate(
    instrument: Mapping[str, Any],
    *,
    session: Any = SESSION,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    result = fetch_yahoo_chart(str(instrument.get("symbol", "")), session=session)
    return candidate_from_chart(instrument, result, now=now) if result else None


def scan_market(
    universe: Iterable[Mapping[str, Any]] = MARKET_UNIVERSE,
    *,
    session: Any = SESSION,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for instrument in universe:
        candidate = fetch_market_candidate(instrument, session=session, now=now)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def fetch_current_quote(
    symbol: str,
    asset_class: str,
    *,
    session: Any = SESSION,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Return a fresh Yahoo price for risk monitoring, or None fail-closed."""
    result = fetch_yahoo_chart(symbol, session=session, range_value="5d", interval="1h")
    if not result:
        return None
    candles = _usable_candles(result)
    if not candles:
        return None
    last = candles[-1]
    timestamp = datetime.fromtimestamp(last["timestamp"], tz=timezone.utc).isoformat()
    if not is_market_timestamp_fresh(timestamp, asset_class, now=now):
        return None
    meta = result.get("meta", {}) if isinstance(result.get("meta"), dict) else {}
    return {
        "price_usd": round(safe_float(last.get("close")), 10),
        "market_data_timestamp": timestamp,
        "source": "Yahoo Chart",
        "currency": str(meta.get("currency") or "USD"),
        "exchange": str(meta.get("exchangeName") or meta.get("fullExchangeName") or "YAHOO"),
    }


def live_no_entry_scan() -> Dict[str, Any]:
    """Read-only acceptance scan. It never imports or writes paper state."""
    rows: List[Dict[str, Any]] = []
    for instrument in MARKET_UNIVERSE:
        result = fetch_yahoo_chart(str(instrument["symbol"]))
        candles = _usable_candles(result) if result else []
        candidate = candidate_from_chart(instrument, result) if result else None
        rows.append(
            {
                "asset_class": instrument["asset_class"],
                "symbol": instrument["symbol"],
                "http_payload_ok": bool(result),
                "usable_candles": len(candles),
                "fresh_candidate": bool(candidate),
                "signal": candidate.get("signal") if candidate else None,
                "price_usd": candidate.get("price_usd") if candidate else None,
            }
        )
    return {
        "mode": "READ_ONLY_NO_ENTRY",
        "paper_state_touched": False,
        "requested": len(MARKET_UNIVERSE),
        "payloads_ok": sum(1 for row in rows if row["http_payload_ok"]),
        "fresh_candidates": sum(1 for row in rows if row["fresh_candidate"]),
        "rows": rows,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(live_no_entry_scan(), ensure_ascii=False, indent=2))
