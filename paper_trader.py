from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

import market_scanner as ms
import state_store
import telegram_reporting
from archive_utils import find_latest_archive as find_latest_scanner_archive, resolve_project_path
from capital_management import (
    apply_profit_reserve,
    normalize_account,
    position_size_usd,
    total_equity,
)
from paper_risk import (
    calculate_equity,
    ensure_daily_snapshot,
    evaluate_position,
    format_local,
    mark_price_missing,
    normalize_position,
    normalize_positions,
    position_side,
    safe_float,
    set_fresh_price,
)
from risk_config import RISK_SETTINGS
from intelligence_radar import allocation_decision, load_latest_intelligence

# Windows consoles may otherwise fail on Greek/token symbols.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ATHENS_TZ = ZoneInfo("Europe/Athens")
ARCHIVE_DIR = resolve_project_path("ARCHIVE_DIR", "archive")
STATE_DIR = resolve_project_path("PAPER_STATE_DIR", "paper_state")
POSITIONS_FILE = STATE_DIR / "paper_positions.json"
TRADES_FILE = STATE_DIR / "paper_trade_log.json"
ACCOUNT_FILE = STATE_DIR / "paper_account.json"
STATE_LOCK_TIMEOUT_SECONDS = float(os.getenv("PAPER_STATE_LOCK_TIMEOUT_SECONDS", "10"))

INITIAL_CASH = float(os.getenv("PAPER_INITIAL_CASH", "1000"))

# LONG entry filters
MIN_ENTRY_SCORE = float(os.getenv("PAPER_MIN_ENTRY_SCORE", "55"))
MIN_LIQUIDITY_USD = float(os.getenv("PAPER_MIN_LIQUIDITY_USD", "30000"))
MIN_VOLUME_24H = float(os.getenv("PAPER_MIN_VOLUME_24H", "150000"))
MAX_CHANGE_24H = float(os.getenv("PAPER_MAX_CHANGE_24H", "180"))
MIN_CHANGE_24H = float(os.getenv("PAPER_MIN_CHANGE_24H", "10"))

# Non-crypto market entry filters.  These deliberately do not reference DEX
# liquidity, pair age or transaction-count fields.
MARKET_MIN_ENTRY_SCORE = float(os.getenv("PAPER_MARKET_MIN_ENTRY_SCORE", "65"))

# Synthetic SHORT entry filters. No real borrow, leverage or exchange order.
MIN_SHORT_ENTRY_SCORE = float(os.getenv("PAPER_MIN_SHORT_ENTRY_SCORE", "40"))
MIN_SHORT_LIQUIDITY_USD = float(os.getenv("PAPER_MIN_SHORT_LIQUIDITY_USD", "50000"))
MIN_SHORT_VOLUME_24H = float(os.getenv("PAPER_MIN_SHORT_VOLUME_24H", "200000"))
SHORT_MIN_DECLINE_PCT = float(os.getenv("PAPER_SHORT_MIN_DECLINE_PCT", "10"))
SHORT_MAX_DECLINE_PCT = float(os.getenv("PAPER_SHORT_MAX_DECLINE_PCT", "60"))
SHORT_MIN_SELL_BUY_RATIO = float(os.getenv("PAPER_SHORT_MIN_SELL_BUY_RATIO", "1.25"))

DEX_API_BASE = "https://api.dexscreener.com"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ai-paper-trader-long-short-demo/4.0"})


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ)


def now_local_str() -> str:
    return format_local(now_local())


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def save_json_file(path: Path, data: Any) -> None:
    state_store.atomic_write_json(path, data)


def state_transaction(*, timeout: Optional[float] = None) -> state_store.InterProcessFileLock:
    """Return the one common lock used by every PAPER portfolio writer."""

    return state_store.portfolio_lock(
        STATE_DIR,
        timeout=STATE_LOCK_TIMEOUT_SECONDS if timeout is None else timeout,
    )


