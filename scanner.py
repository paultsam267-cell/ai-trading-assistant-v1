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


def classify_candidate(pair: Dict[str, Any]) -> str:
    change_h24 = safe_float(pair.get("priceChange", {}).get("h24"))
    buys_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("buys"))
    sells_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("sells"))

    if change_h24 >= 0 and buys_h1 > sells_h1:
        return "LONG"
    if change_h24 < 0 and sells_h1 > buys_h1:
        return "SHORT_WATCH"
    return "NEUTRAL"


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
