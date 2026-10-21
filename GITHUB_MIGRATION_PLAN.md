# GitHub migration plan — approval checkpoint

No remote change has been executed.

## Verified local finding

The current `.github/workflows/scanner.yml` runs `scanner.py` and then
`paper_trader.py` on a GitHub-hosted runner. That runner cannot preserve the
authoritative PAPER portfolio and the workflow can create/send legacy EAGLE
output. It must not be reused as a trading engine.

## Proposed remote changes (require explicit approval)

1. Confirm `paultsam267-cell/ai-trading-assistant-v1` and inspect the active
   workflow/runs without modifying them.
2. Confirm a persistent self-hosted Windows runner on the canonical machine,
   carrying the label `nexus-paper-host`, and set repository variable
   `NEXUS_CANONICAL_STATE_DIR` to the canonical `paper_state` directory.
3. Confirm secrets `NEXUS_TELEGRAM_BOT_TOKEN` and `NEXUS_TELEGRAM_CHAT_ID`
   using Telegram `getMe`/`getChat`; never display their values.
4. Disable the old scheduled `scanner.yml` workflow so the EAGLE sender stops.
5. Add the reviewed template `nexus-paper-reporting.yml.disabled` as
   `nexus-paper-reporting.yml`. It is read-only: no entries, exits, monitors,
   resets, or production JSON writes.
6. Trigger one manual report, verify the resulting workflow log and Telegram
   delivery, then enable the daily schedule.

If the persistent self-hosted runner cannot be verified, stop after step 4 and
keep GitHub reporting disabled. Local monitors remain the only PAPER engine.