def position_key(item: Dict[str, Any]) -> str:
    asset_class = str(item.get("asset_class") or "CRYPTO").upper()
    instrument_id = str(item.get("instrument_id") or "").strip()
    if asset_class in ms.ALLOWED_ASSET_CLASSES and instrument_id:
        # Separate namespace guarantees that a legacy crypto chain/dex/symbol
        # triple can never collide with a Yahoo instrument identity.
        return f"market:{asset_class.lower()}:{instrument_id}".lower()
    return f"{item.get('chain')}:{item.get('dex')}:{item.get('symbol')}".lower()


def asset_class_of(item: Dict[str, Any]) -> str:
    asset_class = str(item.get("asset_class") or "CRYPTO").upper()
    return asset_class if asset_class in ms.ALLOWED_ASSET_CLASSES else "CRYPTO"


def format_money(value: Any) -> str:
    number = safe_float(value)
    if abs(number) < 0.005:
        number = 0.0
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000:
        return f"{sign}${number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{sign}${number / 1_000:.1f}K"
    return f"{sign}${number:.2f}"


def format_price(value: Any) -> str:
    number = safe_float(value)
    if number >= 1:
        return f"${number:.4f}"
    if number >= 0.01:
        return f"${number:.6f}"
    if number >= 0.0001:
        return f"${number:.8f}"
    if number >= 0.00000001:
        return f"${number:.12f}"
    return f"${number:.16f}"


def format_pct(value: Any) -> str:
    number = safe_float(value)
    return f"{'+' if number > 0 else ''}{number:.2f}%"


def send_console_message(text: str) -> None:
    prepared = telegram_reporting.with_paper_header(text)
    print(prepared)
    if not telegram_reporting.send_notification(prepared, session=SESSION, timeout=REQUEST_TIMEOUT):
        print("[TELEGRAM] BOT_TOKEN or CHAT_ID missing. Message kept console-only.")


def load_account() -> Dict[str, Any]:
    account = load_json_file(ACCOUNT_FILE, {})
    if not isinstance(account, dict):
        account = {}
    return normalize_account(account, default_initial=INITIAL_CASH)


def find_latest_archive() -> Optional[Path]:
    return find_latest_scanner_archive(ARCHIVE_DIR)


def load_latest_snapshot() -> Dict[str, Any]:
    latest = find_latest_archive()
    if latest is None:
        raise RuntimeError("Δεν βρέθηκε archive JSON στον φάκελο archive.")
    data = load_json_file(latest, {})
    if not isinstance(data, dict):
        raise RuntimeError("Μη έγκυρο archive format.")
    data["_source_file"] = latest.name
    return data


def build_market_map(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    market_map: Dict[str, Dict[str, Any]] = {}
    for item in snapshot.get("items", []):
        if isinstance(item, dict):
            market_map[position_key(item)] = item
    return market_map


def parse_pair_ref_from_url(url: str) -> Optional[Tuple[str, str]]:
    try:
        parts = [part for part in urlparse(url).path.split("/") if part]
        if len(parts) < 2:
            return None
        return parts[0].lower().strip(), parts[1].strip()
    except Exception:
        return None


def fetch_live_pair(chain: str, pair_id: str) -> Dict[str, Any]:
    try:
        response = SESSION.get(
            f"{DEX_API_BASE}/latest/dex/pairs/{chain}/{pair_id}",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        pairs = payload.get("pairs", []) if isinstance(payload, dict) else []
        if isinstance(pairs, list) and pairs and isinstance(pairs[0], dict):
            return pairs[0]
    except (requests.RequestException, ValueError):
        pass
    return {}


def live_pair_to_market_item(pair: Dict[str, Any], position: Dict[str, Any]) -> Dict[str, Any]:
    change = pair.get("priceChange", {}) if isinstance(pair.get("priceChange"), dict) else {}
    return {
        "symbol": position.get("symbol", ""),
        "name": position.get("name", ""),
        "chain": position.get("chain", ""),
        "dex": position.get("dex", ""),
        "url": position.get("url", ""),
        "price_usd": safe_float(pair.get("priceUsd")),
        "change_h24": safe_float(change.get("h24")),
        "txns": pair.get("txns", {}),
        "signal": classify_live_pair(pair),
    }


def classify_live_pair(pair: Dict[str, Any]) -> str:
    if not pair:
        return "N/A"
    change_h24 = safe_float(pair.get("priceChange", {}).get("h24"))
    buys_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("buys"))
    sells_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("sells"))
    if change_h24 >= 0 and buys_h1 > sells_h1:
        return "LONG"
    if change_h24 < 0 and sells_h1 > buys_h1:
        return "SHORT_WATCH"
    return "NEUTRAL"


