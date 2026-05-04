import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

ATHENS_TZ = ZoneInfo("Europe/Athens")

ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "archive"))
STATE_DIR = Path(os.getenv("PAPER_STATE_DIR", "paper_state"))

POSITIONS_FILE = STATE_DIR / "paper_positions.json"
TRADES_FILE = STATE_DIR / "paper_trade_log.json"
ACCOUNT_FILE = STATE_DIR / "paper_account.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

INITIAL_CASH = float(os.getenv("PAPER_INITIAL_CASH", "1000"))
MIN_ENTRY_SCORE = float(os.getenv("PAPER_MIN_ENTRY_SCORE", "45"))
POSITION_SIZE_USD = float(os.getenv("PAPER_POSITION_SIZE_USD", "100"))
TAKE_PROFIT_PCT = float(os.getenv("PAPER_TP_PCT", "0.08"))   # 8%
STOP_LOSS_PCT = float(os.getenv("PAPER_SL_PCT", "0.04"))     # 4%

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ai-paper-trader-demo/1.0"})


def now_local_str() -> str:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ).strftime("%d/%m/%Y %H:%M:%S")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_account() -> Dict[str, Any]:
    account = load_json_file(
        ACCOUNT_FILE,
        {
            "cash": INITIAL_CASH,
            "realized_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "last_update": None,
        },
    )
    if "cash" not in account:
        account["cash"] = INITIAL_CASH
    if "realized_pnl" not in account:
        account["realized_pnl"] = 0.0
    if "wins" not in account:
        account["wins"] = 0
    if "losses" not in account:
        account["losses"] = 0
    return account


