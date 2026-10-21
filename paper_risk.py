"""Testable risk engine για LONG και συνθετικά SHORT demo positions.

Δεν συνδέεται με exchange και δεν εκτελεί πραγματικές συναλλαγές.
Τα SHORT είναι προσομοίωση 1x collateral, χωρίς πραγματικό borrow/leverage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, MutableMapping, Optional, Tuple
from zoneinfo import ZoneInfo

from risk_config import RISK_SETTINGS, RiskSettings

ATHENS_TZ = ZoneInfo("Europe/Athens")
EPSILON = 1e-12


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def now_athens() -> datetime:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ)


def format_local(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ATHENS_TZ)
    return dt.astimezone(ATHENS_TZ).strftime("%d/%m/%Y %H:%M:%S")


def parse_local(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ATHENS_TZ)
            return parsed.astimezone(ATHENS_TZ)
        except ValueError:
            continue
    return None


def position_side(position: MutableMapping[str, Any]) -> str:
    side = str(position.get("side") or "").upper().strip()
    if side not in {"LONG", "SHORT"}:
        signal = str(position.get("entry_signal") or "").upper().strip()
        side = "SHORT" if signal in {"SHORT", "SHORT_WATCH"} else "LONG"
    return side


def position_unrealized_pnl(position: MutableMapping[str, Any], price: Optional[float] = None) -> float:
    normalize_position(position)
    price_value = safe_float(price, safe_float(position.get("current_price")))
    if price_value <= 0:
        return 0.0
    quantity = safe_float(position.get("remaining_quantity"))
    margin = safe_float(position.get("remaining_cost_basis"))
    entry = safe_float(position.get("entry_price"))
    if position_side(position) == "SHORT":
        return quantity * (entry - price_value)
    return quantity * price_value - margin


def position_pnl_pct(position: MutableMapping[str, Any], price: Optional[float] = None) -> float:
    normalize_position(position)
    entry = safe_float(position.get("entry_price"))
    price_value = safe_float(price, safe_float(position.get("current_price")))
    if entry <= 0 or price_value <= 0:
        return 0.0
    if position_side(position) == "SHORT":
        return (entry - price_value) / entry * 100.0
    return (price_value / entry - 1.0) * 100.0


def position_equity_value(position: MutableMapping[str, Any], price: Optional[float] = None) -> float:
    """Τρέχουσα αξία θέσης μέσα στο συνολικό equity.

    LONG: market value των token.
    SHORT: collateral + synthetic PnL, με κατώτατο όριο μηδέν.
    """
    normalize_position(position)
    price_value = safe_float(price, safe_float(position.get("current_price")))
    margin = safe_float(position.get("remaining_cost_basis"))
    if price_value <= 0:
        return margin
    if position_side(position) == "SHORT":
        return max(margin + position_unrealized_pnl(position, price_value), 0.0)
    return safe_float(position.get("remaining_quantity")) * price_value


def best_price_metrics(position: MutableMapping[str, Any], price: Optional[float] = None) -> Tuple[float, float]:
    """Επιστρέφει καλύτερη τιμή και απόσταση από αυτή.

    LONG: υψηλότερη τιμή. SHORT: χαμηλότερη τιμή.
    """
    normalize_position(position)
    price_value = safe_float(price, safe_float(position.get("current_price")))
    entry = safe_float(position.get("entry_price"))
    if position_side(position) == "SHORT":
        best = safe_float(position.get("lowest_price"), entry)
        distance = ((price_value / best) - 1.0) * 100.0 if price_value > 0 and best > 0 else 0.0
        return best, distance
    best = safe_float(position.get("highest_price"), entry)
    distance = ((price_value / best) - 1.0) * 100.0 if price_value > 0 and best > 0 else 0.0
    return best, distance


def normalize_position(position: MutableMapping[str, Any], key: Optional[str] = None) -> MutableMapping[str, Any]:
    """Μετατρέπει παλιά wallet records στη νέα μορφή χωρίς απώλεια δεδομένων."""
    entry_price = safe_float(position.get("entry_price"))
    legacy_quantity = safe_float(position.get("quantity"))
    initial_quantity = safe_float(position.get("initial_quantity"), legacy_quantity)
    remaining_quantity = safe_float(position.get("remaining_quantity"), legacy_quantity)

    legacy_allocated = safe_float(position.get("allocated_usd"))
    initial_allocated = safe_float(position.get("initial_allocated_usd"), legacy_allocated)
    remaining_cost_basis = safe_float(position.get("remaining_cost_basis"), legacy_allocated)

    if initial_quantity <= 0 and remaining_quantity > 0:
        initial_quantity = remaining_quantity
    if remaining_quantity <= 0 and legacy_quantity > 0:
        remaining_quantity = legacy_quantity
    if initial_allocated <= 0 and remaining_cost_basis > 0:
        initial_allocated = remaining_cost_basis

    side = position_side(position)
    position["side"] = side
    position["key"] = str(position.get("key") or key or "")
    position["initial_quantity"] = initial_quantity
    position["remaining_quantity"] = remaining_quantity
    position["quantity"] = remaining_quantity
    position["initial_allocated_usd"] = initial_allocated
    position["remaining_cost_basis"] = remaining_cost_basis
    position["allocated_usd"] = remaining_cost_basis
    position["opened_at"] = str(position.get("opened_at") or position.get("entry_time") or "")
    position["entry_time"] = str(position.get("entry_time") or position.get("opened_at") or "")

    # Old records predate the multi-asset schema and are crypto by definition.
    position["asset_class"] = str(position.get("asset_class") or "CRYPTO").upper()
    position.setdefault("instrument_id", "")
    position.setdefault("market_source", "DexScreener" if position["asset_class"] == "CRYPTO" else "")
    position.setdefault("currency", "")
    position.setdefault("exchange", "")
    position.setdefault("market_data_timestamp", None)
    position.setdefault("token_address", "")
    position.setdefault("pair_address", "")
    position.setdefault("chain", "")
    position.setdefault("dex", "")

    current_price = safe_float(position.get("current_price"), 0.0)
    position["current_price"] = current_price if current_price > 0 else None
    position.setdefault("last_price_update_at", None)

    status = str(position.get("price_status") or "").upper()
    if status not in {"FRESH", "STALE", "UNAVAILABLE"}:
        status = "STALE" if current_price > 0 else "UNAVAILABLE"
    position["price_status"] = status

    highest = safe_float(position.get("highest_price"), entry_price)
    lowest = safe_float(position.get("lowest_price"), entry_price)
    position["highest_price"] = highest if highest > 0 else entry_price
    position["lowest_price"] = lowest if lowest > 0 else entry_price

    levels = position.get("take_profit_levels_completed", [])
    if not isinstance(levels, list):
        levels = []
    position["take_profit_levels_completed"] = [str(level) for level in levels]

    position["realized_pnl"] = safe_float(position.get("realized_pnl"))
    position["unrealized_pnl"] = safe_float(position.get("unrealized_pnl"))
    position["trailing_stop_active"] = bool(
        position.get("trailing_stop_active", position.get("trailing_activated", False))
    )
    position["trailing_activated"] = position["trailing_stop_active"]
    position.setdefault("last_exit_reason", None)
    position.setdefault("synthetic_short", side == "SHORT")
    position.setdefault("leverage", 1.0)

    if "dynamic_stop_price" not in position or safe_float(position.get("dynamic_stop_price")) <= 0:
        if side == "SHORT":
            position["dynamic_stop_price"] = entry_price * (1 + RISK_SETTINGS.short_stop_loss_pct / 100.0)
        else:
            position["dynamic_stop_price"] = entry_price * (1 - RISK_SETTINGS.stop_loss_pct / 100.0)
    return position


def normalize_positions(positions: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(positions, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, value in positions.items():
        if not isinstance(value, dict):
            continue
        normalized[str(key)] = dict(normalize_position(value, str(key)))
    return normalized


def set_fresh_price(position: MutableMapping[str, Any], price: float, at: Optional[datetime] = None) -> None:
    price = safe_float(price)
    if price <= 0:
        mark_price_missing(position)
        return

    at = at or now_athens()
    normalize_position(position)
    entry_price = safe_float(position.get("entry_price"))
    highest = safe_float(position.get("highest_price"), entry_price)
    lowest = safe_float(position.get("lowest_price"), entry_price)

    position["current_price"] = price
    position["last_price_update_at"] = format_local(at)
    position["price_status"] = "FRESH"
    position["highest_price"] = max(highest, price) if highest > 0 else price
    position["lowest_price"] = min(lowest, price) if lowest > 0 else price
    position["unrealized_pnl"] = position_unrealized_pnl(position, price)


def mark_price_missing(position: MutableMapping[str, Any]) -> None:
    normalize_position(position)
    position["price_status"] = "STALE" if safe_float(position.get("current_price")) > 0 else "UNAVAILABLE"


def effective_price_status(
    position: MutableMapping[str, Any],
    now: Optional[datetime] = None,
    settings: RiskSettings = RISK_SETTINGS,
) -> str:
    normalize_position(position)
    if safe_float(position.get("current_price")) <= 0:
        return "UNAVAILABLE"

    status = str(position.get("price_status") or "").upper()
    updated_at = parse_local(position.get("last_price_update_at"))
    if not updated_at:
        return "STALE"

    now = now or now_athens()
    age_minutes = max((now - updated_at).total_seconds() / 60.0, 0.0)
    if status == "FRESH" and age_minutes <= settings.stale_price_minutes:
        return "FRESH"
    return "STALE"


def position_age_hours(position: MutableMapping[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    opened = parse_local(position.get("opened_at") or position.get("entry_time"))
    if not opened:
        return None
    now = now or now_athens()
    return max((now - opened).total_seconds() / 3600.0, 0.0)


def execute_sale(
    account: MutableMapping[str, Any],
    positions: MutableMapping[str, Dict[str, Any]],
    trades: List[Dict[str, Any]],
    key: str,
    exit_price: float,
    reason: str,
    *,
    sell_fraction_remaining: Optional[float] = None,
    quantity_to_sell: Optional[float] = None,
    at: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Κλείνει μέρος ή όλη τη θέση.

    Το όνομα διατηρείται για backward compatibility. Για SHORT, η ενέργεια
    είναι synthetic buy-to-cover και επιστρέφει collateral + PnL.
    """
    position = positions.get(key)
    exit_price = safe_float(exit_price)
    if not isinstance(position, dict) or exit_price <= 0:
        return None

    normalize_position(position, key)
    side = position_side(position)
    remaining_qty = safe_float(position.get("remaining_quantity"))
    remaining_margin = safe_float(position.get("remaining_cost_basis"))
    entry_price = safe_float(position.get("entry_price"))
    if remaining_qty <= EPSILON:
        return None

    if quantity_to_sell is None:
        fraction = min(max(safe_float(sell_fraction_remaining, 1.0), 0.0), 1.0)
        quantity_to_sell = remaining_qty * fraction
    quantity_to_sell = min(max(safe_float(quantity_to_sell), 0.0), remaining_qty)
    if quantity_to_sell <= EPSILON:
        return None

    fraction_of_remaining = quantity_to_sell / remaining_qty
    margin_released = remaining_margin * fraction_of_remaining

    if side == "SHORT":
        raw_pnl = quantity_to_sell * (entry_price - exit_price)
        settlement = margin_released + raw_pnl
        if settlement < 0:
            settlement = 0.0
            pnl = -margin_released
        else:
            pnl = raw_pnl
        cash_change = settlement
        trade_type = "SHORT_CLOSE"
    else:
        exit_value = quantity_to_sell * exit_price
        pnl = exit_value - margin_released
        cash_change = exit_value
        settlement = exit_value
        trade_type = "SELL"

    account["cash"] = round(safe_float(account.get("cash")) + cash_change, 8)
    account["realized_pnl"] = round(safe_float(account.get("realized_pnl")) + pnl, 8)

    position["realized_pnl"] = round(safe_float(position.get("realized_pnl")) + pnl, 8)
    position["last_exit_reason"] = reason
    position["remaining_quantity"] = max(remaining_qty - quantity_to_sell, 0.0)
    position["quantity"] = position["remaining_quantity"]
    position["remaining_cost_basis"] = max(remaining_margin - margin_released, 0.0)
    position["allocated_usd"] = position["remaining_cost_basis"]

    initial_quantity = safe_float(position.get("initial_quantity"), remaining_qty)
    trade = {
        "type": trade_type,
        "side": side,
        "time": format_local(at or now_athens()),
        "symbol": position.get("symbol", ""),
        "asset_class": position.get("asset_class", "CRYPTO"),
        "chain": position.get("chain", ""),
        "dex": position.get("dex", ""),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "price": exit_price,
        "quantity": quantity_to_sell,
        "sold_pct_of_initial": (quantity_to_sell / initial_quantity * 100.0) if initial_quantity > 0 else 100.0,
        "cost_basis_usd": margin_released,
        "margin_released_usd": margin_released,
        "exit_value_usd": settlement,
        "cash_change_usd": cash_change,
        "pnl_usd": pnl,
        "pnl_pct": (pnl / margin_released * 100.0) if margin_released > 0 else 0.0,
        "reason": reason,
        "url": position.get("url", ""),
        "synthetic_short": side == "SHORT",
        "price_status": effective_price_status(position, at or now_athens()),
        "market_data_timestamp": position.get("market_data_timestamp"),
        "stop_loss": position.get("dynamic_stop_price"),
        "take_profit": ",".join(str(level) for level in position.get("take_profit_levels_completed", [])) or "N/A",
    }
    trades.append(trade)

    fully_closed = position["remaining_quantity"] <= EPSILON
    total_position_pnl = safe_float(position.get("realized_pnl"))
    if fully_closed:
        if total_position_pnl >= 0:
            account["wins"] = int(account.get("wins", 0)) + 1
        else:
            account["losses"] = int(account.get("losses", 0)) + 1
        del positions[key]
    else:
        current_price = safe_float(position.get("current_price"), exit_price)
        position["unrealized_pnl"] = position_unrealized_pnl(position, current_price)

    return {
        "reason": reason,
        "symbol": trade["symbol"],
        "side": side,
        "quantity": quantity_to_sell,
        "exit_price": exit_price,
        "exit_value_usd": settlement,
        "pnl_usd": pnl,
        "fully_closed": fully_closed,
        "remaining_quantity": 0.0 if fully_closed else position["remaining_quantity"],
        "trade": trade,
    }


