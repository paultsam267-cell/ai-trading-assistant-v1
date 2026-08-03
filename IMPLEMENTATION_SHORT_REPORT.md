# Synthetic SHORT Paper Trading Pass

## Scope

Προστέθηκε side-aware demo engine για `LONG` και `SHORT`, με τα SHORT να είναι αυστηρά **synthetic 1x simulation**. Δεν υπάρχει πραγματικό borrow, leverage, funding ή σύνδεση εκτέλεσης orders.

## Position model

Κάθε θέση έχει πλέον `side: LONG|SHORT`. Τα παλιά records χωρίς `side` φορτώνονται ως LONG.

Για synthetic SHORT:

- το position size κρατείται ως collateral από το demo cash,
- η ποσότητα είναι `collateral / entry_price`,
- το unrealized PnL είναι `quantity * (entry_price - current_price)`,
- το equity της θέσης είναι `remaining_collateral + unrealized_pnl`, με κατώτατο όριο μηδέν,
- στο κλείσιμο επιστρέφεται collateral συν PnL στο demo cash.

## Short entry filters

- signal `SHORT` ή `SHORT_WATCH`,
- score >= 40,
- liquidity >= $50K,
- volume 24h >= $200K,
- πτώση 24h από -10% έως -60%,
- H1 sells/buys ratio >= 1.25,
- μέχρι 2 ανοιχτά synthetic SHORT,
- αποφυγή chasing token που έχει ήδη καταρρεύσει πάνω από 60%.

## Short exits

- Stop Loss: 20%,
- TP1: 15%, κλείσιμο 25%,
- TP2: 30%, κλείσιμο 25%,
- TP3: 50%, κλείσιμο 25%,
- trailing activation: 20%, distance: 10%,
- max hold: 48 ώρες,
- synthetic liquidation guard: adverse move 75%.

## Scanner

Το scanner αποθηκεύει πλέον τα `txns` δεδομένα στο archive και κρατά έως 2 `SHORT_WATCH` υποψηφίους μέσα στο report των 8 θέσεων.

## Dashboard

Προστέθηκαν:

- LONG/SHORT counter,
- στήλη πλευράς θέσης,
- position equity για SHORT αντί spot market value,
- καλύτερη τιμή ανά πλευρά, high για LONG και low για SHORT,
- ξεχωριστοί κανόνες synthetic SHORT,
- SHORT_OPEN / SHORT_CLOSE trade history,
- διόρθωση εμφάνισης `-$0.00`.

## Validation

- Python compile: OK
- Existing regression tests: OK
- New synthetic short tests: OK
- Total: 22 tests OK
- Deterministic end-to-end SHORT open smoke test: OK
