from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

import market_scanner as ms
import paper_opportunity_monitor as pom
import paper_risk_monitor as risk_monitor
import paper_trader as pt
from paper_risk import calculate_equity, evaluate_position, normalize_position, set_fresh_price
from risk_config import RiskSettings


NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


def chart_payload(prices, *, volumes=None, currency="USD", exchange="TEST"):
    start = int((NOW - timedelta(hours=len(prices) - 1)).timestamp())
    if volumes is None:
        volumes = [1000 + index * 10 for index in range(len(prices))]
    return {
        "meta": {"currency": currency, "exchangeName": exchange},
        "timestamp": [start + index * 3600 for index in range(len(prices))],
        "indicators": {"quote": [{"close": list(prices), "volume": list(volumes)}]},
    }


def wrapped_payload(result):
    return {"chart": {"result": [result], "error": None}}


def rising_prices(count=40, start=100.0, step=0.45):
    return [start + index * step for index in range(count)]


def market_candidate(symbol="AAPL", asset_class="STOCK", signal="LONG", score=80.0):
    return {
        "asset_class": asset_class,
        "instrument_id": ms.stable_instrument_id(symbol),
        "symbol": symbol,
        "name": symbol,
        "signal": signal,
        "score": score,
        "price_usd": 100.0,
        "change_h24": 2.0 if signal == "LONG" else -2.0,
        "source": "Yahoo Chart",
        "currency": "USD",
        "exchange": "TEST",
        "market_data_timestamp": NOW.isoformat(),
        "paper_tradable": asset_class != "INDEX",
        "analysis_only": asset_class == "INDEX",
        "anti_chasing_ok": True,
    }


def allocation(*args, **kwargs):
    return {
        "allocation_usd": 2.5,
        "allocation_pct": 2.5,
        "level": "TEST",
        "regime_direction": "NEUTRAL",
        "regime_confidence": 0.0,
        "blocked": False,
    }


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class MarketScannerTests(unittest.TestCase):
    def test_stock_candidate_long(self):
        instrument = {"asset_class": "STOCK", "symbol": "AAPL", "name": "Apple", "paper_tradable": True}
        candidate = ms.candidate_from_chart(instrument, chart_payload(rising_prices()), now=NOW)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["signal"], "LONG")
        self.assertGreaterEqual(candidate["score"], 65)

    def test_stock_candidate_neutral(self):
        instrument = {"asset_class": "STOCK", "symbol": "MSFT", "name": "Microsoft", "paper_tradable": True}
        candidate = ms.candidate_from_chart(instrument, chart_payload([100.0] * 40), now=NOW)
        self.assertEqual(candidate["signal"], "NEUTRAL")
        self.assertEqual(candidate["score"], 50.0)

    def test_metal_candidate(self):
        instrument = {"asset_class": "METAL", "symbol": "GC=F", "name": "Gold", "paper_tradable": True}
        candidate = ms.candidate_from_chart(instrument, chart_payload(rising_prices(start=2000, step=1.5)), now=NOW)
        self.assertEqual(candidate["asset_class"], "METAL")
        self.assertEqual(candidate["instrument_id"], "YAHOO:GC=F")

    def test_fx_candidate(self):
        prices = [1.20 - index * 0.001 for index in range(40)]
        instrument = {"asset_class": "FX", "symbol": "EURUSD=X", "name": "EUR/USD", "paper_tradable": True}
        candidate = ms.candidate_from_chart(instrument, chart_payload(prices, currency="USD"), now=NOW)
        self.assertEqual(candidate["asset_class"], "FX")
        self.assertEqual(candidate["signal"], "SHORT_WATCH")

    def test_malformed_yahoo_response_fail_closed(self):
        session = Mock()
        session.get.return_value = FakeResponse({"chart": {"result": None}})
        self.assertIsNone(ms.fetch_yahoo_chart("AAPL", session=session))

    def test_http_failure_fail_closed(self):
        session = Mock()
        session.get.return_value = FakeResponse(error=requests.HTTPError("503"))
        self.assertIsNone(ms.fetch_yahoo_chart("AAPL", session=session))

    def test_stable_instrument_id(self):
        self.assertEqual(ms.stable_instrument_id("gc=f"), "YAHOO:GC=F")
        self.assertEqual(ms.stable_instrument_id(" AAPL "), "YAHOO:AAPL")