def evaluate_position(
    account: MutableMapping[str, Any],
    positions: MutableMapping[str, Dict[str, Any]],
    trades: List[Dict[str, Any]],
    key: str,
    *,
    at: Optional[datetime] = None,
    settings: RiskSettings = RISK_SETTINGS,
) -> List[Dict[str, Any]]:
    """Εφαρμόζει side-aware stop, TP, trailing και max-hold μόνο με FRESH τιμή."""
    position = positions.get(key)
    if not isinstance(position, dict):
        return []
    normalize_position(position, key)

    at = at or now_athens()
    if effective_price_status(position, at, settings) != "FRESH":
        return []

    price = safe_float(position.get("current_price"))
    entry = safe_float(position.get("entry_price"))
    if price <= 0 or entry <= 0:
        return []

    set_fresh_price(position, price, at)
    side = position_side(position)
    pnl_pct = position_pnl_pct(position, price)
    actions: List[Dict[str, Any]] = []

    if side == "SHORT" and price >= entry * (1.0 + settings.short_liquidation_pct / 100.0):
        action = execute_sale(account, positions, trades, key, price, "SHORT_LIQUIDATION", at=at)
        return [action] if action else []

    if pnl_pct <= -abs(settings.stop_loss_for(side)):
        reason = "SHORT_STOP_LOSS" if side == "SHORT" else "STOP_LOSS"
        action = execute_sale(account, positions, trades, key, price, reason, at=at)
        return [action] if action else []

    initial_qty = safe_float(position.get("initial_quantity"))
    for level_name, trigger_pct, sell_pct in settings.take_profit_levels(side):
        if key not in positions:
            break
        position = positions[key]
        completed = position.get("take_profit_levels_completed", [])
        if pnl_pct < trigger_pct or level_name in completed:
            continue

        quantity_to_sell = initial_qty * (sell_pct / 100.0)
        action = execute_sale(
            account,
            positions,
            trades,
            key,
            price,
            level_name,
            quantity_to_sell=quantity_to_sell,
            at=at,
        )
        if action:
            actions.append(action)
            if key in positions:
                positions[key].setdefault("take_profit_levels_completed", []).append(level_name)

    if key not in positions:
        return actions

    position = positions[key]
    normalize_position(position, key)
    activation = settings.trailing_activation_for(side)
    distance = settings.trailing_distance_for(side)

    if pnl_pct >= activation:
        position["trailing_stop_active"] = True
        position["trailing_activated"] = True

    if position.get("trailing_stop_active"):
        if side == "SHORT":
            lowest = min(safe_float(position.get("lowest_price"), price), price)
            position["lowest_price"] = lowest
            trailing_stop = lowest * (1.0 + distance / 100.0)
            current_dynamic = safe_float(position.get("dynamic_stop_price"), entry * 10)
            position["dynamic_stop_price"] = min(current_dynamic, trailing_stop)
            if price >= trailing_stop:
                action = execute_sale(account, positions, trades, key, price, "SHORT_TRAILING_STOP", at=at)
                if action:
                    actions.append(action)
                return actions
        else:
            highest = max(safe_float(position.get("highest_price"), price), price)
            position["highest_price"] = highest
            trailing_stop = highest * (1.0 - distance / 100.0)
            position["dynamic_stop_price"] = max(safe_float(position.get("dynamic_stop_price")), trailing_stop)
            if price <= trailing_stop:
                action = execute_sale(account, positions, trades, key, price, "TRAILING_STOP", at=at)
                if action:
                    actions.append(action)
                return actions

    age_hours = position_age_hours(position, at)
    if age_hours is not None and age_hours >= settings.max_age_for(side):
        reason = "SHORT_MAX_HOLD_TIME" if side == "SHORT" else "MAX_HOLD_TIME"
        action = execute_sale(account, positions, trades, key, price, reason, at=at)
        if action:
            actions.append(action)

    return actions


