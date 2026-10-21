"""GitHub-hosted, stateless, PAPER-only opportunity reporting."""

import argparse
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import scanner as crypto
import market_scanner as market
import telegram_reporting as telegram


def normalized(candidate):
    raw = max(float(candidate.get("score", 0)), 0.0)
    if candidate["asset_class"] == "CRYPTO":
        return round(100.0 * (1.0 - math.exp(-raw / 100.0)), 2)
    return round(min(raw, 100.0), 2)


def crypto_rows():
    watchlist = crypto.build_token_watchlist()
    if not watchlist:
        raise RuntimeError("NO_CRYPTO_WATCHLIST")

    pairs = []
    for chain, addresses in watchlist.items():
        pairs.extend(crypto.fetch_pairs_for_chain(chain, addresses))

    if not pairs:
        raise RuntimeError("NO_CRYPTO_PAIRS")

    selected = crypto.select_best_pairs(pairs)
    selected = [p for p in selected if crypto.is_candidate(p)]

    rows = []
    for p in selected:
        base = p.get("baseToken") or {}
        rows.append({
            "asset_class": "CRYPTO",
            "symbol": base.get("symbol", "?"),
            "name": base.get("name", "?"),
            "signal": crypto.classify_candidate(p),
            "score": crypto.score_pair(p),
            "price_usd": crypto.safe_float(p.get("priceUsd")),
            "change_h24": crypto.safe_float(
                (p.get("priceChange") or {}).get("h24")
            ),
            "liquidity": crypto.safe_float(
                (p.get("liquidity") or {}).get("usd")
            ),
            "volume": crypto.safe_float(
                (p.get("volume") or {}).get("h24")
            ),
            "chain": p.get("chainId", "?"),
            "dex": p.get("dexId", "?"),
            "url": p.get("url", ""),
        })
    return rows


def market_rows():
    return market.scan_market()


def make_report(rows, warnings):
    stamp = datetime.now(ZoneInfo("Europe/Athens")).strftime(
        "%d/%m/%Y %H:%M"
    )
    ranked = sorted(rows, key=normalized, reverse=True)
    selected = ranked[:8]

    lines = [
        "P-DJD NEXUS AI TRADING ASSISTANT",
        "PAPER TRADING ONLY",
        "MULTI-ASSET OPPORTUNITY REPORT",
        "Athens time: " + stamp,
        "Candidates: " + str(len(ranked)),
        "",
    ]

    for number, row in enumerate(selected, 1):
        asset = row["asset_class"]
        lines.extend([
            f"#{number} | {row.get('symbol')} | {asset}",
            f"{row.get('name', '')}",
            f"Signal: {row.get('signal', '?')}"
            f" | Rank: {normalized(row):.2f}/100",
            f"Price: {row.get('price_usd')} "
            f"{row.get('currency', 'USD')}",
            f"Change: {row.get('change_h24', 0):+.2f}%",
        ])
        if asset == "CRYPTO":
            lines.extend([
                f"Chain: {row.get('chain')} | DEX: {row.get('dex')}",
                f"Liq: ${row.get('liquidity', 0):,.0f}"
                f" | Vol24h: ${row.get('volume', 0):,.0f}",
                str(row.get("url", "")),
            ])
        if asset == "INDEX" or row.get("analysis_only"):
            lines.append("ANALYSIS ONLY")
        lines.append("")

    for warning in warnings:
        lines.append("DATA WARNING: " + warning)

    if not selected:
        lines.append("No validated candidates.")

    lines.append("Demo only. No real trades.")
    return "\n".join(lines)


def self_test():
    sample = [
        {"asset_class": "CRYPTO", "symbol": "TEST",
         "score": 150, "signal": "LONG", "price_usd": 1.0},
        {"asset_class": "ETF", "symbol": "ETFTEST",
         "score": 70, "signal": "NEUTRAL", "price_usd": 100.0},
    ]
    assert 0 < normalized(sample[0]) <= 100
    assert normalized(sample[1]) == 70
    report = make_report(sample, [])
    assert "TEST" in report
    assert "ETFTEST" in report
    assert "PAPER TRADING ONLY" in report
    print("SELF_TEST_PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    rows = []
    warnings = []
    successful_sources = 0

    for label, scan in (
        ("DEXSCREENER", crypto_rows),
        ("YAHOO", market_rows),
    ):
        try:
            found = scan()
            rows.extend(found)
            successful_sources += 1
            print(f"{label}: {len(found)} candidates")
        except Exception as exc:
            warnings.append(f"{label}: {type(exc).__name__}")
            print(f"{label}: FAILED ({type(exc).__name__})")

    if successful_sources == 0:
        raise RuntimeError("ALL_MARKET_SOURCES_FAILED")

    report = make_report(rows, warnings)

    if not args.send:
        print(report)
        print("PREVIEW_ONLY_NO_TELEGRAM_SEND")
        return

    status = telegram.routing_status(perform_network_check=True)
    if not (
        status["bot_identity_verified"]
        and status["chat_routing_verified"]
    ):
        raise RuntimeError("TELEGRAM_ROUTING_NOT_VERIFIED")

    if not telegram.send_notification(report):
        raise RuntimeError("TELEGRAM_SEND_FAILED")

    print("TELEGRAM_REPORT_SENT")


if __name__ == "__main__":
    main()