class MultiAssetEntryTests(unittest.TestCase):
    def test_no_collision_crypto_non_crypto_keys(self):
        crypto = {"chain": "stock", "dex": "yahoo", "symbol": "AAPL"}
        stock = market_candidate()
        self.assertNotEqual(pt.position_key(crypto), pt.position_key(stock))
        self.assertEqual(pt.position_key(stock), "market:stock:yahoo:aapl")

    @patch.object(pt.ms, "is_market_timestamp_fresh", return_value=True)
    @patch.object(pt, "allocation_decision", side_effect=allocation)
    def test_market_gate_does_not_require_crypto_fields(self, mock_allocation, mock_fresh):
        item = market_candidate()
        allowed, _ = pt.should_open_position(item, {"initial_capital": 100, "cash": 100}, {})
        self.assertTrue(allowed)
        self.assertNotIn("liquidity_usd", item)
        self.assertNotIn("txns", item)

    @patch.object(pt.ms, "is_market_timestamp_fresh", return_value=True)
    @patch.object(pt, "allocation_decision", side_effect=allocation)
    def test_duplicate_non_crypto_position_blocked(self, mock_allocation, mock_fresh):
        item = market_candidate()
        positions = {pt.position_key(item): {**item, "side": "LONG"}}
        allowed, reason = pt.should_open_position(item, {"initial_capital": 100, "cash": 100}, positions)
        self.assertFalse(allowed)
        self.assertIn("ήδη", reason)

    @patch.object(pt.ms, "is_market_timestamp_fresh", return_value=True)
    @patch.object(pt, "allocation_decision", side_effect=allocation)
    def test_common_cash_allocation(self, mock_allocation, mock_fresh):
        account = {"initial_capital": 100.0, "cash": 100.0}
        positions = {}
        trades = []
        pt.open_demo_position(account, positions, trades, market_candidate("AAPL"), NOW)
        pt.open_demo_position(account, positions, trades, market_candidate("MSFT"), NOW)
        self.assertAlmostEqual(account["cash"], 95.0)
        self.assertEqual(len(positions), 2)

    @patch.object(pt.ms, "is_market_timestamp_fresh", return_value=True)
    @patch.object(pt, "allocation_decision", side_effect=allocation)
    def test_global_max_positions_across_asset_classes(self, mock_allocation, mock_fresh):
        positions = {"solana:pumpswap:token": {"asset_class": "CRYPTO", "side": "LONG"}}
        with patch.object(pt, "RISK_SETTINGS", RiskSettings(max_open_positions=1)):
            allowed, reason = pt.should_open_position(
                market_candidate(), {"initial_capital": 100, "cash": 100}, positions
            )
        self.assertFalse(allowed)
        self.assertIn("global", reason)

    @patch.object(pt.ms, "is_market_timestamp_fresh", return_value=True)
    @patch.object(pt, "allocation_decision", side_effect=allocation)
    def test_indices_are_analysis_only(self, mock_allocation, mock_fresh):
        allowed, reason = pt.should_open_position(
            market_candidate("^GSPC", "INDEX"), {"initial_capital": 100, "cash": 100}, {}
        )
        self.assertFalse(allowed)
        self.assertIn("analysis-only", reason)


