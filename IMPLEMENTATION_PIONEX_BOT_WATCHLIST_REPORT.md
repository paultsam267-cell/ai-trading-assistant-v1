# Implementation Report: Pionex Bot Watchlist V1

## Added

- `pionex_bot_watchlist.py`
- `tests/test_pionex_bot_watchlist.py`
- Dashboard card: `🤖 Pionex Bot Watchlist`
- Route: `GET /pionex-bots`
- Route: `POST /pionex-bots/add`
- `README_PIONEX_BOT_WATCHLIST.md`

## Runtime-created files

The module creates these automatically if missing:

- `intelligence_state/pionex_bot_watchlist.json`
- `intelligence_state/pionex_bot_snapshots/`
- `intelligence_state/pionex_bot_summary.json`

## Safety guarantees

- Manual snapshots only.
- No API key.
- No scraping.
- No account connection.
- No real orders.
- PAPER TRADING ONLY.
- Existing wallet, reserve, scanner, risk engine and synthetic short rules are not reset or modified.

## Tests

Validated locally with:

`python -m unittest discover -s tests -v`

Result:

`Ran 52 tests - OK`
