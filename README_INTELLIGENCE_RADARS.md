# AI Trading Assistant V1 • Intelligence Radars Update

Η έκδοση παραμένει αποκλειστικά **PAPER TRADING ONLY**. Δεν συνδέεται με πραγματικό wallet και δεν στέλνει orders.

## Τι προστέθηκε

### 1. Pionex Positioning & Squeeze Radar
Διαβάζει μόνο δημόσια δεδομένα futures του Pionex:
- πρόσφατα taker trades,
- order-book depth,
- 24ωρο ticker,
- mark/index και next funding,
- open interest.

Παράγει καταστάσεις:
- `LONG_FLOW`
- `SHORT_FLOW`
- `LONG_CROWDING`
- `SHORT_CROWDING`
- `LONG_SQUEEZE_RISK`
- `SHORT_SQUEEZE_RISK`
- `NEUTRAL`

### 2. Pionex Early Bot Radar
Το δημόσιο leaderboard **δεν διαβάζεται με μη τεκμηριωμένο scraping**. Το module δέχεται δομημένα snapshots στο:

`pionex_leaderboard_snapshots.json`

Από διαδοχικά snapshots υπολογίζει:
- πιθανή αλλαγή ημερήσιου κύκλου,
- χρόνο ζωής bot,
- άνοδο/πτώση rank,
- ταχύτητα profit,
- ποινή ακραίου leverage,
- early score.

### 3. Whale Flow Radar
Δέχεται:
- χειροκίνητα events στο `whale_events.json`,
- προαιρετικά δημόσιες Solana διευθύνσεις μέσω Helius,
- προαιρετικά δημόσιες BSC διευθύνσεις μέσω BSC/Etherscan V2 API.

Αρχεία:
- `whale_watchlist.json`
- `whale_events.json`

Μεταβλητές περιβάλλοντος, μόνο εφόσον χρησιμοποιηθούν providers:
- `HELIUS_API_KEY`
- `BSCSCAN_API_KEY`

**Ποτέ seed phrase, private key ή wallet password.**

### 4. Δυναμική κατανομή κεφαλαίου
Η θέση υπολογίζεται μόνο από το ενεργό κεφάλαιο. Το αποθεματικό 25% παραμένει έξω.

Προεπιλογές:
- μικτή/πρώιμη ένδειξη: 1%
- κανονική θέση: 2,5%
- ισχυρή συμφωνία: 4%
- εξαιρετικά ισχυρή συμφωνία με whale confirmation: 5%
- squeeze/conflict: 0%
- μέγιστη συνολική έκθεση: 20% του αρχικού κεφαλαίου

Στο `intelligence_config.json` μπορεί να αλλάξει το mode από:
- `DYNAMIC_PERCENT`
σε:
- `FIXED_USD`

### 5. Πύλη παρατήρησης
Το dashboard καταγράφει:
- ημέρες παρατήρησης / 30,
- κλεισμένες θέσεις / 50,
- readiness για επόμενο shadow review.

Δεν ενεργοποιεί πραγματικό trading όταν συμπληρωθούν τα όρια. Απλώς εμφανίζει ότι υπάρχει αρκετό δείγμα για ανθρώπινη αξιολόγηση.

## Εγκατάσταση

1. Κλείσε dashboard και PowerShell.
2. Αντέγραψε όλα τα περιεχόμενα του ZIP στον μοναδικό κύριο φάκελο.
3. Πάτησε «Αντικατάσταση αρχείων».
4. Μην διαγράψεις `paper_state` και `archive`.
5. Τρέξε:

```powershell
.\RUN_TESTS.bat
```

Αναμενόμενο:

```text
Ran 43 tests
OK
```

Μετά:

```powershell
.\RUN_DEMO_APP.bat
```

Η πλήρης ροή είναι:

`Scanner → Intelligence Radars → Dynamic Paper Allocation → Paper Trader → Dashboard`

## Χρήση από το dashboard

Υπάρχει νέο κουμπί:

`📡 Ανανέωση Radars`

και μία συμπαγής ενότητα που δείχνει SOLANA/BSC κατεύθυνση, squeeze state, confidence, whale score και ενδεικτικό μέγεθος θέσης.

## Ασφάλεια

- Καμία πραγματική συναλλαγή.
- Καμία χρήση API key του Pionex για private/trading endpoints.
- Καμία αυτόματη χρήση του αποθεματικού.
- Αν αποτύχει το Pionex API, το bot επιστρέφει στη βασική θέση 2,5% και δεν σταματά το paper wallet.
- Whale signal μόνο του δεν δίνει μέγιστη θέση. Χρειάζεται συμφωνία αγοράς και κατεύθυνσης.