def fetch_open_position_market_data(
    positions: Dict[str, Dict[str, Any]],
    snapshot_map: Dict[str, Dict[str, Any]],
    at: datetime,
) -> Dict[str, Dict[str, Any]]:
    """Perform market-data network calls without holding the portfolio lock."""

    market_map: Dict[str, Dict[str, Any]] = {}
    for key, position in positions.items():
        position = dict(position) if isinstance(position, dict) else {}
        normalize_position(position, key)
        market_item: Dict[str, Any] = {}
        asset_class = asset_class_of(position)
        if asset_class == "CRYPTO":
            pair_ref = parse_pair_ref_from_url(str(position.get("url", "")))
            if pair_ref:
                pair = fetch_live_pair(*pair_ref)
                if safe_float(pair.get("priceUsd")) > 0:
                    market_item = live_pair_to_market_item(pair, position)
        else:
            quote = ms.fetch_current_quote(
                str(position.get("symbol", "")),
                asset_class,
                now=at,
            )
            if isinstance(quote, dict) and safe_float(quote.get("price_usd")) > 0:
                market_item = quote

        if not market_item:
            snapshot_item = snapshot_map.get(key, {})
            if safe_float(snapshot_item.get("price_usd")) > 0:
                market_item = snapshot_item

        if safe_float(market_item.get("price_usd")) > 0:
            market_map[key] = market_item
    return market_map


def apply_open_position_market_data(
    positions: Dict[str, Dict[str, Any]],
    market_map: Dict[str, Dict[str, Any]],
    at: datetime,
) -> List[str]:
    """Revalidate fetched data and apply it to the latest locked positions."""

    skipped: List[str] = []
    for key, position in positions.items():
        normalize_position(position, key)
        market_item = market_map.get(key, {})
        asset_class = asset_class_of(position)
        price = safe_float(market_item.get("price_usd"))

        if asset_class != "CRYPTO" and price > 0:
            market_timestamp = market_item.get("market_data_timestamp")
            if not ms.is_market_timestamp_fresh(market_timestamp, asset_class, now=at):
                price = 0.0
            else:
                position["market_data_timestamp"] = market_timestamp
                position["market_source"] = market_item.get("source") or position.get("market_source")
                position["currency"] = market_item.get("currency") or position.get("currency")
                position["exchange"] = market_item.get("exchange") or position.get("exchange")

        if price > 0:
            set_fresh_price(position, price, at)
            continue

        mark_price_missing(position)
        message = (
            f"{position.get('symbol', key)}: {position.get('price_status')} market data; "
            "exit skipped (STALE/MISSING => NO EXIT)."
        )
        skipped.append(message)
        print(message)
    return skipped


def refresh_open_position_prices(
    positions: Dict[str, Dict[str, Any]],
    snapshot_map: Dict[str, Dict[str, Any]],
    at: datetime,
) -> Dict[str, Dict[str, Any]]:
    """Compatibility wrapper; transactional callers use fetch/apply separately."""

    market_map = fetch_open_position_market_data(positions, snapshot_map, at)
    apply_open_position_market_data(positions, market_map, at)
    return market_map


