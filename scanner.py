import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

BASE_URL = "https://api.dexscreener.com"
STATE_PATH = Path(".scanner-state.json")

SCAN_CHAINS = {c.strip().lower() for c in os.getenv("SCAN_CHAINS", "solana,bsc").split(",") if c.strip()}
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "80000"))
MAX_MARKET_CAP = float(os.getenv("MAX_MARKET_CAP", "3000000"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "30000"))
MIN_H1_VOLUME = float(os.getenv("MIN_H1_VOLUME", "30000"))
MIN_SPIKE_RATIO = float(os.getenv("MIN_SPIKE_RATIO", "2.5"))
MIN_PRICE_MOVE_PCT = float(os.getenv("MIN_PRICE_MOVE_PCT", "12"))
MAX_PRICE_MOVE_PCT = float(os.getenv("MAX_PRICE_MOVE_PCT", "1000"))
BUY_SELL_RATIO_LONG_MIN = float(os.getenv("BUY_SELL_RATIO_LONG_MIN", "1.10"))
BUY_SELL_RATIO_SHORT_MAX = float(os.getenv("BUY_SELL_RATIO_SHORT_MAX", "0.95"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "240"))
TOP_N = int(os.getenv("TOP_N", "3"))
MIN_ALERT_SCORE = float(os.getenv("MIN_ALERT_SCORE", "4.0"))
MIN_SHORT_WATCH_SCORE = float(os.getenv("MIN_SHORT_WATCH_SCORE", "4.2"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PREFERRED_QUOTES = {"USDC", "USDT", "BUSD", "WBNB", "BNB", "WSOL", "SOL"}

session = requests.Session()
session.headers.update({"User-Agent": "ai-trading-assistant-v1/1.0"})

def _tier_points(value, tiers):
    for threshold, points in sorted(tiers, key=lambda x: x[0], reverse=True):
        if value >= threshold:
            return points
    return 0.0

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


def get_json(path: str) -> Any:
    url = f"{BASE_URL}{path}"
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_bucket(data: Optional[Dict[str, Any]], keys: Iterable[str]) -> float:
    if not isinstance(data, dict):
        return 0.0
    for key in keys:
        if key in data:
            return num(data.get(key))
    return 0.0


def load_state() -> Dict[str, int]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: Dict[str, int]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_candidate_tokens() -> List[Tuple[str, str]]:
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


def pair_rank(pair: Dict[str, Any]) -> Tuple[int, float, float]:
    quote_symbol = str(pair.get("quoteToken", {}).get("symbol", "")).upper()
    preferred = 1 if quote_symbol in PREFERRED_QUOTES else 0
    liquidity = num(pair.get("liquidity", {}).get("usd"))
    volume_h24 = get_bucket(pair.get("volume"), ("h24", "24h"))
    return preferred, liquidity, volume_h24


def choose_best_pair(pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pairs:
        return None
    return max(pairs, key=pair_rank)


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
        return None

    score = 0.0
    score += min(liquidity / max(MIN_LIQUIDITY, 1), 5.0)
    score += min(volume_h1 / max(MIN_H1_VOLUME, 1), 5.0)
    score += min(spike_ratio / max(MIN_SPIKE_RATIO, 0.1), 5.0)
    score += min(price_move / max(MIN_PRICE_MOVE_PCT, 0.1), 5.0)
    score += min(boosts_active, 3)
    
    if signal_type == "LONG_CANDIDATE" and score < MIN_ALERT_SCORE:
        return None
    
    if signal_type == "SHORT_WATCH" and score < MIN_SHORT_WATCH_SCORE:
        return None
   
    return {
        "chain": pair.get("chainId"),
        "dex": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "token_address": pair.get("baseToken", {}).get("address"),
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
        "pair_url": pair.get("url"),
        "score": round(score, 2),
    }


def fmt_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value/1_000:.1f}K"
    return f"${value:.0f}"


def build_message(item: Dict[str, Any]) -> str:
    if item["signal_type"] == "LONG_CANDIDATE":
        signal_emoji = "🟢"
        signal_title = "LONG_CANDIDATE"
        bias_line = "Bias: bullish continuation"
    else:
        signal_emoji = "🟠"
        signal_title = "SHORT_WATCH"
        bias_line = "Bias: possible exhaustion / fade"

    body = [
        f"{signal_emoji} <b>{signal_title}</b>",
        f"<b>{item['symbol']}</b> | {item['chain']} | {item['dex']}",
        f"{item['name']}",
        f"{bias_line}",
        "",
        f"Price: <b>${item['price_usd']:.8f}</b>",
        f"Market Cap: <b>{fmt_money(item['market_cap'])}</b>",
        f"Liquidity: <b>{fmt_money(item['liquidity'])}</b>",
        f"H1 Volume: <b>{fmt_money(item['volume_h1'])}</b>",
        f"Spike Ratio: <b>{item['spike_ratio']:.2f}x</b>",
        f"Price Move: <b>{item['price_move']:.2f}%</b>",
        f"H1 Txns: <b>{item['buys_h1']} buys / {item['sells_h1']} sells</b>",
        f"Buy/Sell Ratio: <b>{item['buy_sell_ratio']:.3f}</b>",
        f"Boosts: <b>{item['boosts_active']}</b>",
        f"Score: <b>{item['score']}</b>",
        "",
        f"<a href=\"{item['pair_url']}\">Open on DexScreener</a>",
        f"<code>{item['token_address']}</code>",
    ]
    return "\n".join(body)


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = session.post(url, json=payload, timeout=20)
    resp.raise_for_status()


def main() -> None:
    state = load_state()
    now = int(time.time())
    cooldown_sec = ALERT_COOLDOWN_MIN * 60

    candidates = get_candidate_tokens()
    print(f"[info] candidates discovered: {len(candidates)}")

    hits: List[Dict[str, Any]] = []

    for chain, token in candidates:
        pairs = fetch_pairs(chain, token)
        pair = choose_best_pair(pairs)
        if not pair:
            continue

        item = analyze_pair(pair)
        if not item:
            continue

        state_key = f"{item['chain']}:{item['pair_address']}:{item['signal_type']}"
        last_sent = int(state.get(state_key, 0))
        if now - last_sent < cooldown_sec:
            continue

        hits.append(item)

    hits.sort(key=lambda x: x["score"], reverse=True)
    hits = hits[:TOP_N]

    print(f"[info] hits after filters: {len(hits)}")

    for item in hits:
        send_telegram(build_message(item))
        state_key = f"{item['chain']}:{item['pair_address']}:{item['signal_type']}"
        state[state_key] = now
        print(f"[sent] {item['symbol']} on {item['chain']} type={item['signal_type']} score={item['score']}")

    save_state(state)


if __name__ == "__main__":
    main()
