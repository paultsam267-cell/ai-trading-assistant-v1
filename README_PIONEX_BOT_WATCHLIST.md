# Pionex Bot Watchlist V1

Manual, read-only Pionex bot leaderboard snapshot module for the AI Trading Assistant V1.

## Safety

- PAPER TRADING ONLY.
- No Pionex account login.
- No API key.
- No scraping.
- No real orders.
- No seed phrase or private key.
- Whale flow is intentionally not part of this pass.

## Dashboard

Open the normal dashboard and click:

`🤖 Pionex Bot Watchlist`

or open:

`http://127.0.0.1:5000/pionex-bots`

## Manual snapshot format

Paste JSON in this shape:

```json
{
  "bots": [
    {
      "pair": "RIF/USDT",
      "market": "perp",
      "direction": "SHORT",
      "leverage": 50,
      "roi_percent": 842.0,
      "profit_percent": null,
      "bot_age_text": "6h 20m",
      "rank": 1,
      "source": "manual_pionex_snapshot",
      "notes": ""
    }
  ]
}
```

Missing fields are tolerated and defaulted safely.

## What it calculates

- total_bots_watched
- long_count / short_count / unknown_count
- top_long_pairs / top_short_pairs
- repeated_pairs
- high_leverage_count
- extreme_roi_count
- leaderboard_bias
- crowding_state
- freshness_state
- action_effect

## Advisory only

For this pass the watchlist does not aggressively change the paper trader decision logic. It is shown on the dashboard and saved for later comparison. After five observation days, whale flow can be added as a separate variable.