def get_buy_sell_data(item: Dict[str, Any]) -> Tuple[float, float]:
    txns = item.get("txns", {})
    if not isinstance(txns, dict):
        return 0.0, 0.0
    return (
        safe_float(txns.get("h1", {}).get("buys")),
        safe_float(txns.get("h1", {}).get("sells")),
    )


def count_side(positions: Dict[str, Any], side: str) -> int:
    wanted = side.upper()
    return sum(1 for p in positions.values() if isinstance(p, dict) and position_side(p) == wanted)


def should_open_long(item: Dict[str, Any], account: Dict[str, Any], positions: Dict[str, Any], intelligence: Optional[Dict[str, Any]] = None):
    symbol = str(item.get("symbol", "-")).upper()
    signal = str(item.get("signal", "NEUTRAL")).upper()
    key = position_key(item)
    score = safe_float(item.get("score"))
    price = safe_float(item.get("price_usd"))
    liquidity = safe_float(item.get("liquidity_usd"))
    volume = safe_float(item.get("volume_h24"))
    change = safe_float(item.get("change_h24"))
    cash = safe_float(account.get("cash"))
    decision = allocation_decision(item, account, intelligence, positions)
    planned_size = safe_float(decision.get("allocation_usd"))
    buys_h1, sells_h1 = get_buy_sell_data(item)

    if decision.get("blocked"):
        return False, f"{symbol}: μπλοκάρεται από Intelligence Radar ({decision.get('level')})."
    if key in positions:
        return False, f"{symbol}: υπάρχει ήδη ανοιχτή θέση."
    if len(positions) >= RISK_SETTINGS.max_open_positions:
        return False, f"{symbol}: μπλοκάρεται λόγω ορίου ανοιχτών θέσεων."
    if signal != "LONG":
        return False, f"{symbol}: δεν είναι αγοραστικό σήμα."
    if price <= 0:
        return False, f"{symbol}: μη έγκυρη τιμή."
    if cash < planned_size:
        return False, f"{symbol}: χαμηλό διαθέσιμο cash."
    if score < MIN_ENTRY_SCORE:
        return False, f"{symbol}: χαμηλό score ({score})."
    if liquidity < MIN_LIQUIDITY_USD:
        return False, f"{symbol}: χαμηλή ρευστότητα ({format_money(liquidity)})."
    if volume < MIN_VOLUME_24H:
        return False, f"{symbol}: χαμηλός όγκος 24h ({format_money(volume)})."
    if change > MAX_CHANGE_24H:
        return False, f"{symbol}: έχει ήδη κάνει μεγάλο pump ({format_pct(change)})."
    if change < MIN_CHANGE_24H:
        return False, f"{symbol}: αδύναμη ανοδική τάση ({format_pct(change)})."
    if (buys_h1 > 0 or sells_h1 > 0) and sells_h1 > buys_h1:
        return False, f"{symbol}: οι πωλητές είναι περισσότεροι από τους αγοραστές."
    if change > 120 and volume < liquidity * 5:
        return False, f"{symbol}: πιθανό fake pump, δεν μπαίνουμε."
    return True, f"{symbol}: επιτρέπεται LONG demo θέση."