class MultiAssetRiskTests(unittest.TestCase):
    def make_stock_position(self):
        item = market_candidate()
        position = {
            **item,
            "key": pt.position_key(item),
            "side": "LONG",
            "entry_price": 100.0,
            "quantity": 0.025,
            "remaining_quantity": 0.025,
            "allocated_usd": 2.5,
            "remaining_cost_basis": 2.5,
            "current_price": 100.0,
            "price_status": "FRESH",
            "entry_time": "10/10/2026 13:00:00",
        }
        normalize_position(position, position["key"])
        return position

    @patch.object(pt.ms, "fetch_current_quote")
    def test_non_crypto_fresh_price_refresh(self, mock_quote):
        position = self.make_stock_position()
        mock_quote.return_value = {
            "price_usd": 105.0,
            "market_data_timestamp": NOW.isoformat(),
            "source": "Yahoo Chart",
            "currency": "USD",
            "exchange": "NMS",
        }
        positions = {position["key"]: position}
        pt.refresh_open_position_prices(positions, {}, NOW)
        self.assertEqual(position["price_status"], "FRESH")
        self.assertEqual(position["current_price"], 105.0)
        self.assertEqual(position["market_source"], "Yahoo Chart")

    @patch.object(pt.ms, "fetch_current_quote", return_value=None)
    def test_non_crypto_stale_refresh_cannot_trigger_exit(self, mock_quote):
        position = self.make_stock_position()
        set_fresh_price(position, 50.0, NOW)
        positions = {position["key"]: position}
        pt.refresh_open_position_prices(positions, {}, NOW)
        actions = evaluate_position(
            {"cash": 97.5, "realized_pnl": 0, "wins": 0, "losses": 0},
            positions,
            [],
            position["key"],
            at=NOW,
        )
        self.assertEqual(position["price_status"], "STALE")
        self.assertEqual(actions, [])

    def test_backward_compatibility_old_crypto_position(self):
        old = {
            "symbol": "OLD",
            "entry_price": 1.0,
            "quantity": 2.5,
            "allocated_usd": 2.5,
            "chain": "solana",
            "dex": "pumpswap",
        }
        normalize_position(old, "solana:pumpswap:old")
        self.assertEqual(old["asset_class"], "CRYPTO")
        self.assertEqual(old["key"], "solana:pumpswap:old")

    def test_mixed_portfolio_equity_calculation(self):
        crypto = self.make_stock_position()
        crypto["asset_class"] = "CRYPTO"
        crypto["key"] = "solana:pumpswap:test"
        stock = self.make_stock_position()
        stock["key"] = "stock:yahoo:aapl"
        set_fresh_price(crypto, 120.0, NOW)
        set_fresh_price(stock, 80.0, NOW)
        equity = calculate_equity({"cash": 95.0}, {crypto["key"]: crypto, stock["key"]: stock})
        self.assertAlmostEqual(equity, 100.0)

    @patch.object(risk_monitor.pt, "save_json_file")
    @patch.object(risk_monitor.pt, "load_json_file")
    def test_existing_equity_history_still_works(self, mock_load, mock_save):
        mock_load.return_value = [{"timestamp": (NOW - timedelta(hours=1)).isoformat(), "total_equity": 99.0}]
        written = risk_monitor.record_equity_snapshot(
            {"initial_capital": 100.0, "cash": 100.0, "reserve_cash": 0.0}, {}, NOW
        )
        self.assertTrue(written)
        self.assertEqual(len(mock_save.call_args.args[1]), 2)


class OpportunityMonitorTests(unittest.TestCase):
    @patch.object(pom.market_scanner, "scan_market", return_value=[])
    @patch.object(pom, "scan_crypto_candidates", return_value=[])
    def test_opportunity_monitor_cannot_execute_real_trade(self, mock_crypto, mock_market):
        result = pom.run_opportunity_cycle(allow_entries=False, run_at=NOW)
        self.assertTrue(pom.PAPER_ONLY)
        self.assertEqual(result["real_trades_executed"], 0)
        self.assertEqual(result["mode"], "READ_ONLY_NO_ENTRY")

    @patch.object(pom.pt, "evaluate_position")
    @patch.object(pom.market_scanner, "scan_market", return_value=[])
    @patch.object(pom, "scan_crypto_candidates", return_value=[])
    def test_opportunity_monitor_does_not_execute_exits(self, mock_crypto, mock_market, mock_evaluate):
        result = pom.run_opportunity_cycle(allow_entries=False, run_at=NOW)
        self.assertEqual(result["exits_executed"], 0)
        mock_evaluate.assert_not_called()

    @patch.object(pom.pt, "send_console_message")
    @patch.object(pom.pt, "load_latest_intelligence", return_value={})
    @patch.object(pom.pt.ms, "is_market_timestamp_fresh", return_value=True)
    @patch.object(pom.pt, "allocation_decision", side_effect=allocation)
    @patch.object(pom, "scan_crypto_candidates", return_value=[])
    def test_no_duplicate_entry_across_cycles(
        self,
        mock_crypto,
        mock_allocation,
        mock_fresh,
        mock_intelligence,
        mock_send,
    ):
        account = {"initial_capital": 100.0, "cash": 100.0}
        positions = {}
        trades = []

        def load_json(path, default):
            if path == pom.pt.POSITIONS_FILE:
                return positions
            if path == pom.pt.TRADES_FILE:
                return trades
            return default

        def save_json(path, data):
            if path == pom.pt.POSITIONS_FILE:
                positions.clear()
                positions.update(data)
            elif path == pom.pt.TRADES_FILE:
                trades.clear()
                trades.extend(data)

        with patch.object(pom.market_scanner, "scan_market", return_value=[market_candidate()]), patch.object(
            pom.pt, "load_account", return_value=account
        ), patch.object(pom.pt, "load_json_file", side_effect=load_json), patch.object(
            pom.pt, "save_json_file", side_effect=save_json
        ):
            first = pom.run_opportunity_cycle(allow_entries=True, run_at=NOW)
            second = pom.run_opportunity_cycle(allow_entries=True, run_at=NOW)

        self.assertEqual(first["paper_entries"], 1)
        self.assertEqual(second["paper_entries"], 0)
        self.assertEqual(len(positions), 1)


