import json
import math
import os
import random
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ============================================================
# DEMO TRADING BOT V1
# Paper trading only. No real orders.
# - Spot buy / sell simulation
# - Long / short futures simulation
# - Dynamic position sizing
# - Scaling in / scaling out
# - Dynamic TP / SL management
# - Profit split: 30% safe pool, 70% reinvest
# - Telegram alerts
# - JSON journal + performance summary
# ============================================================

# -----------------------------
# Config
# -----------------------------
BASE_URL = "https://api.dexscreener.com"
STATE_PATH = Path(".demo-bot-state.json")
JOURNAL_PATH = Path("demo-journal.json")
SUMMARY_PATH = Path("demo-summary.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SCAN_CHAINS = {
    c.strip().lower()
    for c in os.getenv("SCAN_CHAINS", "solana,bsc").split(",")
    if c.strip()
}

# Demo balance config
INITIAL_TRADING_BALANCE = float(os.getenv("INITIAL_TRADING_BALANCE", "100"))
INITIAL_SAFE_BALANCE = float(os.getenv("INITIAL_SAFE_BALANCE", "0"))
SAFE_PROFIT_PCT = float(os.getenv("SAFE_PROFIT_PCT", "0.30"))   # 30% of realized profit to safe pool
REINVEST_PCT = float(os.getenv("REINVEST_PCT", "0.70"))         # 70% remains trading capital

# Position sizing
SPOT_MIN_POS_PCT = float(os.getenv("SPOT_MIN_POS_PCT", "0.05"))
SPOT_BASE_POS_PCT = float(os.getenv("SPOT_BASE_POS_PCT", "0.10"))
SPOT_MAX_POS_PCT = float(os.getenv("SPOT_MAX_POS_PCT", "0.20"))

FUTURES_MIN_POS_PCT = float(os.getenv("FUTURES_MIN_POS_PCT", "0.03"))
FUTURES_BASE_POS_PCT = float(os.getenv("FUTURES_BASE_POS_PCT", "0.05"))
FUTURES_MAX_POS_PCT = float(os.getenv("FUTURES_MAX_POS_PCT", "0.10"))

FUTURES_LEVERAGE = float(os.getenv("FUTURES_LEVERAGE", "2"))

# Entry / exit thresholds
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "50000"))
MAX_MARKET_CAP = float(os.getenv("MAX_MARKET_CAP", "5000000"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "20000"))
MIN_H1_VOLUME = float(os.getenv("MIN_H1_VOLUME", "15000"))
MIN_SPIKE_RATIO = float(os.getenv("MIN_SPIKE_RATIO", "2.0"))
MIN_PRICE_MOVE_PCT = float(os.getenv("MIN_PRICE_MOVE_PCT", "8"))
MAX_PRICE_MOVE_PCT = float(os.getenv("MAX_PRICE_MOVE_PCT", "1000"))

BUY_SELL_RATIO_LONG_MIN = float(os.getenv("BUY_SELL_RATIO_LONG_MIN", "1.08"))
BUY_SELL_RATIO_SHORT_MAX = float(os.getenv("BUY_SELL_RATIO_SHORT_MAX", "0.94"))

TOP_N = int(os.getenv("TOP_N", "3"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "6"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "240"))

# Risk management
LONG_INITIAL_TP_PCT = float(os.getenv("LONG_INITIAL_TP_PCT", "0.08"))
LONG_INITIAL_SL_PCT = float(os.getenv("LONG_INITIAL_SL_PCT", "0.04"))
SHORT_INITIAL_TP_PCT = float(os.getenv("SHORT_INITIAL_TP_PCT", "0.08"))
SHORT_INITIAL_SL_PCT = float(os.getenv("SHORT_INITIAL_SL_PCT", "0.04"))

PARTIAL_TAKE_PROFIT_PCT = float(os.getenv("PARTIAL_TAKE_PROFIT_PCT", "0.30"))  # Sell 30% on first weakness
SECOND_PARTIAL_TAKE_PROFIT_PCT = float(os.getenv("SECOND_PARTIAL_TAKE_PROFIT_PCT", "0.30"))

# Grid demo settings
GRID_ENABLED = os.getenv("GRID_ENABLED", "1") == "1"
GRID_CAPITAL_PCT = float(os.getenv("GRID_CAPITAL_PCT", "0.08"))
GRID_LEVELS = int(os.getenv("GRID_LEVELS", "5"))
GRID_RANGE_PCT = float(os.getenv("GRID_RANGE_PCT", "0.06"))  # +/- 6% around price
GRID_ONLY_FOR_RANGE = os.getenv("GRID_ONLY_FOR_RANGE", "1") == "1"

PREFERRED_QUOTES = {"USDC", "USDT", "BUSD", "WBNB", "BNB", "WSOL", "SOL"}

session = requests.Session()
session.headers.update({"User-Agent": "demo-trading-bot-v1/1.0"})


