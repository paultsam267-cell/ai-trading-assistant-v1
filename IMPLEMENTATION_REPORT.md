# AI Trading Assistant V1 — Risk & Exit Engine Dev

## Τι προστέθηκε

- Κεντρικό `risk_config.py` με παραμετροποιήσιμα Stop Loss, τρία Take Profit, trailing stop, μέγιστη διάρκεια, stale-price όριο και μέγιστες θέσεις.
- `paper_risk.py` με backward-compatible μετατροπή παλιών wallet records, partial/full demo sells, realized/unrealized PnL, daily snapshot Europe/Athens και προστασία από STALE/UNAVAILABLE τιμές.
- Ο `paper_trader.py` ενημερώνει κάθε ανοιχτή θέση απευθείας από DexScreener, ακόμη κι όταν το token δεν υπάρχει πια στο τελευταίο scanner report.
- Dashboard με κατάσταση τιμής, τελευταία ενημέρωση, υψηλό, απόσταση από υψηλό, υπόλοιπο ποσότητας, κατοχυρωμένο PnL, TP/trailing status, επόμενη ενέργεια και demo controls 25%/50%/75%/100%.
- Αυτόματο daily PnL snapshot με ζώνη ώρας `Europe/Athens`.
- 10 offline tests και deterministic simulation.

## Προεπιλεγμένοι κανόνες

- Stop Loss: `-35%`
- TP1: `+50%`, πώληση `25%` της αρχικής ποσότητας
- TP2: `+100%`, πώληση `25%`
- TP3: `+200%`, πώληση `25%`
- Trailing activation: `+100%`
- Trailing distance: `20%`
- Max holding time: `168 ώρες`
- Stale price threshold: `5 λεπτά`
- Max open positions: `10`

## Έλεγχοι

```bat
RUN_TESTS.bat
```

ή:

```bat
python -m py_compile app.py paper_trader.py paper_risk.py risk_config.py
python -m unittest discover -s tests -v
python simulate_risk_engine.py
```

## Εκκίνηση

Μόνο dashboard, χωρίς να εφαρμοστούν αμέσως οι κανόνες στις παλιές θέσεις:

```bat
RUN_DASHBOARD_ONLY.bat
```

Scanner + Risk Engine + Dashboard:

```bat
RUN_DEMO_APP.bat
```

## Σημαντικό για το πρώτο run

Το `RUN_DEMO_APP.bat` εκτελεί πρώτα scanner και paper trader. Εφόσον βρεθεί FRESH τιμή, οι υπάρχουσες παλιές demo θέσεις μπορούν να κλείσουν ή να κατοχυρώσουν κέρδη σύμφωνα με τους νέους κανόνες. Η έκδοση αυτή βρίσκεται σε ξεχωριστό φάκελο development ώστε να μη γράψει πάνω στο αρχικό project.

Το σύστημα παραμένει **PAPER TRADING ONLY**. Δεν δημιουργεί πραγματικά orders και δεν αγγίζει wallets, private keys ή exchange credentials.