def should_open_short(item: Dict[str, Any], account: Dict[str, Any], positions: Dict[str, Any], intelligence: Optional[Dict[str, Any]] = None):
    symbol = str(item.get("symbol", "-")).upper()
    signal = str(item.get("signal", "NEUTRAL")).upper()
    key = position_key(item)
    score = safe_float(item.get("score"))
    price = safe_float(item.get("price_usd"))
    liquidity = safe_float(item.get("liquidity_usd"))
    volume = safe_float(item.get("volume_h24"))
    change = safe_float(item.get("change_h24"))
    cash = safe_float(account.get("cash"))
    decision = allocation_decision(item, account, intelligence, positions)
    planned_size = safe_float(decision.get("allocation_usd"))
    buys_h1, sells_h1 = get_buy_sell_data(item)
    ratio = sells_h1 / max(buys_h1, 1.0)

    if decision.get("blocked"):
        return False, f"{symbol}: μπλοκάρεται από Intelligence Radar ({decision.get('level')})."
    if key in positions:
        return False, f"{symbol}: υπάρχει ήδη ανοιχτή θέση."
    if len(positions) >= RISK_SETTINGS.max_open_positions:
        return False, f"{symbol}: μπλοκάρεται λόγω ορίου ανοιχτών θέσεων."
    if count_side(positions, "SHORT") >= RISK_SETTINGS.max_short_positions:
        return False, f"{symbol}: συμπληρώθηκε το όριο συνθετικών SHORT."
    if signal not in {"SHORT", "SHORT_WATCH"}:
        return False, f"{symbol}: δεν είναι πτωτικό σήμα για SHORT."
    if price <= 0:
        return False, f"{symbol}: μη έγκυρη τιμή."
    if cash < planned_size:
        return False, f"{symbol}: χαμηλό διαθέσιμο cash για short collateral."
    if score < MIN_SHORT_ENTRY_SCORE:
        return False, f"{symbol}: χαμηλό short score ({score})."
    if liquidity < MIN_SHORT_LIQUIDITY_USD:
        return False, f"{symbol}: χαμηλή ρευστότητα για SHORT ({format_money(liquidity)})."
    if volume < MIN_SHORT_VOLUME_24H:
        return False, f"{symbol}: χαμηλός όγκος για SHORT ({format_money(volume)})."
    if change > -SHORT_MIN_DECLINE_PCT:
        return False, f"{symbol}: η πτώση δεν είναι αρκετή για SHORT ({format_pct(change)})."
    if change < -SHORT_MAX_DECLINE_PCT:
        return False, f"{symbol}: έχει ήδη καταρρεύσει πολύ, αποφεύγεται chasing SHORT ({format_pct(change)})."
    if ratio < SHORT_MIN_SELL_BUY_RATIO:
        return False, f"{symbol}: ανεπαρκής υπεροχή πωλητών ({ratio:.2f}x)."
    return True, f"{symbol}: επιτρέπεται SYNTHETIC SHORT demo θέση 1x."


def should_open_market(
    item: Dict[str, Any],
    account: Dict[str, Any],
    positions: Dict[str, Any],
    intelligence: Optional[Dict[str, Any]] = None,
):
    """Asset-specific non-crypto gate with global portfolio limits."""
    asset_class = asset_class_of(item)
    symbol = str(item.get("symbol", "-")).upper()
    signal = str(item.get("signal", "NEUTRAL")).upper()
    score = safe_float(item.get("score"))
    price = safe_float(item.get("price_usd"))
    change = safe_float(item.get("change_h24"))
    key = position_key(item)
    cash = safe_float(account.get("cash"))
    decision = allocation_decision(item, account, intelligence, positions)
    planned_size = safe_float(decision.get("allocation_usd"))

    if asset_class == "INDEX" or bool(item.get("analysis_only")) or item.get("paper_tradable") is False:
        return False, f"{symbol}: INDEX/analysis-only instrument, δεν ανοίγει paper θέση."
    if asset_class not in ms.TRADABLE_PAPER_ASSET_CLASSES:
        return False, f"{symbol}: μη υποστηριζόμενο market asset class."
    if decision.get("blocked"):
        return False, f"{symbol}: μπλοκάρεται από το κοινό allocation/exposure policy ({decision.get('level')})."
    if key in positions or any(
        isinstance(position, dict)
        and str(position.get("instrument_id") or "").upper() == str(item.get("instrument_id") or "").upper()
        and asset_class_of(position) == asset_class
        for position in positions.values()
    ):
        return False, f"{symbol}: υπάρχει ήδη ανοιχτή θέση για το instrument."
    if len(positions) >= RISK_SETTINGS.max_open_positions:
        return False, f"{symbol}: μπλοκάρεται λόγω global ορίου ανοιχτών θέσεων."
    if signal not in {"LONG", "SHORT_WATCH"}:
        return False, f"{symbol}: το market signal είναι NEUTRAL."
    if signal == "SHORT_WATCH" and count_side(positions, "SHORT") >= RISK_SETTINGS.max_short_positions:
        return False, f"{symbol}: συμπληρώθηκε το global όριο συνθετικών SHORT."
    if price <= 0:
        return False, f"{symbol}: μη έγκυρη market τιμή."
    if not ms.is_market_timestamp_fresh(item.get("market_data_timestamp"), asset_class):
        return False, f"{symbol}: STALE/MISSING market timestamp."
    if cash < planned_size or planned_size <= 0:
        return False, f"{symbol}: χαμηλό διαθέσιμο κοινό cash."
    if score < MARKET_MIN_ENTRY_SCORE:
        return False, f"{symbol}: χαμηλό market score ({score})."
    if item.get("anti_chasing_ok") is False:
        return False, f"{symbol}: μπλοκάρεται από anti-chasing guard."

    extreme_limit = 3.0 if asset_class == "FX" else 8.0 if asset_class == "METAL" else 10.0
    if signal == "LONG" and change > extreme_limit:
        return False, f"{symbol}: ακραία πρόσφατη άνοδος, αποφεύγεται chasing ({format_pct(change)})."
    if signal == "SHORT_WATCH" and change < -extreme_limit:
        return False, f"{symbol}: ακραία πρόσφατη πτώση, αποφεύγεται chasing ({format_pct(change)})."
    return True, f"{symbol}: επιτρέπεται {signal} {asset_class} PAPER θέση."