def calculate_equity(
    account: MutableMapping[str, Any],
    positions: MutableMapping[str, Dict[str, Any]],
    *,
    use_cost_basis_when_unavailable: bool = True,
) -> float:
    equity = safe_float(account.get("cash"))
    for key, position in positions.items():
        if not isinstance(position, dict):
            continue
        normalize_position(position, key)
        price = safe_float(position.get("current_price"))
        price_status = str(position.get("price_status") or "").upper()

        if price > 0 and price_status == "FRESH":
            equity += position_equity_value(position, price)
        elif use_cost_basis_when_unavailable:
            equity += safe_float(position.get("remaining_cost_basis"))
    return equity


def ensure_daily_snapshot(
    account: MutableMapping[str, Any],
    equity: float,
    at: Optional[datetime] = None,
) -> None:
    at = at or now_athens()
    today = at.astimezone(ATHENS_TZ).date().isoformat()
    if str(account.get("day_start_date") or "") != today:
        account["day_start_date"] = today
        account["day_start_equity"] = round(safe_float(equity), 8)
        account["day_start_recorded_at"] = format_local(at)


def daily_pnl(account: MutableMapping[str, Any], equity: float) -> Dict[str, float]:
    start = safe_float(account.get("day_start_equity"), 0.0)
    if start <= 0:
        return {"pnl": 0.0, "pct": 0.0, "base": 0.0}
    pnl = safe_float(equity) - start
    if abs(pnl) < 0.005:
        pnl = 0.0
    pct = pnl / start * 100.0
    if abs(pct) < 0.0005:
        pct = 0.0
    return {"pnl": pnl, "pct": pct, "base": start}


