import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://api.dexscreener.com"
ATHENS_TZ = ZoneInfo("Europe/Athens")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ai-trading-scanner-demo/1.0"})

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCAN_CHAINS = {c.strip().lower() for c in os.getenv("SCAN_CHAINS", "solana,bsc").split(",") if c.strip()}
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "80000"))
MAX_MARKET_CAP = float(os.getenv("MAX_MARKET_CAP", "3000000"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "30000"))
MIN_H24_VOLUME = float(os.getenv("MIN_H24_VOLUME", "50000"))
MAX_AGE_HOURS = float(os.getenv("MAX_AGE_HOURS", "72"))
MAX_TOKENS_PER_CHAIN = int(os.getenv("MAX_TOKENS_PER_CHAIN", "60"))
MAX_REPORT_ITEMS = int(os.getenv("MAX_REPORT_ITEMS", "8"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "35"))

LATEST_PROFILES_ENDPOINT = f"{BASE_URL}/token-profiles/latest/v1"
TOP_BOOSTS_ENDPOINT = f"{BASE_URL}/token-boosts/top/v1"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ)


def get_json(url: str) -> Any:
    response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def format_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    if value >= 1:
        return f"${value:.2f}"
    return f"${value:.6f}"


def format_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def format_price(value: float) -> str:
    if value >= 1:
        return f"${value:.4f}"
    if value >= 0.01:
        return f"${value:.6f}"
    if value >= 0.0001:
        return f"${value:.8f}"
    return f"${value:.10f}"


def hours_since(timestamp_ms: Any) -> float:
    ts = safe_float(timestamp_ms)
    if ts <= 0:
        return 999999.0
    created = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    delta = datetime.now(timezone.utc) - created
    return delta.total_seconds() / 3600.0


def build_token_watchlist() -> Dict[str, List[str]]:
    chains_to_tokens: Dict[str, set[str]] = defaultdict(set)

    profiles = get_json(LATEST_PROFILES_ENDPOINT)
    if isinstance(profiles, list):
        for item in profiles:
            chain = str(item.get("chainId", "")).lower().strip()
            token = str(item.get("tokenAddress", "")).strip()
            if chain in SCAN_CHAINS and token:
                chains_to_tokens[chain].add(token)

    boosts = get_json(TOP_BOOSTS_ENDPOINT)
    if isinstance(boosts, list):
        for item in boosts:
            chain = str(item.get("chainId", "")).lower().strip()
            token = str(item.get("tokenAddress", "")).strip()
            if chain in SCAN_CHAINS and token:
                chains_to_tokens[chain].add(token)

    final_map: Dict[str, List[str]] = {}
    for chain, tokens in chains_to_tokens.items():
        final_map[chain] = list(tokens)[:MAX_TOKENS_PER_CHAIN]

    return final_map


def fetch_pairs_for_chain(chain: str, token_addresses: List[str]) -> List[Dict[str, Any]]:
    all_pairs: List[Dict[str, Any]] = []

    for batch in chunked(token_addresses, 30):
        url = f"{BASE_URL}/tokens/v1/{chain}/" + ",".join(batch)
        payload = get_json(url)

        if isinstance(payload, list):
            pairs = payload
        elif isinstance(payload, dict):
            pairs = payload.get("pairs", []) or []
        else:
            pairs = []

        for pair in pairs:
            if isinstance(pair, dict):
                all_pairs.append(pair)

        time.sleep(0.25)

    return all_pairs


def select_best_pairs(raw_pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_token: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for pair in raw_pairs:
        chain = str(pair.get("chainId", "")).lower().strip()
        token = str(pair.get("baseToken", {}).get("address", "")).strip()
        if not chain or not token:
            continue

        current_score = (
            safe_float(pair.get("liquidity", {}).get("usd")) * 1.0
            + safe_float(pair.get("volume", {}).get("h24")) * 0.35
            + safe_float(pair.get("boosts", {}).get("active")) * 5000
        )

        key = (chain, token)
        existing = best_by_token.get(key)
        if existing is None:
            best_by_token[key] = pair
            continue

        existing_score = (
            safe_float(existing.get("liquidity", {}).get("usd")) * 1.0
            + safe_float(existing.get("volume", {}).get("h24")) * 0.35
            + safe_float(existing.get("boosts", {}).get("active")) * 5000
        )

        if current_score > existing_score:
            best_by_token[key] = pair

    return list(best_by_token.values())


def score_pair(pair: Dict[str, Any]) -> float:
    liquidity = safe_float(pair.get("liquidity", {}).get("usd"))
    volume_h24 = safe_float(pair.get("volume", {}).get("h24"))
    change_h24 = safe_float(pair.get("priceChange", {}).get("h24"))
    buys_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("buys"))
    sells_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("sells"))
    boosts = safe_float(pair.get("boosts", {}).get("active"))
    age_h = hours_since(pair.get("pairCreatedAt"))

    score = 0.0
    score += min(liquidity / 50_000, 3.0) * 18
    score += min(volume_h24 / max(liquidity, 1.0), 3.0) * 16

    if change_h24 > 0:
        score += min(change_h24, 80) * 0.35
    else:
        score += max(change_h24, -30) * 0.20

    if buys_h1 > sells_h1:
        score += min(buys_h1 - sells_h1, 40) * 0.7
    else:
        score -= min(sells_h1 - buys_h1, 40) * 0.5

    score += min(boosts, 10) * 2.5

    if age_h <= 24:
        score += 10
    elif age_h <= 72:
        score += 6
    elif age_h <= 168:
        score += 3

    return round(max(score, 0.0), 2)