def should_open_position(item: Dict[str, Any], account: Dict[str, Any], positions: Dict[str, Any], intelligence: Optional[Dict[str, Any]] = None):
    if asset_class_of(item) != "CRYPTO":
        return should_open_market(item, account, positions, intelligence)
    signal = str(item.get("signal", "NEUTRAL")).upper()
    if signal in {"SHORT", "SHORT_WATCH"}:
        return should_open_short(item, account, positions, intelligence)
    return should_open_long(item, account, positions, intelligence)


def open_demo_position(
    account: Dict[str, Any],
    positions: Dict[str, Dict[str, Any]],
    trades: List[Dict[str, Any]],
    item: Dict[str, Any],
    at: datetime,
    intelligence: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    allowed, reason = should_open_position(item, account, positions, intelligence)
    print(reason)
    if not allowed:
        return None

    key = position_key(item)
    symbol = str(item.get("symbol", "")).upper()
    signal = str(item.get("signal", "LONG")).upper()
    side = "SHORT" if signal in {"SHORT", "SHORT_WATCH"} else "LONG"
    asset_class = asset_class_of(item)
    price = safe_float(item.get("price_usd"))
    allocation_meta = allocation_decision(item, account, intelligence, positions)
    allocation = min(safe_float(allocation_meta.get("allocation_usd")), safe_float(account.get("cash")))
    quantity = allocation / price
    if side == "SHORT":
        initial_stop = price * (1 + RISK_SETTINGS.short_stop_loss_pct / 100.0)
    else:
        initial_stop = price * (1 - RISK_SETTINGS.stop_loss_pct / 100.0)
    opened_at = format_local(at)

    account["cash"] = round(safe_float(account.get("cash")) - allocation, 8)
    position = {
        "key": key,
        "side": side,
        "synthetic_short": side == "SHORT",
        "leverage": 1.0,
        "symbol": symbol,
        "name": str(item.get("name", "")),
        "asset_class": asset_class,
        "instrument_id": str(item.get("instrument_id") or "").strip(),
        "market_source": str(item.get("source") or ("DexScreener" if asset_class == "CRYPTO" else "")).strip(),
        "currency": str(item.get("currency") or ("USD" if asset_class != "CRYPTO" else "")).strip(),
        "exchange": str(item.get("exchange") or "").strip(),
        "market_data_timestamp": item.get("market_data_timestamp"),
        "chain": str(item.get("chain", "")).lower(),
        "dex": str(item.get("dex", "")).lower(),
        "token_address": str(item.get("token_address") or "").strip(),
        "pair_address": str(item.get("pair_address") or "").strip(),
        "entry_price": price,
        "quantity": quantity,
        "initial_quantity": quantity,
        "remaining_quantity": quantity,
        "allocated_usd": allocation,
        "initial_allocated_usd": allocation,
        "remaining_cost_basis": allocation,
        "entry_score": safe_float(item.get("score")),
        "entry_signal": signal,
        "entry_time": opened_at,
        "opened_at": opened_at,
        "url": str(item.get("url", "")).strip(),
        "current_price": price,
        "last_price_update_at": opened_at,
        "price_status": "FRESH",
        "highest_price": price,
        "lowest_price": price,
        "dynamic_stop_price": initial_stop,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "take_profit_levels_completed": [],
        "trailing_stop_active": False,
        "trailing_activated": False,
        "last_exit_reason": None,
        "intelligence_allocation_level": allocation_meta.get("level"),
        "intelligence_allocation_pct": allocation_meta.get("allocation_pct"),
        "intelligence_regime": allocation_meta.get("regime_direction"),
        "intelligence_confidence": allocation_meta.get("regime_confidence"),
    }
    positions[key] = position

    trade_type = "SHORT_OPEN" if side == "SHORT" else "BUY"
    trade_reason = "SYNTHETIC_SHORT_ENTRY" if side == "SHORT" else "SMART_ENTRY"
    trades.append(
        {
            "type": trade_type,
            "side": side,
            "time": opened_at,
            "symbol": symbol,
            "asset_class": asset_class,
            "instrument_id": position["instrument_id"],
            "market_source": position["market_source"],
            "currency": position["currency"],
            "exchange": position["exchange"],
            "chain": position["chain"],
            "dex": position["dex"],
            "price": price,
            "quantity": quantity,
            "allocated_usd": allocation,
            "margin_usd": allocation,
            "score": position["entry_score"],
            "signal": position["entry_signal"],
            "reason": trade_reason,
            "url": position["url"],
            "synthetic_short": side == "SHORT",
            "entry_price": price,
            "stop_loss": initial_stop,
            "take_profit": "/".join(
                f"+{level[1]:.0f}%" for level in RISK_SETTINGS.take_profit_levels(side)
            ),
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
            "price_status": "FRESH",
            "market_data_timestamp": position.get("market_data_timestamp"),
        }
    )
    return telegram_reporting.format_trade_report(trades[-1], freshness_status="FRESH")


def action_message(action: Dict[str, Any]) -> str:
    record = action.get("trade") if isinstance(action.get("trade"), dict) else action
    return telegram_reporting.format_trade_report(record, freshness_status=str(record.get("price_status") or "FRESH"))


def build_summary(account: Dict[str, Any], positions: Dict[str, Any], snapshot: Dict[str, Any]) -> str:
    settings = RISK_SETTINGS
    long_count = count_side(positions, "LONG")
    short_count = count_side(positions, "SHORT")
    active_equity = calculate_equity(account, positions)
    total_value = total_equity(active_equity, account)
    initial = safe_float(account.get("initial_capital"), INITIAL_CASH)
    total_pnl = total_value - initial
    return (
        "ΣΥΝΟΨΗ DEMO BOT LONG + SYNTHETIC SHORT\n"
        f"Ώρα: {now_local_str()}\n"
        f"Αρχείο scanner: {snapshot.get('_source_file', '-')}\n"
        f"Αρχικό κεφάλαιο: {format_money(initial)}\n"
        f"Ενεργό cash: {format_money(account.get('cash'))}\n"
        f"Αποθεματικό κερδών: {format_money(account.get('reserve_cash'))}\n"
        f"Συνολικό κεφάλαιο: {format_money(total_value)} ({format_money(total_pnl)})\n"
        f"Πραγματοποιημένο PnL: {format_money(account.get('realized_pnl'))}\n"
        f"Μέγεθος νέας θέσης: {format_money(position_size_usd(account))}\n"
        f"Ανοιχτές θέσεις: {len(positions)} (LONG {long_count} | SHORT {short_count})\n"
        f"Κερδισμένα: {int(account.get('wins', 0))} | Χαμένα: {int(account.get('losses', 0))}\n"
        f"LONG Stop Loss: -{settings.stop_loss_pct:.0f}%\n"
        f"LONG TP: +{settings.take_profit_1_pct:.0f}% / +{settings.take_profit_2_pct:.0f}% / +{settings.take_profit_3_pct:.0f}%\n"
        f"SHORT Stop Loss: -{settings.short_stop_loss_pct:.0f}%\n"
        f"SHORT TP: +{settings.short_take_profit_1_pct:.0f}% / +{settings.short_take_profit_2_pct:.0f}% / +{settings.short_take_profit_3_pct:.0f}% synthetic PnL\n"
        f"SHORT Trailing: από +{settings.short_trailing_stop_activation_pct:.0f}% με απόσταση {settings.short_trailing_stop_distance_pct:.0f}%\n"
        f"Μέγιστη διάρκεια: LONG {settings.max_position_age_hours}h | SHORT {settings.short_max_position_age_hours}h"
    )


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    run_at = now_local()
    snapshot = load_latest_snapshot()
    snapshot_map = build_market_map(snapshot)
    intelligence = load_latest_intelligence()
    with state_transaction():
        position_snapshot = normalize_positions(load_json_file(POSITIONS_FILE, {}))

    # All network work is deliberately outside the common state lock.
    market_map = fetch_open_position_market_data(position_snapshot, snapshot_map, run_at)

    alerts: List[str] = []
    with state_transaction():
        # Reload and revalidate every entry/exit against the latest common state.
        account = load_account()
        positions = normalize_positions(load_json_file(POSITIONS_FILE, {}))
        trades = load_json_file(TRADES_FILE, [])
        if not isinstance(trades, list):
            trades = []

        apply_open_position_market_data(positions, market_map, now_local())
        active_equity_at_start = calculate_equity(account, positions)
        ensure_daily_snapshot(account, total_equity(active_equity_at_start, account), run_at)

        closed_keys = set()
        for key in list(positions.keys()):
            actions = evaluate_position(account, positions, trades, key, at=run_at)
            for action in actions:
                alerts.append(action_message(action))
                if action.get("fully_closed"):
                    closed_keys.add(key)

        reserve_result = apply_profit_reserve(account, at=run_at)
        if reserve_result["transferred"] > 0:
            alerts.append(
                "ΑΥΤΟΜΑΤΗ ΜΕΤΑΦΟΡΑ ΣΤΟ ΑΠΟΘΕΜΑΤΙΚΟ\n"
                f"Ποσό: {format_money(reserve_result['transferred'])}\n"
                f"Κανόνας: {safe_float(account.get('reserve_rate_pct')):.0f}% του νέου peak καθαρού πραγματοποιημένου κέρδους."
            )

        for item in snapshot.get("items", []):
            if not isinstance(item, dict) or position_key(item) in closed_keys:
                continue
            message = open_demo_position(account, positions, trades, item, run_at, intelligence)
            if message:
                alerts.append(message)

        account["last_update"] = format_local(run_at)
        save_json_file(POSITIONS_FILE, positions)
        save_json_file(TRADES_FILE, trades)
        save_json_file(ACCOUNT_FILE, account)
        summary = build_summary(account, positions, snapshot)

    for alert in alerts:
        send_console_message(alert)
    send_console_message(summary)
    print("Ο paper trader ολοκληρώθηκε σε PAPER TRADING ONLY mode. Τα SHORT είναι synthetic 1x simulation.")


if __name__ == "__main__":
    main()