def next_exit_action(position: MutableMapping[str, Any], settings: RiskSettings = RISK_SETTINGS) -> str:
    normalize_position(position)
    status = effective_price_status(position, settings=settings)
    if status != "FRESH":
        return "Αναμονή έγκυρης τιμής"

    entry = safe_float(position.get("entry_price"))
    price = safe_float(position.get("current_price"))
    if entry <= 0 or price <= 0:
        return "Αναμονή έγκυρης τιμής"

    side = position_side(position)
    pnl_pct = position_pnl_pct(position, price)
    completed = position.get("take_profit_levels_completed", [])

    if side == "SHORT" and price >= entry * (1.0 + settings.short_liquidation_pct / 100.0):
        return "Πλήρες κλείσιμο Synthetic Liquidation"
    if pnl_pct <= -settings.stop_loss_for(side):
        return "Πλήρες κλείσιμο Short Stop Loss" if side == "SHORT" else "Πλήρες κλείσιμο Stop Loss"
    for level_name, trigger_pct, _ in settings.take_profit_levels(side):
        if level_name not in completed:
            prefix = "πτώση " if side == "SHORT" else "+"
            return f"{level_name} σε {prefix}{trigger_pct:.0f}%"
    if position.get("trailing_stop_active"):
        return "Παρακολούθηση short trailing stop" if side == "SHORT" else "Παρακολούθηση trailing stop"
    return "Παρακολούθηση συνθετικού SHORT" if side == "SHORT" else "Παρακολούθηση θέσης"