class CrossAssetNormalizationTests(unittest.TestCase):
    def test_crypto_raw_score_above_100_is_bounded(self):
        normalized = pom.normalize_crypto_score(192.78)
        self.assertGreaterEqual(normalized, 0.0)
        self.assertLessEqual(normalized, 100.0)

    def test_crypto_raw_zero_has_valid_normalized_value(self):
        self.assertEqual(pom.normalize_crypto_score(0), 0.0)

    def test_crypto_normalization_is_monotonic(self):
        values = [pom.normalize_crypto_score(raw) for raw in (0, 20, 55, 100, 200)]
        self.assertEqual(values, sorted(values))

    def test_market_normalized_score_is_bounded(self):
        high = pom.with_normalized_scores(market_candidate(score=150.0))
        low = pom.with_normalized_scores(market_candidate(score=-10.0))
        self.assertEqual(high["normalized_score"], 100.0)
        self.assertEqual(low["normalized_score"], 0.0)

    def test_common_ranking_uses_normalized_not_raw_score(self):
        crypto = {
            "asset_class": "CRYPTO",
            "instrument_id": "CRYPTO:solana:high-raw",
            "symbol": "RAW",
            "score": 200.0,
        }
        stock = market_candidate(score=90.0)
        ranked = pom.rank_candidates([crypto, stock])
        self.assertEqual(ranked[0]["asset_class"], "STOCK")
        self.assertGreater(crypto["score"], stock["score"])
        self.assertGreater(ranked[0]["normalized_score"], ranked[1]["normalized_score"])

    @patch.object(pom.crypto_scanner, "score_pair", return_value=150.0)
    @patch.object(pom.crypto_scanner, "classify_candidate", return_value="LONG")
    @patch.object(pom.crypto_scanner, "prioritize_report_mix", side_effect=lambda pairs: pairs)
    @patch.object(pom.crypto_scanner, "is_candidate", return_value=True)
    @patch.object(pom.crypto_scanner, "select_best_pairs", side_effect=lambda pairs: pairs)
    @patch.object(pom.crypto_scanner, "fetch_pairs_for_chain")
    @patch.object(pom.crypto_scanner, "build_token_watchlist", return_value={"solana": ["token"]})
    def test_crypto_candidate_serialization_includes_both_scores(
        self,
        mock_watchlist,
        mock_fetch,
        mock_select,
        mock_candidate,
        mock_prioritize,
        mock_classify,
        mock_score,
    ):
        mock_fetch.return_value = [
            {
                "chainId": "solana",
                "dexId": "pumpswap",
                "pairAddress": "pair",
                "baseToken": {"address": "token", "symbol": "TOK", "name": "Token"},
                "priceUsd": "1.0",
            }
        ]
        candidate = pom.scan_crypto_candidates()[0]
        self.assertEqual(candidate["raw_score"], 150.0)
        self.assertEqual(candidate["score"], 150.0)
        self.assertIn("normalized_score", candidate)
        self.assertLessEqual(candidate["normalized_score"], 100.0)

    @patch.object(pt, "allocation_decision", side_effect=allocation)
    def test_crypto_raw_entry_gate_behavior_is_unchanged(self, mock_allocation):
        raw_score = max(pt.MIN_ENTRY_SCORE, 60.0)
        item = {
            "asset_class": "CRYPTO",
            "chain": "solana",
            "dex": "pumpswap",
            "symbol": "GATE",
            "signal": "LONG",
            "score": raw_score,
            "raw_score": raw_score,
            "normalized_score": pom.normalize_crypto_score(raw_score),
            "price_usd": 1.0,
            "liquidity_usd": 100000.0,
            "volume_h24": 500000.0,
            "change_h24": 20.0,
            "txns": {"h1": {"buys": 10, "sells": 5}},
        }
        allowed, _ = pt.should_open_position(item, {"initial_capital": 1000, "cash": 1000}, {})
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