def is_candidate(pair: Dict[str, Any]) -> bool:
    chain = str(pair.get("chainId", "")).lower().strip()
    if chain not in SCAN_CHAINS:
        return False

    market_cap = safe_float(pair.get("marketCap")) or safe_float(pair.get("fdv"))
    liquidity = safe_float(pair.get("liquidity", {}).get("usd"))
    volume_h24 = safe_float(pair.get("volume", {}).get("h24"))
    age_h = hours_since(pair.get("pairCreatedAt"))

    if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
        return False
    if liquidity < MIN_LIQUIDITY:
        return False
    if volume_h24 < MIN_H24_VOLUME:
        return False
    if age_h > MAX_AGE_HOURS:
        return False
    if score_pair(pair) < MIN_SCORE:
        return False

    return True


def build_report_items(candidates: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []

    for idx, pair in enumerate(candidates[:MAX_REPORT_ITEMS], start=1):
        base = pair.get("baseToken", {}) or {}
        symbol = str(base.get("symbol") or "?").upper()
        name = str(base.get("name") or symbol)
        chain = str(pair.get("chainId", "?")).lower()
        dex = str(pair.get("dexId", "?"))
        price_usd = safe_float(pair.get("priceUsd"))
        market_cap = safe_float(pair.get("marketCap")) or safe_float(pair.get("fdv"))
        liquidity = safe_float(pair.get("liquidity", {}).get("usd"))
        volume_h24 = safe_float(pair.get("volume", {}).get("h24"))
        change_h24 = safe_float(pair.get("priceChange", {}).get("h24"))
        buys_h1 = int(safe_float(pair.get("txns", {}).get("h1", {}).get("buys")))
        sells_h1 = int(safe_float(pair.get("txns", {}).get("h1", {}).get("sells")))
        boosts = int(safe_float(pair.get("boosts", {}).get("active")))
        age_h = hours_since(pair.get("pairCreatedAt"))
        url = str(pair.get("url", "")).strip()
        score = score_pair(pair)

        lines.append(
            "\n".join(
                [
                    f"{idx}. {symbol} | {name}",
                    f"   Score: {score} | Chain: {chain} | DEX: {dex}",
                    f"   Price: {format_price(price_usd)} | MC: {format_money(market_cap)} | Liq: {format_money(liquidity)}",
                    f"   Vol24h: {format_money(volume_h24)} | 24h: {format_pct(change_h24)} | H1 B/S: {buys_h1}/{sells_h1}",
                    f"   Age: {age_h:.1f}h | Boosts: {boosts}",
                    f"   {url}",
                ]
            )
        )

    return lines


def build_daily_report(candidates: List[Dict[str, Any]]) -> str:
    ts = now_local().strftime("%d/%m/%Y %H:%M")
    header = [
        "AI Trading Scanner | DAILY DEMO REPORT",
        f"ώρα Ελλάδας: {ts}",
        "",
        "Φίλτρα:",
        f"- Chains: {', '.join(sorted(SCAN_CHAINS))}",
        f"- Market Cap: {format_money(MIN_MARKET_CAP)} έως {format_money(MAX_MARKET_CAP)}",
        f"- Liquidity >= {format_money(MIN_LIQUIDITY)}",
        f"- Volume 24h >= {format_money(MIN_H24_VOLUME)}",
        f"- Max age: {MAX_AGE_HOURS:.0f}h",
        f"- Min score: {MIN_SCORE:.1f}",
        "",
    ]

    if not candidates:
        header.append("Δεν βρέθηκαν tokens που να περνούν τα φίλτρα σήμερα.")
        header.append("")
        header.append("Demo only. Όχι financial advice.")
        return "\n".join(header)

    body = ["Top candidates:", ""]
    body.extend(build_report_items(candidates))
    footer = ["", "Demo only. Όχι financial advice."]
    return "\n".join(header + body + footer)


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN ή TELEGRAM_CHAT_ID λείπει. Εκτυπώνω μόνο το report:\n")
        print(text)
        return

    max_len = 3900
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]

    for chunk in chunks:
        response = SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        time.sleep(1.0)


def main() -> None:
    print("Ξεκινάω scan...")
    print(f"Ελάχιστο score φίλτρου: {MIN_SCORE}")
    watchlist = build_token_watchlist()

    if not watchlist:
        raise RuntimeError("Δεν βρέθηκαν token addresses από DexScreener profile/boost endpoints.")

    raw_pairs: List[Dict[str, Any]] = []
    for chain, tokens in watchlist.items():
        if not tokens:
            continue
        print(f"Φέρνω pairs για {chain}: {len(tokens)} tokens")
        raw_pairs.extend(fetch_pairs_for_chain(chain, tokens))

    if not raw_pairs:
        raise RuntimeError("Δεν βρέθηκαν pairs από το DexScreener tokens endpoint.")

    best_pairs = select_best_pairs(raw_pairs)
    print(f"Καλύτερα pairs μετά την επιλογή: {len(best_pairs)}")
    candidates = [pair for pair in best_pairs if is_candidate(pair)]
    print(f"Υποψήφια pairs μετά τα filters: {len(candidates)}")
    candidates.sort(key=score_pair, reverse=True)
    print(f"Στοιχεία που μπήκαν στο report: {min(len(candidates), MAX_REPORT_ITEMS)}")
    report = build_daily_report(candidates)
    send_telegram_message(report)
    print("Ολοκληρώθηκε το daily report.")


if __name__ == "__main__":
    main()