# -----------------------------
# Helpers
# -----------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("pairs"), list):
            return [x for x in data["pairs"] if isinstance(x, dict)]
        return [data]
    return []


def fmt_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def get_json(path: str) -> Any:
    url = f"{BASE_URL}{path}"
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_bucket(data: Optional[Dict[str, Any]], keys) -> float:
    if not isinstance(data, dict):
        return 0.0
    for key in keys:
        if key in data:
            return num(data.get(key))
    return 0.0


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[warn] Telegram credentials missing, skipping message")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = session.post(url, json=payload, timeout=20)
    resp.raise_for_status()


# -----------------------------
# Data models
# -----------------------------
@dataclass
class Position:
    id: str
    symbol: str
    name: str
    chain: str
    pair_address: str
    token_address: str
    pair_url: str
    side: str                 # SPOT_LONG, FUTURES_LONG, FUTURES_SHORT, GRID
    status: str               # OPEN, CLOSED
    entry_price: float
    current_price: float
    size_usd: float
    quantity: float
    leverage: float
    stop_loss: float
    take_profit: float
    confidence: float
    score: float
    phase: str
    risk: str
    opened_at: str
    updated_at: str
    closed_at: Optional[str] = None
    exit_price: Optional[float] = None
    realized_pnl_usd: float = 0.0
    realized_pnl_pct: float = 0.0
    scale_in_count: int = 0
    partial_exit_count: int = 0
    notes: str = ""
    tp_extended_count: int = 0


