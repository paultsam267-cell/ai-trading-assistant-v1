"""Διαχείριση αρχικού κεφαλαίου και αποθεματικού κερδών για το demo wallet.

Το module είναι καθαρά λογιστικό και χρησιμοποιείται μόνο σε paper trading.
Δεν συνδέεται με πραγματικό wallet, exchange ή τραπεζικό λογαριασμό.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, MutableMapping, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

ATHENS_TZ = ZoneInfo("Europe/Athens")
DEFAULT_INITIAL_CAPITAL = 1000.0
DEFAULT_RESERVE_RATE_PCT = 25.0
DEFAULT_POSITION_SIZE_PCT = 2.5
MIN_INITIAL_CAPITAL = 1.0
MAX_INITIAL_CAPITAL = 1_000_000_000.0
EPSILON = 1e-9


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def now_athens() -> datetime:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ)


def format_local(dt: Optional[datetime] = None) -> str:
    value = dt or now_athens()
    if value.tzinfo is None:
        value = value.replace(tzinfo=ATHENS_TZ)
    return value.astimezone(ATHENS_TZ).strftime("%d/%m/%Y %H:%M:%S")


def validate_initial_capital(value: Any) -> float:
    amount = safe_float(value, 0.0)
    if amount < MIN_INITIAL_CAPITAL or amount > MAX_INITIAL_CAPITAL:
        raise ValueError(
            f"Το αρχικό κεφάλαιο πρέπει να είναι από ${MIN_INITIAL_CAPITAL:.2f} "
            f"έως ${MAX_INITIAL_CAPITAL:,.2f}."
        )
    return round(amount, 8)


def normalize_account(
    account: Optional[MutableMapping[str, Any]],
    *,
    default_initial: float = DEFAULT_INITIAL_CAPITAL,
) -> Dict[str, Any]:
    """Επιστρέφει backward-compatible λογαριασμό με το νέο schema."""
    raw: Dict[str, Any] = dict(account or {})
    initial = safe_float(raw.get("initial_capital"), safe_float(default_initial, DEFAULT_INITIAL_CAPITAL))
    if initial <= 0:
        initial = DEFAULT_INITIAL_CAPITAL

    raw["initial_capital"] = round(initial, 8)
    raw["cash"] = round(safe_float(raw.get("cash"), initial), 8)
    raw["realized_pnl"] = round(safe_float(raw.get("realized_pnl")), 8)
    raw["wins"] = int(safe_float(raw.get("wins"), 0))
    raw["losses"] = int(safe_float(raw.get("losses"), 0))
    raw.setdefault("last_update", None)

    reserve_rate = safe_float(raw.get("reserve_rate_pct"), DEFAULT_RESERVE_RATE_PCT)
    reserve_rate = min(max(reserve_rate, 0.0), 100.0)
    reserve_cash = max(safe_float(raw.get("reserve_cash")), 0.0)
    reserve_released = max(safe_float(raw.get("reserve_released_total")), 0.0)
    reserve_contributed = max(
        safe_float(raw.get("reserve_contributed_total"), reserve_cash + reserve_released),
        reserve_cash + reserve_released,
    )
    reserve_hwm = max(safe_float(raw.get("reserve_profit_hwm")), 0.0)

    raw["reserve_rate_pct"] = round(reserve_rate, 8)
    raw["reserve_cash"] = round(reserve_cash, 8)
    raw["reserve_released_total"] = round(reserve_released, 8)
    raw["reserve_contributed_total"] = round(reserve_contributed, 8)
    raw["reserve_profit_hwm"] = round(reserve_hwm, 8)
    raw.setdefault("last_reserve_transfer_at", None)
    raw.setdefault("last_reserve_release_at", None)

    position_size_pct = safe_float(raw.get("position_size_pct"), DEFAULT_POSITION_SIZE_PCT)
    if position_size_pct <= 0:
        position_size_pct = DEFAULT_POSITION_SIZE_PCT
    raw["position_size_pct"] = round(position_size_pct, 8)

    raw.setdefault("simulation_id", f"sim-{uuid4().hex[:12]}")
    raw.setdefault("simulation_started_at", raw.get("last_update") or format_local())
    return raw


def position_size_usd(account: MutableMapping[str, Any]) -> float:
    normalized = normalize_account(account)
    amount = normalized["initial_capital"] * normalized["position_size_pct"] / 100.0
    return round(max(amount, 0.01), 8)


def apply_profit_reserve(
    account: MutableMapping[str, Any],
    *,
    at: Optional[datetime] = None,
) -> Dict[str, float]:
    """Μεταφέρει αυτόματα το 25% του νέου peak καθαρού realized profit στο reserve.

    Η χρήση high-water mark αποτρέπει διπλή κράτηση του ίδιου κέρδους.
    Χειροκίνητη χρήση του reserve δεν επανακρατείται αμέσως, επειδή
    παρακολουθείται το συνολικό ποσό που έχει ήδη συνεισφερθεί.
    """
    normalized = normalize_account(account)
    account.clear()
    account.update(normalized)

    realized = max(safe_float(account.get("realized_pnl")), 0.0)
    old_hwm = max(safe_float(account.get("reserve_profit_hwm")), 0.0)
    new_hwm = max(old_hwm, realized)
    rate = safe_float(account.get("reserve_rate_pct"), DEFAULT_RESERVE_RATE_PCT) / 100.0
    target_contributed = max(new_hwm * rate, 0.0)
    already_contributed = max(safe_float(account.get("reserve_contributed_total")), 0.0)
    requested = max(target_contributed - already_contributed, 0.0)
    available_cash = max(safe_float(account.get("cash")), 0.0)
    transferred = min(requested, available_cash)

    account["reserve_profit_hwm"] = round(new_hwm, 8)
    if transferred > EPSILON:
        account["cash"] = round(available_cash - transferred, 8)
        account["reserve_cash"] = round(safe_float(account.get("reserve_cash")) + transferred, 8)
        account["reserve_contributed_total"] = round(already_contributed + transferred, 8)
        account["last_reserve_transfer_at"] = format_local(at)

    return {
        "transferred": round(transferred, 8),
        "target_contributed": round(target_contributed, 8),
        "profit_high_water_mark": round(new_hwm, 8),
    }


def release_reserve(
    account: MutableMapping[str, Any],
    amount: Any,
    *,
    at: Optional[datetime] = None,
) -> float:
    normalized = normalize_account(account)
    account.clear()
    account.update(normalized)

    requested = safe_float(amount, 0.0)
    if requested <= 0:
        raise ValueError("Το ποσό χρήσης αποθεματικού πρέπει να είναι θετικό.")
    reserve = safe_float(account.get("reserve_cash"))
    if requested > reserve + EPSILON:
        raise ValueError("Το ποσό είναι μεγαλύτερο από το διαθέσιμο αποθεματικό.")

    released = min(requested, reserve)
    account["reserve_cash"] = round(reserve - released, 8)
    account["cash"] = round(safe_float(account.get("cash")) + released, 8)
    account["reserve_released_total"] = round(
        safe_float(account.get("reserve_released_total")) + released,
        8,
    )
    account["last_reserve_release_at"] = format_local(at)
    return round(released, 8)


def total_equity(active_equity: Any, account: MutableMapping[str, Any]) -> float:
    normalized = normalize_account(account)
    return round(safe_float(active_equity) + safe_float(normalized.get("reserve_cash")), 8)


def new_simulation_account(
    initial_capital: Any,
    *,
    at: Optional[datetime] = None,
    reserve_rate_pct: float = DEFAULT_RESERVE_RATE_PCT,
) -> Dict[str, Any]:
    initial = validate_initial_capital(initial_capital)
    started = at or now_athens()
    return normalize_account(
        {
            "simulation_id": f"sim-{started.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}",
            "simulation_started_at": format_local(started),
            "initial_capital": initial,
            "cash": initial,
            "realized_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "reserve_rate_pct": reserve_rate_pct,
            "reserve_cash": 0.0,
            "reserve_contributed_total": 0.0,
            "reserve_released_total": 0.0,
            "reserve_profit_hwm": 0.0,
            "position_size_pct": DEFAULT_POSITION_SIZE_PCT,
            "last_update": format_local(started),
            "day_start_date": started.astimezone(ATHENS_TZ).date().isoformat(),
            "day_start_equity": initial,
            "day_start_recorded_at": format_local(started),
        },
        default_initial=initial,
    )


def simulation_summary(
    account: MutableMapping[str, Any],
    *,
    active_equity: Any,
    open_positions: int,
    trade_count: int,
    at: Optional[datetime] = None,
) -> Dict[str, Any]:
    normalized = normalize_account(account)
    total = total_equity(active_equity, normalized)
    initial = safe_float(normalized.get("initial_capital"))
    pnl = total - initial
    return {
        "simulation_id": normalized.get("simulation_id"),
        "started_at": normalized.get("simulation_started_at"),
        "ended_at": format_local(at),
        "initial_capital": round(initial, 8),
        "active_equity": round(safe_float(active_equity), 8),
        "reserve_cash": round(safe_float(normalized.get("reserve_cash")), 8),
        "total_equity": round(total, 8),
        "total_pnl": round(pnl, 8),
        "total_pnl_pct": round((pnl / initial * 100.0) if initial > 0 else 0.0, 8),
        "realized_pnl": round(safe_float(normalized.get("realized_pnl")), 8),
        "wins": int(normalized.get("wins", 0)),
        "losses": int(normalized.get("losses", 0)),
        "open_positions_discarded": int(open_positions),
        "trade_count": int(trade_count),
    }
