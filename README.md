# NEXUS AI Trading Assistant

Τοπικό demo scanner και paper-trading dashboard για Solana/BSC με:

- LONG paper trading
- Synthetic SHORT 1x simulation
- Risk & Exit Engine
- Scanner sync
- μεταβλητό αρχικό κεφάλαιο
- δυναμικό μέγεθος θέσης 2,5% του αρχικού κεφαλαίου
- αποθεματικό 25% των νέων peak καθαρών πραγματοποιημένων κερδών

## Γρήγορη εκκίνηση

1. `pip install -r requirements.txt`
2. `RUN_TESTS.bat`
3. `RUN_DEMO_APP.bat`

Στο dashboard, άνοιξε την ενότητα **«Νέα προσομοίωση και αποθεματικό»** για να ξεκινήσεις νέο demo με διαφορετικό ποσό ή να χρησιμοποιήσεις χειροκίνητα μέρος του reserve.

Δες το `IMPLEMENTATION_CAPITAL_RESERVE_REPORT.md` για την ακριβή λογική.

**PAPER TRADING ONLY. Δεν εκτελεί πραγματικές συναλλαγές.**
