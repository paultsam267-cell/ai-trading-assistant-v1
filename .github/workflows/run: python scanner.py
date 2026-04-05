name: demo-trading-bot

on:
  workflow_dispatch:
  schedule:
    - cron: "2,7,12,17,22,27,32,37,42,47,52,57 * * * *"

concurrency:
  group: demo-trading-bot-${{ github.ref }}
  cancel-in-progress: true

jobs:
  run-demo-bot:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Restore demo bot state
        uses: actions/cache/restore@v4
        with:
          path: |
            .demo-bot-state.json
            demo-journal.json
            demo-summary.json
          key: demo-bot-state-${{ github.ref_name }}-${{ github.run_id }}-${{ github.run_attempt }}
          restore-keys: |
            demo-bot-state-${{ github.ref_name }}-
            demo-bot-state-

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install dependencies
        run: pip install requests

      - name: Debug trigger
        run: |
          echo "event_name=${{ github.event_name }}"
          echo "schedule=${{ github.event.schedule }}"
          echo "ref=${{ github.ref }}"
          echo "ref_name=${{ github.ref_name }}"

      - name: Run demo trading bot
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}

          SCAN_CHAINS: "solana,bsc"

          INITIAL_TRADING_BALANCE: "100"
          INITIAL_SAFE_BALANCE: "0"
          SAFE_PROFIT_PCT: "0.30"
          REINVEST_PCT: "0.70"

          SPOT_MIN_POS_PCT: "0.05"
          SPOT_BASE_POS_PCT: "0.10"
          SPOT_MAX_POS_PCT: "0.20"

          FUTURES_MIN_POS_PCT: "0.03"
          FUTURES_BASE_POS_PCT: "0.05"
          FUTURES_MAX_POS_PCT: "0.10"
          FUTURES_LEVERAGE: "2"

          MIN_MARKET_CAP: "50000"
          MAX_MARKET_CAP: "5000000"
          MIN_LIQUIDITY: "20000"
          MIN_H1_VOLUME: "15000"
          MIN_SPIKE_RATIO: "2.0"
          MIN_PRICE_MOVE_PCT: "8"
          MAX_PRICE_MOVE_PCT: "1000"

          BUY_SELL_RATIO_LONG_MIN: "1.08"
          BUY_SELL_RATIO_SHORT_MAX: "0.94"

          TOP_N: "3"
          MAX_OPEN_POSITIONS: "6"
          ALERT_COOLDOWN_MIN: "240"

          LONG_INITIAL_TP_PCT: "0.08"
          LONG_INITIAL_SL_PCT: "0.04"
          SHORT_INITIAL_TP_PCT: "0.08"
          SHORT_INITIAL_SL_PCT: "0.04"

          PARTIAL_TAKE_PROFIT_PCT: "0.30"
          SECOND_PARTIAL_TAKE_PROFIT_PCT: "0.30"

          GRID_ENABLED: "1"
          GRID_CAPITAL_PCT: "0.08"
          GRID_LEVELS: "5"
          GRID_RANGE_PCT: "0.06"
          GRID_ONLY_FOR_RANGE: "1"
        run: python demo_trading_bot_v1.py

      - name: Show summary
        if: always()
        run: |
          echo "----- DEMO SUMMARY -----"
          if [ -f demo-summary.json ]; then
            cat demo-summary.json
          else
            echo "demo-summary.json not found"
          fi
          echo "----- END SUMMARY -----"

      - name: Save demo bot state
        if: always()
        uses: actions/cache/save@v4
        with:
          path: |
            .demo-bot-state.json
            demo-journal.json
            demo-summary.json
          key: demo-bot-state-${{ github.ref_name }}-${{ github.run_id }}-${{ github.run_attempt }}