def find_latest_archive() -> Optional[Path]:
    if not ARCHIVE_DIR.exists():
        return None

    files = sorted(ARCHIVE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_latest_snapshot() -> Dict[str, Any]:
    latest = find_latest_archive()
    if latest is None:
        raise RuntimeError("Δεν βρέθηκε archive JSON στο φάκελο archive.")

    with open(latest, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError("Μη έγκυρο archive format.")

    data["_source_file"] = latest.name
    return data


def position_key(item: Dict[str, Any]) -> str:
    chain = str(item.get("chain", "")).lower().strip()
    symbol = str(item.get("symbol", "")).upper().strip()
    dex = str(item.get("dex", "")).lower().strip()
    return f"{chain}:{dex}:{symbol}"


def format_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def format_price(value: float) -> str:
    if value >= 1:
        return f"${value:.4f}"
    if value >= 0.01:
        return f"${value:.6f}"
    if value >= 0.0001:
        return f"${value:.8f}"
    return f"${value:.10f}"


def format_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials λείπουν. Μήνυμα:")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    response = SESSION.post(url, json=payload, timeout=20)
    response.raise_for_status()
    time.sleep(1.0)


def build_market_map(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    items = snapshot.get("items", [])
    market_map: Dict[str, Dict[str, Any]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        key = position_key(item)
        market_map[key] = item

    return market_map


def open_demo_position(
    account: Dict[str, Any],
    positions: Dict[str, Any],
    trades: List[Dict[str, Any]],
    item: Dict[str, Any],
) -> Optional[str]:
    key = position_key(item)
    if key in positions:
        return None

    signal = str(item.get("signal", "NEUTRAL")).upper()
    score = safe_float(item.get("score"))
    price = safe_float(item.get("price_usd"))

    if signal != "LONG":
        return None
    if score < MIN_ENTRY_SCORE:
        return None
    if price <= 0:
        return None

    allocation = min(POSITION_SIZE_USD, safe_float(account.get("cash")))
    if allocation <= 0:
        return None

    quantity = allocation / price
    account["cash"] = round(safe_float(account.get("cash")) - allocation, 2)

    positions[key] = {
        "key": key,
        "symbol": str(item.get("symbol", "")).upper(),
        "name": str(item.get("name", "")),
        "chain": str(item.get("chain", "")).lower(),
        "dex": str(item.get("dex", "")).lower(),
        "entry_price": price,
        "quantity": quantity,
        "allocated_usd": allocation,
        "entry_score": score,
        "entry_signal": signal,
        "entry_time": now_local_str(),
        "url": str(item.get("url", "")).strip(),
    }

    trades.append(
        {
            "type": "BUY",
            "time": now_local_str(),
            "symbol": str(item.get("symbol", "")).upper(),
            "chain": str(item.get("chain", "")).lower(),
            "dex": str(item.get("dex", "")).lower(),
            "price": price,
            "quantity": quantity,
            "allocated_usd": allocation,
            "score": score,
            "signal": signal,
            "url": str(item.get("url", "")).strip(),
        }
    )

    return (
        "🟢 DEMO BUY\n"
        f"Symbol: {str(item.get('symbol', '')).upper()}\n"
        f"Signal: {signal}\n"
        f"Score: {score}\n"
        f"Entry: {format_price(price)}\n"
        f"Position size: {format_money(allocation)}\n"
        f"Qty: {quantity:.6f}\n"
        f"Cash left: {format_money(safe_float(account.get('cash')))}"
    )


def close_demo_position(
    account: Dict[str, Any],
    positions: Dict[str, Any],
    trades: List[Dict[str, Any]],
    key: str,
    market_item: Dict[str, Any],
    reason: str,
) -> Optional[str]:
    position = positions.get(key)
    if not position:
        return None

    exit_price = safe_float(market_item.get("price_usd"))
    if exit_price <= 0:
        return None

    quantity = safe_float(position.get("quantity"))
    entry_price = safe_float(position.get("entry_price"))
    value_now = quantity * exit_price
    allocated = safe_float(position.get("allocated_usd"))
    pnl = value_now - allocated
    pnl_pct = ((exit_price / entry_price) - 1.0) * 100 if entry_price > 0 else 0.0

    account["cash"] = round(safe_float(account.get("cash")) + value_now, 2)
    account["realized_pnl"] = round(safe_float(account.get("realized_pnl")) + pnl, 2)

    if pnl >= 0:
        account["wins"] = int(account.get("wins", 0)) + 1
    else:
        account["losses"] = int(account.get("losses", 0)) + 1

    trades.append(
        {
            "type": "SELL",
            "time": now_local_str(),
            "symbol": position.get("symbol", ""),
            "chain": position.get("chain", ""),
            "dex": position.get("dex", ""),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "allocated_usd": allocated,
            "exit_value_usd": value_now,
            "pnl_usd": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "url": position.get("url", ""),
        }
    )

    del positions[key]

    return (
        "🔴 DEMO SELL\n"
        f"Symbol: {position.get('symbol', '')}\n"
        f"Reason: {reason}\n"
        f"Entry: {format_price(entry_price)}\n"
        f"Exit: {format_price(exit_price)}\n"
        f"PnL: {format_money(pnl)} ({format_pct(pnl_pct)})\n"
        f"Cash now: {format_money(safe_float(account.get('cash')))}"
    )


def evaluate_open_positions(
    account: Dict[str, Any],
    positions: Dict[str, Any],
    trades: List[Dict[str, Any]],
    market_map: Dict[str, Dict[str, Any]],
) -> List[str]:
    alerts: List[str] = []

    for key in list(positions.keys()):
        position = positions[key]
        market_item = market_map.get(key)
        if not market_item:
            continue

        entry_price = safe_float(position.get("entry_price"))
        current_price = safe_float(market_item.get("price_usd"))
        signal = str(market_item.get("signal", "NEUTRAL")).upper()

        if entry_price <= 0 or current_price <= 0:
            continue

        pnl_pct_decimal = (current_price / entry_price) - 1.0

        if pnl_pct_decimal >= TAKE_PROFIT_PCT:
            msg = close_demo_position(account, positions, trades, key, market_item, "TAKE_PROFIT")
            if msg:
                alerts.append(msg)
            continue

        if pnl_pct_decimal <= -STOP_LOSS_PCT:
            msg = close_demo_position(account, positions, trades, key, market_item, "STOP_LOSS")
            if msg:
                alerts.append(msg)
            continue

        if signal == "SHORT_WATCH":
            msg = close_demo_position(account, positions, trades, key, market_item, "SIGNAL_FLIP")
            if msg:
                alerts.append(msg)

    return alerts


def build_summary(account: Dict[str, Any], positions: Dict[str, Any], snapshot: Dict[str, Any]) -> str:
    return (
        "📘 DEMO BOT SUMMARY\n"
        f"Ώρα: {now_local_str()}\n"
        f"Source archive: {snapshot.get('_source_file', '-')}\n"
        f"Cash: {format_money(safe_float(account.get('cash')))}\n"
        f"Realized PnL: {format_money(safe_float(account.get('realized_pnl')))}\n"
        f"Open positions: {len(positions)}\n"
        f"Wins: {int(account.get('wins', 0))} | Losses: {int(account.get('losses', 0))}"
    )


def main() -> None:
    ensure_state_dir()

    snapshot = load_latest_snapshot()
    market_map = build_market_map(snapshot)

    account = load_account()
    positions = load_json_file(POSITIONS_FILE, {})
    trades = load_json_file(TRADES_FILE, [])

    alerts: List[str] = []

    alerts.extend(evaluate_open_positions(account, positions, trades, market_map))

    for item in snapshot.get("items", []):
        if not isinstance(item, dict):
            continue
        msg = open_demo_position(account, positions, trades, item)
        if msg:
            alerts.append(msg)

    account["last_update"] = now_local_str()

    save_json_file(POSITIONS_FILE, positions)
    save_json_file(TRADES_FILE, trades)
    save_json_file(ACCOUNT_FILE, account)

    for alert in alerts:
        send_telegram_message(alert)

    send_telegram_message(build_summary(account, positions, snapshot))
    print("Ο paper trader ολοκληρώθηκε.")


if __name__ == "__main__":
    main()
