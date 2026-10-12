from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app as dashboard_app
from capital_management import (
    ATHENS_TZ,
    apply_profit_reserve,
    new_simulation_account,
    normalize_account,
    position_size_usd,
    release_reserve,
    total_equity,
    validate_initial_capital,
)

FIXED_NOW = datetime(2026, 7, 2, 16, 0, 0, tzinfo=ATHENS_TZ)


class CapitalManagementTests(unittest.TestCase):
    def test_old_account_gets_backward_compatible_defaults(self):
        account = normalize_account({"cash": 1140.25, "realized_pnl": 140.25})
        self.assertEqual(account["initial_capital"], 1000.0)
        self.assertEqual(account["reserve_cash"], 0.0)
        self.assertEqual(account["reserve_rate_pct"], 25.0)
        self.assertEqual(position_size_usd(account), 25.0)

    def test_position_size_scales_with_initial_capital(self):
        self.assertEqual(position_size_usd(new_simulation_account(100, at=FIXED_NOW)), 2.5)
        self.assertEqual(position_size_usd(new_simulation_account(1000, at=FIXED_NOW)), 25.0)

    def test_reserve_takes_25_percent_of_new_realized_profit_peak(self):
        account = normalize_account({"cash": 1140.25, "realized_pnl": 140.25})
        result = apply_profit_reserve(account, at=FIXED_NOW)
        self.assertAlmostEqual(result["transferred"], 35.0625)
        self.assertAlmostEqual(account["cash"], 1105.1875)
        self.assertAlmostEqual(account["reserve_cash"], 35.0625)

    def test_same_profit_is_not_reserved_twice(self):
        account = normalize_account({"cash": 1100.0, "realized_pnl": 100.0})
        apply_profit_reserve(account, at=FIXED_NOW)
        second = apply_profit_reserve(account, at=FIXED_NOW)
        self.assertEqual(second["transferred"], 0.0)
        self.assertAlmostEqual(account["reserve_cash"], 25.0)

    def test_losses_do_not_pull_money_back_from_reserve(self):
        account = normalize_account({"cash": 1100.0, "realized_pnl": 100.0})
        apply_profit_reserve(account, at=FIXED_NOW)
        account["realized_pnl"] = 60.0
        result = apply_profit_reserve(account, at=FIXED_NOW)
        self.assertEqual(result["transferred"], 0.0)
        self.assertAlmostEqual(account["reserve_cash"], 25.0)

    def test_only_incremental_new_peak_is_reserved(self):
        account = normalize_account({"cash": 1100.0, "realized_pnl": 100.0})
        apply_profit_reserve(account, at=FIXED_NOW)
        account["realized_pnl"] = 140.0
        account["cash"] += 40.0
        result = apply_profit_reserve(account, at=FIXED_NOW)
        self.assertAlmostEqual(result["transferred"], 10.0)
        self.assertAlmostEqual(account["reserve_cash"], 35.0)

    def test_manual_reserve_use_is_not_immediately_recaptured(self):
        account = normalize_account({"cash": 1100.0, "realized_pnl": 100.0})
        apply_profit_reserve(account, at=FIXED_NOW)
        released = release_reserve(account, 10.0, at=FIXED_NOW)
        self.assertEqual(released, 10.0)
        self.assertAlmostEqual(account["reserve_cash"], 15.0)
        result = apply_profit_reserve(account, at=FIXED_NOW)
        self.assertEqual(result["transferred"], 0.0)
        self.assertAlmostEqual(account["reserve_cash"], 15.0)

    def test_total_equity_includes_reserve(self):
        account = normalize_account({"cash": 900.0, "reserve_cash": 25.0})
        self.assertEqual(total_equity(1000.0, account), 1025.0)

    def test_new_simulation_account_is_clean(self):
        account = new_simulation_account(100.0, at=FIXED_NOW)
        self.assertEqual(account["cash"], 100.0)
        self.assertEqual(account["reserve_cash"], 0.0)
        self.assertEqual(account["day_start_equity"], 100.0)
        self.assertEqual(account["position_size_pct"], 2.5)

    def test_initial_capital_validation(self):
        self.assertEqual(validate_initial_capital("1000"), 1000.0)
        with self.assertRaises(ValueError):
            validate_initial_capital("0")