# -----------------------------
# Persistence
# -----------------------------
def default_state() -> Dict[str, Any]:
    return {
        "trading_balance": INITIAL_TRADING_BALANCE,
        "safe_balance": INITIAL_SAFE_BALANCE,
        "open_positions": [],
        "closed_positions": [],
        "last_alerted": {},
        "stats": {
            "wins": 0,
            "losses": 0,
            "total_realized_pnl": 0.0,
            "total_trades": 0,
        },
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_state()
        base = default_state()
        base.update(data)
        return base
    except Exception as exc:
        print(f"[warn] failed to load state: {exc}")
        return default_state()


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = utc_now_iso()
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_journal(entry: Dict[str, Any]) -> None:
    data = []
    if JOURNAL_PATH.exists():
        try:
            data = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []
    data.append(entry)
    JOURNAL_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_summary(state: Dict[str, Any]) -> None:
    stats = state.get("stats", {})
    wins = int(stats.get("wins", 0))
    losses = int(stats.get("losses", 0))
    total_trades = int(stats.get("total_trades", 0))
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0

    summary = {
        "timestamp": utc_now_iso(),
        "trading_balance": round(num(state.get("trading_balance")), 4),
        "safe_balance": round(num(state.get("safe_balance")), 4),
        "equity_estimate": round(
            num(state.get("trading_balance")) + num(state.get("safe_balance")), 4
        ),
        "open_positions": len(state.get("open_positions", [])),
        "closed_positions": len(state.get("closed_positions", [])),
        "wins": wins,
        "losses": losses,
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "total_realized_pnl": round(num(stats.get("total_realized_pnl")), 4),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


# -----------------------------
# Discovery
# -----------------------------
def get_candidate_tokens() -> List[tuple[str, str]]:
    sources = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/community-takeovers/latest/v1",
    ]
    out = set()
    for path in sources:
        try:
            items = as_list(get_json(path))
        except Exception as exc:
            print(f"[warn] failed source {path}: {exc}")
            continue
        for item in items:
            chain = str(item.get("chainId", "")).strip().lower()
            token = str(item.get("tokenAddress", "")).strip()
            if chain in SCAN_CHAINS and token:
                out.add((chain, token))
    return list(out)


def fetch_pairs(chain: str, token_address: str) -> List[Dict[str, Any]]:
    try:
        return as_list(get_json(f"/token-pairs/v1/{chain}/{token_address}"))
    except Exception as exc:
        print(f"[warn] failed token-pairs {chain}/{token_address}: {exc}")
        return []


def pair_rank(pair: Dict[str, Any]):
    quote_symbol = str(pair.get("quoteToken", {}).get("symbol", "")).upper()
    preferred = 1 if quote_symbol in PREFERRED_QUOTES else 0
    liquidity = num(pair.get("liquidity", {}).get("usd"))
    volume_h24 = get_bucket(pair.get("volume"), ("h24", "24h"))
    return preferred, liquidity, volume_h24


def choose_best_pair(pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pairs:
        return None
    return max(pairs, key=pair_rank)


# -----------------------------
# Analysis engine
# -----------------------------
def evaluate_context(item: Dict[str, Any]) -> Dict[str, str]:
    phase = "ΜΕΣΑΙΑ ΦΑΣΗ 🟡"
    risk = "ΜΕΤΡΙΟ 🟡"
    warning = ""

    price_move = num(item.get("price_move"))
    spike = num(item.get("spike_ratio"))
    liquidity = num(item.get("liquidity"))

    if price_move < 25 and spike < 4:
        phase = "ΝΩΡΙΣ 🟢"
    elif price_move < 70:
        phase = "ΜΕΣΑΙΑ ΦΑΣΗ 🟡"
    else:
        phase = "ΑΡΓΑ / ΚΟΝΤΑ ΣΤΗΝ ΚΟΡΥΦΗ 🔴"

    if liquidity > 80000 and spike < 6:
        risk = "ΧΑΜΗΛΟ 🟢"
    elif liquidity > 30000:
        risk = "ΜΕΤΡΙΟ 🟡"
    else:
        risk = "ΥΨΗΛΟ 🔴"

    if liquidity < 20000:
        warning = "⚠️ ΠΡΟΣΟΧΗ: ΧΑΜΗΛΗ ΡΕΥΣΤΟΤΗΤΑ"
    elif spike > 10 and price_move > 80:
        warning = "⚠️ ΠΡΟΣΟΧΗ: ΠΙΘΑΝΟ ΥΠΕΡΒΟΛΙΚΟ PUMP / ΠΙΘΑΝΗ ΔΙΟΡΘΩΣΗ"

    return {"phase": phase, "risk": risk, "warning": warning}


def confidence_score(item: Dict[str, Any]) -> float:
    # 0-100 confidence
    liquidity = num(item.get("liquidity"))
    volume_h1 = num(item.get("volume_h1"))
    spike = num(item.get("spike_ratio"))
    price_move = num(item.get("price_move"))
    ratio = num(item.get("buy_sell_ratio"))

    liquidity_score = clip((liquidity / 100000.0) * 20.0, 0, 20)
    volume_score = clip((volume_h1 / 100000.0) * 20.0, 0, 20)
    spike_score = clip((spike / 8.0) * 20.0, 0, 20)
    move_score = 0.0
    if price_move < 15:
        move_score = 8.0
    elif price_move < 40:
        move_score = 18.0
    elif price_move < 80:
        move_score = 12.0
    else:
        move_score = 6.0

    ratio_score = 0.0
    signal_type = item.get("signal_type")
    if signal_type == "LONG_CANDIDATE":
        ratio_score = clip((ratio - 1.0) * 40.0, 0, 20)
    elif signal_type == "SHORT_WATCH":
        ratio_score = clip((1.0 - ratio) * 40.0, 0, 20)

    return round(liquidity_score + volume_score + spike_score + move_score + ratio_score, 2)


def analyze_pair(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market_cap = num(pair.get("marketCap")) or num(pair.get("fdv"))
    liquidity = num(pair.get("liquidity", {}).get("usd"))
    volume_h1 = get_bucket(pair.get("volume"), ("h1", "1h"))
    volume_h24 = get_bucket(pair.get("volume"), ("h24", "24h"))

    baseline_h1 = (volume_h24 / 24.0) if volume_h24 > 0 else 0.0
    spike_ratio = (volume_h1 / baseline_h1) if baseline_h1 > 0 else (999.0 if volume_h1 >= MIN_H1_VOLUME else 0.0)

    price_change_h1 = abs(get_bucket(pair.get("priceChange"), ("h1", "1h")))
    price_change_m5 = abs(get_bucket(pair.get("priceChange"), ("m5", "5m")))
    price_move = max(price_change_h1, price_change_m5)

    if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
        return None
    if liquidity < MIN_LIQUIDITY:
        return None
    if volume_h1 < MIN_H1_VOLUME:
        return None
    if spike_ratio < MIN_SPIKE_RATIO:
        return None
    if price_move < MIN_PRICE_MOVE_PCT:
        return None
    if price_move > MAX_PRICE_MOVE_PCT:
        return None

    boosts_active = int(num(pair.get("boosts", {}).get("active")))
    buys_h1 = int(num(pair.get("txns", {}).get("h1", {}).get("buys")))
    sells_h1 = int(num(pair.get("txns", {}).get("h1", {}).get("sells")))
    buy_sell_ratio = buys_h1 / max(sells_h1, 1)

    if buy_sell_ratio >= BUY_SELL_RATIO_LONG_MIN:
        signal_type = "LONG_CANDIDATE"
    elif buy_sell_ratio <= BUY_SELL_RATIO_SHORT_MAX:
        signal_type = "SHORT_WATCH"
    else:
        signal_type = "RANGE_WATCH"

    score = 0.0
    score += min(liquidity / max(MIN_LIQUIDITY, 1), 5.0)
    score += min(volume_h1 / max(MIN_H1_VOLUME, 1), 5.0)
    score += min(spike_ratio / max(MIN_SPIKE_RATIO, 0.1), 5.0)
    score += min(price_move / max(MIN_PRICE_MOVE_PCT, 0.1), 5.0)
    score += min(boosts_active, 3)

    item = {
        "chain": pair.get("chainId"),
        "dex": pair.get("dexId"),
        "pair_address": str(pair.get("pairAddress") or "").strip(),
        "token_address": str(pair.get("baseToken", {}).get("address") or "").strip(),
        "symbol": pair.get("baseToken", {}).get("symbol"),
        "name": pair.get("baseToken", {}).get("name"),
        "quote": pair.get("quoteToken", {}).get("symbol"),
        "price_usd": num(pair.get("priceUsd")),
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume_h1": volume_h1,
        "volume_h24": volume_h24,
        "spike_ratio": spike_ratio,
        "price_move": price_move,
        "buys_h1": buys_h1,
        "sells_h1": sells_h1,
        "buy_sell_ratio": round(buy_sell_ratio, 3),
        "signal_type": signal_type,
        "boosts_active": boosts_active,
        "pair_url": pair.get("url") or "https://dexscreener.com",
        "score": round(score, 2),
    }
    item["confidence"] = confidence_score(item)
    item.update(evaluate_context(item))

    if not item["pair_address"] or not item["token_address"]:
        return None
    return item


# -----------------------------
# Decision engine
# -----------------------------
def should_open_grid(item: Dict[str, Any]) -> bool:
    if not GRID_ENABLED:
        return False
    if GRID_ONLY_FOR_RANGE and item.get("signal_type") != "RANGE_WATCH":
        return False
    price_move = num(item.get("price_move"))
    spike = num(item.get("spike_ratio"))
    liquidity = num(item.get("liquidity"))
    return liquidity >= 40000 and price_move <= 18 and spike <= 3.5


def decide_action(item: Dict[str, Any]) -> str:
    signal_type = item.get("signal_type")
    confidence = num(item.get("confidence"))
    phase = item.get("phase", "")
    risk = item.get("risk", "")

    if should_open_grid(item):
        return "OPEN_GRID"

    if signal_type == "LONG_CANDIDATE":
        if confidence >= 75 and "ΝΩΡΙΣ" in phase:
            return "OPEN_SPOT_LONG"
        if confidence >= 60:
            return "OPEN_FUTURES_LONG"
        return "WATCH"

    if signal_type == "SHORT_WATCH":
        if confidence >= 65 and "ΥΨΗΛΟ" not in risk:
            return "OPEN_FUTURES_SHORT"
        return "WATCH"

    return "HOLD"


def compute_position_pct(action: str, item: Dict[str, Any], state: Dict[str, Any]) -> float:
    confidence = num(item.get("confidence"))
    risk = item.get("risk", "")
    phase = item.get("phase", "")

    if action == "OPEN_SPOT_LONG":
        base = SPOT_BASE_POS_PCT
        if confidence >= 80 and "ΝΩΡΙΣ" in phase:
            base = SPOT_MAX_POS_PCT
        elif confidence < 65:
            base = SPOT_MIN_POS_PCT
        if "ΥΨΗΛΟ" in risk:
            base *= 0.6
        return clip(base, SPOT_MIN_POS_PCT, SPOT_MAX_POS_PCT)

    if action in {"OPEN_FUTURES_LONG", "OPEN_FUTURES_SHORT"}:
        base = FUTURES_BASE_POS_PCT
        if confidence >= 80:
            base = FUTURES_MAX_POS_PCT
        elif confidence < 65:
            base = FUTURES_MIN_POS_PCT
        if "ΥΨΗΛΟ" in risk:
            base *= 0.7
        return clip(base, FUTURES_MIN_POS_PCT, FUTURES_MAX_POS_PCT)

    if action == "OPEN_GRID":
        return GRID_CAPITAL_PCT

    return 0.0


def last_alerted_recently(item: Dict[str, Any], state: Dict[str, Any]) -> bool:
    last_alerted = state.get("last_alerted", {})
    state_key = f"{item['chain']}:{item['pair_address']}:{item['signal_type']}"
    now = int(time.time())
    last = int(num(last_alerted.get(state_key), 0))
    return now - last < ALERT_COOLDOWN_MIN * 60


def mark_alerted(item: Dict[str, Any], state: Dict[str, Any]) -> None:
    last_alerted = state.setdefault("last_alerted", {})
    state_key = f"{item['chain']}:{item['pair_address']}:{item['signal_type']}"
    last_alerted[state_key] = int(time.time())


# -----------------------------
# Execution engine (demo only)
# -----------------------------
def create_position(action: str, item: Dict[str, Any], state: Dict[str, Any]) -> Optional[Position]:
    trading_balance = num(state.get("trading_balance"))
    if trading_balance <= 0:
        return None

    pos_pct = compute_position_pct(action, item, state)
    size_usd = round(trading_balance * pos_pct, 4)
    if size_usd <= 0:
        return None

    entry_price = num(item.get("price_usd"))
    if entry_price <= 0:
        return None

    leverage = 1.0
    side = "SPOT_LONG"
    if action == "OPEN_FUTURES_LONG":
        side = "FUTURES_LONG"
        leverage = FUTURES_LEVERAGE
    elif action == "OPEN_FUTURES_SHORT":
        side = "FUTURES_SHORT"
        leverage = FUTURES_LEVERAGE
    elif action == "OPEN_GRID":
        side = "GRID"
        leverage = 1.0

    effective_exposure = size_usd * leverage
    quantity = effective_exposure / entry_price

    if side in {"SPOT_LONG", "FUTURES_LONG", "GRID"}:
        stop_loss = entry_price * (1.0 - LONG_INITIAL_SL_PCT)
        take_profit = entry_price * (1.0 + LONG_INITIAL_TP_PCT)
    else:
        stop_loss = entry_price * (1.0 + SHORT_INITIAL_SL_PCT)
        take_profit = entry_price * (1.0 - SHORT_INITIAL_TP_PCT)

    pos = Position(
        id=f"pos-{int(time.time() * 1000)}-{random.randint(1000, 9999)}",
        symbol=str(item.get("symbol") or "UNKNOWN"),
        name=str(item.get("name") or ""),
        chain=str(item.get("chain") or ""),
        pair_address=str(item.get("pair_address") or ""),
        token_address=str(item.get("token_address") or ""),
        pair_url=str(item.get("pair_url") or "https://dexscreener.com"),
        side=side,
        status="OPEN",
        entry_price=entry_price,
        current_price=entry_price,
        size_usd=size_usd,
        quantity=quantity,
        leverage=leverage,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=num(item.get("confidence")),
        score=num(item.get("score")),
        phase=str(item.get("phase") or ""),
        risk=str(item.get("risk") or ""),
        opened_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        notes=f"Action={action}",
    )

    # Reserve demo capital from trading balance
    state["trading_balance"] = round(trading_balance - size_usd, 4)
    return pos


def build_open_message(pos: Position) -> str:
    return "\n".join([
        "🤖 <b>DEMO BOT ΑΝΟΙΞΕ ΘΕΣΗ</b>",
        f"<b>{pos.symbol}</b> | {pos.chain}",
        f"Τύπος: <b>{pos.side}</b>",
        f"Τιμή εισόδου: <b>${pos.entry_price:.8f}</b>",
        f"Μέγεθος θέσης: <b>{fmt_money(pos.size_usd)}</b>",
        f"Μόχλευση: <b>{pos.leverage:.1f}x</b>",
        f"SL: <b>${pos.stop_loss:.8f}</b>",
        f"TP: <b>${pos.take_profit:.8f}</b>",
        f"Confidence: <b>{pos.confidence:.2f}</b>",
        f"Φάση: <b>{pos.phase}</b>",
        f"Ρίσκο: <b>{pos.risk}</b>",
        "",
        f"<a href=\"{pos.pair_url}\">Άνοιγμα στο DexScreener</a>",
    ])


def current_price_from_pair(pair: Dict[str, Any]) -> float:
    return num(pair.get("priceUsd"))


def unrealized_pnl(pos: Position, price: float) -> tuple[float, float]:
    if pos.side in {"SPOT_LONG", "FUTURES_LONG", "GRID"}:
        pnl_pct = ((price - pos.entry_price) / pos.entry_price) if pos.entry_price > 0 else 0.0
    else:
        pnl_pct = ((pos.entry_price - price) / pos.entry_price) if pos.entry_price > 0 else 0.0
    pnl_usd = pos.size_usd * pnl_pct * pos.leverage
    return pnl_usd, pnl_pct * 100.0


def close_position(pos: Position, price: float, state: Dict[str, Any], reason: str) -> Position:
    pnl_usd, pnl_pct = unrealized_pnl(pos, price)
    pos.current_price = price
    pos.exit_price = price
    pos.realized_pnl_usd = round(pnl_usd, 4)
    pos.realized_pnl_pct = round(pnl_pct, 4)
    pos.closed_at = utc_now_iso()
    pos.updated_at = pos.closed_at
    pos.status = "CLOSED"
    pos.notes = (pos.notes + f" | close_reason={reason}").strip()

    trading_balance = num(state.get("trading_balance"))
    safe_balance = num(state.get("safe_balance"))

    # Return reserved capital
    trading_balance += pos.size_usd

    # Realized PnL split
    if pnl_usd > 0:
        safe_cut = pnl_usd * SAFE_PROFIT_PCT
        reinvest_cut = pnl_usd * REINVEST_PCT
        safe_balance += safe_cut
        trading_balance += reinvest_cut
        state["stats"]["wins"] = int(state["stats"].get("wins", 0)) + 1
    else:
        trading_balance += pnl_usd
        state["stats"]["losses"] = int(state["stats"].get("losses", 0)) + 1

    state["stats"]["total_trades"] = int(state["stats"].get("total_trades", 0)) + 1
    state["stats"]["total_realized_pnl"] = round(
        num(state["stats"].get("total_realized_pnl")) + pnl_usd,
        4,
    )

    state["trading_balance"] = round(trading_balance, 4)
    state["safe_balance"] = round(safe_balance, 4)

    append_journal({
        "event": "CLOSE_POSITION",
        "timestamp": utc_now_iso(),
        "reason": reason,
        "position": asdict(pos),
        "balances": {
            "trading_balance": state["trading_balance"],
            "safe_balance": state["safe_balance"],
        },
    })

    return pos


def partial_close(pos: Position, price: float, state: Dict[str, Any], pct: float, reason: str) -> None:
    pct = clip(pct, 0.0, 1.0)
    if pct <= 0 or pct >= 1:
        return

    pnl_usd, pnl_pct = unrealized_pnl(pos, price)
    portion_size = pos.size_usd * pct
    portion_pnl = pnl_usd * pct
    pos.size_usd = round(pos.size_usd * (1.0 - pct), 4)
    pos.quantity = pos.quantity * (1.0 - pct)
    pos.current_price = price
    pos.updated_at = utc_now_iso()
    pos.partial_exit_count += 1
    pos.notes += f" | partial_close={pct:.2f} reason={reason}"

    trading_balance = num(state.get("trading_balance")) + portion_size
    safe_balance = num(state.get("safe_balance"))

    if portion_pnl > 0:
        safe_cut = portion_pnl * SAFE_PROFIT_PCT
        reinvest_cut = portion_pnl * REINVEST_PCT
        safe_balance += safe_cut
        trading_balance += reinvest_cut
    else:
        trading_balance += portion_pnl

    state["trading_balance"] = round(trading_balance, 4)
    state["safe_balance"] = round(safe_balance, 4)

    append_journal({
        "event": "PARTIAL_CLOSE",
        "timestamp": utc_now_iso(),
        "reason": reason,
        "pct": pct,
        "price": price,
        "portion_pnl_usd": round(portion_pnl, 4),
        "portion_pnl_pct": round(pnl_pct * pct, 4),
        "position_id": pos.id,
        "symbol": pos.symbol,
        "balances": {
            "trading_balance": state["trading_balance"],
            "safe_balance": state["safe_balance"],
        },
    })


def maybe_scale_in(pos: Position, item: Dict[str, Any], state: Dict[str, Any]) -> None:
    # Scale in only if trade continues strongly and not too many times
    if pos.scale_in_count >= 2:
        return

    price = num(item.get("price_usd"))
    confidence = num(item.get("confidence"))
    if price <= 0 or confidence < 75:
        return

    pnl_usd, pnl_pct = unrealized_pnl(pos, price)
    if pnl_pct < 2.5:
        return

    trade_balance = num(state.get("trading_balance"))
    if trade_balance <= 0:
        return

    add_pct = 0.03 if pos.side.startswith("FUTURES") else 0.05
    add_size = min(trade_balance * add_pct, pos.size_usd * 0.5)
    if add_size <= 0:
        return

    qty_add = (add_size * pos.leverage) / price
    pos.size_usd = round(pos.size_usd + add_size, 4)
    pos.quantity += qty_add
    pos.scale_in_count += 1
    pos.current_price = price
    pos.updated_at = utc_now_iso()
    pos.notes += f" | scale_in={add_size:.2f}"
    state["trading_balance"] = round(trade_balance - add_size, 4)

    append_journal({
        "event": "SCALE_IN",
        "timestamp": utc_now_iso(),
        "position_id": pos.id,
        "symbol": pos.symbol,
        "added_size_usd": round(add_size, 4),
        "price": price,
    })


def manage_position(pos: Position, item: Dict[str, Any], state: Dict[str, Any]) -> Optional[Position]:
    price = num(item.get("price_usd"))
    if price <= 0:
        return pos

    pos.current_price = price
    pos.updated_at = utc_now_iso()

    pnl_usd, pnl_pct = unrealized_pnl(pos, price)
    confidence = num(item.get("confidence"))
    ratio = num(item.get("buy_sell_ratio"))
    price_move = num(item.get("price_move"))
    spike = num(item.get("spike_ratio"))

    # 1. Move TP and SL dynamically when price approaches TP
    if pos.side in {"SPOT_LONG", "FUTURES_LONG", "GRID"}:
        tp_progress = price / pos.take_profit if pos.take_profit > 0 else 0
        if tp_progress >= 0.98:
            if confidence >= 75 and ratio > 1.15 and spike >= 3:
                pos.take_profit *= 1.03
                pos.stop_loss = max(pos.stop_loss, price * 0.985)
                pos.tp_extended_count += 1
                pos.notes += " | tp_extended_long"
            else:
                if pos.partial_exit_count == 0:
                    partial_close(pos, price, state, PARTIAL_TAKE_PROFIT_PCT, "first_weakness_near_tp")
                pos.stop_loss = max(pos.stop_loss, price * 0.99)

        # Partial exits on weakness
        if pnl_pct >= 4.0 and ratio < 1.02 and pos.partial_exit_count == 0:
            partial_close(pos, price, state, PARTIAL_TAKE_PROFIT_PCT, "momentum_weakening")
            pos.stop_loss = max(pos.stop_loss, price * 0.99)

        if pnl_pct >= 6.0 and ratio < 0.98 and pos.partial_exit_count == 1:
            partial_close(pos, price, state, SECOND_PARTIAL_TAKE_PROFIT_PCT, "second_weakness")
            pos.stop_loss = max(pos.stop_loss, price * 0.992)

        # Stop or full TP close
        if price <= pos.stop_loss:
            return close_position(pos, price, state, "stop_loss_hit")

        if price >= pos.take_profit and pos.tp_extended_count >= 2:
            return close_position(pos, price, state, "take_profit_hit_after_extensions")

    else:
        # FUTURES_SHORT
        tp_progress = pos.take_profit / price if price > 0 else 0
        if tp_progress >= 0.98:
            if confidence >= 75 and ratio < 0.90 and spike >= 3:
                pos.take_profit *= 0.97
                pos.stop_loss = min(pos.stop_loss, price * 1.015)
                pos.tp_extended_count += 1
                pos.notes += " | tp_extended_short"
            else:
                if pos.partial_exit_count == 0:
                    partial_close(pos, price, state, PARTIAL_TAKE_PROFIT_PCT, "first_weakness_near_tp_short")
                pos.stop_loss = min(pos.stop_loss, price * 1.01)

        if pnl_pct >= 4.0 and ratio > 0.98 and pos.partial_exit_count == 0:
            partial_close(pos, price, state, PARTIAL_TAKE_PROFIT_PCT, "short_momentum_weakening")
            pos.stop_loss = min(pos.stop_loss, price * 1.01)

        if pnl_pct >= 6.0 and ratio > 1.02 and pos.partial_exit_count == 1:
            partial_close(pos, price, state, SECOND_PARTIAL_TAKE_PROFIT_PCT, "second_weakness_short")
            pos.stop_loss = min(pos.stop_loss, price * 1.008)

        if price >= pos.stop_loss:
            return close_position(pos, price, state, "stop_loss_hit_short")

        if price <= pos.take_profit and pos.tp_extended_count >= 2:
            return close_position(pos, price, state, "take_profit_hit_after_extensions_short")

    maybe_scale_in(pos, item, state)
    return pos


# -----------------------------
# Telegram / reporting helpers
# -----------------------------
def build_watch_message(item: Dict[str, Any], action: str) -> str:
    action_map = {
        "OPEN_SPOT_LONG": "Άνοιγμα demo spot αγοράς",
        "OPEN_FUTURES_LONG": "Άνοιγμα demo long futures",
        "OPEN_FUTURES_SHORT": "Άνοιγμα demo short futures",
        "OPEN_GRID": "Άνοιγμα demo grid",
        "WATCH": "Παρακολούθηση",
        "HOLD": "Καμία ενέργεια",
    }
    title = action_map.get(action, action)
    warning = item.get("warning") or ""
    lines = [
        "📡 <b>DEMO BOT ΑΠΟΦΑΣΗ</b>",
        f"<b>{item.get('symbol') or 'ΑΓΝΩΣΤΟ'}</b> | {item.get('chain') or '-'} | {item.get('dex') or '-'}",
        f"Ενέργεια: <b>{title}</b>",
        f"Confidence: <b>{num(item.get('confidence')):.2f}</b>",
        f"Φάση: <b>{item.get('phase') or '-'}</b>",
        f"Ρίσκο: <b>{item.get('risk') or '-'}</b>",
        f"Τιμή: <b>${num(item.get('price_usd')):.8f}</b>",
        f"Κεφαλαιοποίηση: <b>{fmt_money(num(item.get('market_cap')))}</b>",
        f"Ρευστότητα: <b>{fmt_money(num(item.get('liquidity')))}</b>",
        f"Όγκος 1 ώρας: <b>{fmt_money(num(item.get('volume_h1')))}</b>",
        f"Ένταση κίνησης: <b>{num(item.get('spike_ratio')):.2f}x</b>",
        f"Μεταβολή τιμής: <b>{num(item.get('price_move')):.2f}%</b>",
        f"Αναλογία αγορών/πωλήσεων: <b>{num(item.get('buy_sell_ratio')):.3f}</b>",
        f"Βαθμολογία: <b>{num(item.get('score')):.2f}</b>",
    ]
    if warning:
        lines.extend(["", f"<b>{warning}</b>"])
    lines.extend(["", f"<a href=\"{item.get('pair_url') or 'https://dexscreener.com'}\">Άνοιγμα στο DexScreener</a>"])
    return "\n".join(lines)


def build_close_message(pos: Position) -> str:
    icon = "🟢" if pos.realized_pnl_usd >= 0 else "🔴"
    return "\n".join([
        f"{icon} <b>DEMO BOT ΕΚΛΕΙΣΕ ΘΕΣΗ</b>",
        f"<b>{pos.symbol}</b> | {pos.chain}",
        f"Τύπος: <b>{pos.side}</b>",
        f"Είσοδος: <b>${pos.entry_price:.8f}</b>",
        f"Έξοδος: <b>${num(pos.exit_price):.8f}</b>",
        f"Αποτέλεσμα: <b>{pos.realized_pnl_pct:.2f}%</b>",
        f"PnL: <b>{fmt_money(pos.realized_pnl_usd)}</b>",
        f"Scale-in: <b>{pos.scale_in_count}</b>",
        f"Μερικές έξοδοι: <b>{pos.partial_exit_count}</b>",
        f"Σημειώσεις: <b>{pos.notes}</b>",
        "",
        f"<a href=\"{pos.pair_url}\">Άνοιγμα στο DexScreener</a>",
    ])


# -----------------------------
# Main cycle
# -----------------------------
def position_matches_item(pos: Dict[str, Any], item: Dict[str, Any]) -> bool:
    return pos.get("pair_address") == item.get("pair_address")


def run_cycle() -> None:
    state = load_state()
    now = utc_now_iso()
    print(f"[info] demo cycle started at {now}")
    print(
        f"[info] balances | trading={state['trading_balance']:.2f} safe={state['safe_balance']:.2f} "
        f"open_positions={len(state.get('open_positions', []))}"
    )

    candidates = get_candidate_tokens()
    print(f"[info] candidates discovered: {len(candidates)}")

    analyzed: List[Dict[str, Any]] = []
    for chain, token in candidates:
        try:
            pairs = fetch_pairs(chain, token)
            pair = choose_best_pair(pairs)
            if not pair:
                continue
            item = analyze_pair(pair)
            if not item:
                continue
            analyzed.append(item)
        except Exception as exc:
            print(f"[error] analyzing {chain}/{token}: {exc}")
            continue

    analyzed.sort(key=lambda x: (num(x.get("confidence")), num(x.get("score"))), reverse=True)
    top_items = analyzed[:TOP_N]
    print(f"[info] hits after filters: {len(top_items)}")

    # Refresh open positions using newly analyzed items
    open_positions = state.get("open_positions", [])
    updated_open_positions: List[Dict[str, Any]] = []
    closed_any = False

    for pos_dict in open_positions:
        pos = Position(**pos_dict)
        matching_item = next((x for x in analyzed if position_matches_item(pos_dict, x)), None)
        if matching_item is None:
            updated_open_positions.append(asdict(pos))
            continue

        managed = manage_position(pos, matching_item, state)
        if managed and managed.status == "CLOSED":
            state.setdefault("closed_positions", []).append(asdict(managed))
            send_telegram(build_close_message(managed))
            closed_any = True
        else:
            updated_open_positions.append(asdict(managed or pos))

    state["open_positions"] = updated_open_positions

    # Open new positions if capacity available
    capacity = MAX_OPEN_POSITIONS - len(state.get("open_positions", []))
    if capacity > 0:
        for item in top_items:
            if capacity <= 0:
                break

            already_open = any(position_matches_item(p, item) for p in state.get("open_positions", []))
            if already_open:
                continue

            action = decide_action(item)
            if action in {"HOLD", "WATCH"}:
                if not last_alerted_recently(item, state):
                    send_telegram(build_watch_message(item, action))
                    mark_alerted(item, state)
                continue

            if last_alerted_recently(item, state):
                continue

            pos = create_position(action, item, state)
            if not pos:
                continue

            state.setdefault("open_positions", []).append(asdict(pos))
            append_journal({
                "event": "OPEN_POSITION",
                "timestamp": utc_now_iso(),
                "position": asdict(pos),
                "balances": {
                    "trading_balance": state["trading_balance"],
                    "safe_balance": state["safe_balance"],
                },
            })
            send_telegram(build_open_message(pos))
            mark_alerted(item, state)
            capacity -= 1

    # Save summary and state
    save_summary(state)
    save_state(state)
    print(
        f"[info] demo cycle complete | trading={state['trading_balance']:.2f} safe={state['safe_balance']:.2f} "
        f"open={len(state.get('open_positions', []))} closed={len(state.get('closed_positions', []))}"
    )


if __name__ == "__main__":
    try:
        run_cycle()
    except Exception as exc:
        print(f"[fatal] demo bot crashed: {exc}")
        raise