class CapitalDashboardTests(unittest.TestCase):
    def test_dashboard_has_six_simple_capital_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            archive = Path(tmp) / "archive"
            state.mkdir()
            archive.mkdir()
            (state / "paper_account.json").write_text(
                json.dumps(new_simulation_account(1000, at=FIXED_NOW)), encoding="utf-8"
            )
            (state / "paper_positions.json").write_text("{}", encoding="utf-8")
            (state / "paper_trade_log.json").write_text("[]", encoding="utf-8")
            with patch.object(dashboard_app, "STATE_DIR", state), patch.object(
                dashboard_app, "POSITIONS_FILE", state / "paper_positions.json"
            ), patch.object(dashboard_app, "TRADES_FILE", state / "paper_trade_log.json"), patch.object(
                dashboard_app, "ACCOUNT_FILE", state / "paper_account.json"
            ), patch.object(
                dashboard_app, "SIMULATION_HISTORY_FILE", state / "simulation_history.json"
            ), patch.object(dashboard_app, "ARCHIVE_DIR", archive):
                response = dashboard_app.app.test_client().get("/")
                text = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                for label in (
                    "Αρχικό Κεφάλαιο",
                    "Ενεργό Πορτοφόλι",
                    "Αποθεματικό Κερδών",
                    "Συνολικό Κεφάλαιο",
                    "Σήμερα",
                    "Από την Αρχή",
                ):
                    self.assertIn(label, text)
                self.assertIn("Νέα Προσομοίωση", text)

    def test_new_simulation_route_archives_previous_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            archive = Path(tmp) / "archive"
            state.mkdir()
            archive.mkdir()
            account_file = state / "paper_account.json"
            positions_file = state / "paper_positions.json"
            trades_file = state / "paper_trade_log.json"
            history_file = state / "simulation_history.json"
            account_file.write_text(
                json.dumps({"cash": 1100.0, "realized_pnl": 100.0}), encoding="utf-8"
            )
            positions_file.write_text("{}", encoding="utf-8")
            trades_file.write_text("[]", encoding="utf-8")

            # Hard isolation guard: even if a future refactor introduces a new
            # global path, this test must fail before it can read or write the
            # production paper_state directory.
            allowed_files = {
                path.resolve()
                for path in (account_file, positions_file, trades_file, history_file)
            }
            original_load = dashboard_app.load_json_file
            original_save = dashboard_app.save_json_file

            def isolated_load(path, default):
                resolved = Path(path).resolve()
                if resolved not in allowed_files:
                    raise AssertionError(f"test attempted production-state read: {resolved}")
                return original_load(path, default)

            def isolated_save(path, data):
                resolved = Path(path).resolve()
                if resolved not in allowed_files:
                    raise AssertionError(f"test attempted production-state write: {resolved}")
                return original_save(path, data)

            with patch.object(dashboard_app, "STATE_DIR", state), patch.object(
                dashboard_app, "POSITIONS_FILE", positions_file
            ), patch.object(dashboard_app, "TRADES_FILE", trades_file), patch.object(
                dashboard_app, "ACCOUNT_FILE", account_file
            ), patch.object(dashboard_app, "SIMULATION_HISTORY_FILE", history_file), patch.object(
                dashboard_app, "ARCHIVE_DIR", archive
            ), patch.object(
                dashboard_app, "load_json_file", side_effect=isolated_load
            ), patch.object(
                dashboard_app, "save_json_file", side_effect=isolated_save
            ):
                client = dashboard_app.app.test_client()
                response = client.post(
                    "/new-simulation",
                    data={"initial_capital": "100"},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 302)
                new_account = json.loads(account_file.read_text(encoding="utf-8"))
                history = json.loads(history_file.read_text(encoding="utf-8"))
                self.assertEqual(new_account["initial_capital"], 100.0)
                self.assertEqual(new_account["cash"], 100.0)
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["initial_capital"], 1000.0)


if __name__ == "__main__":
    unittest.main()
