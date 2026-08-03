from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from archive_utils import find_latest_archive as find_latest_scanner_archive, resolve_project_path
from capital_management import (
    apply_profit_reserve,
    new_simulation_account,
    normalize_account,
    position_size_usd,
    release_reserve,
    simulation_summary,
    total_equity,
    validate_initial_capital,
)
from paper_risk import (
    best_price_metrics,
    calculate_equity,
    daily_pnl as calculate_daily_pnl,
    effective_price_status,
    ensure_daily_snapshot,
    execute_sale,
    next_exit_action,
    normalize_position,
    normalize_positions,
    position_equity_value,
    position_pnl_pct,
    position_side,
    parse_local,
    safe_float as risk_safe_float,
    set_fresh_price,
)
from risk_config import RISK_SETTINGS
from intelligence_radar import load_latest_intelligence
from pionex_bot_watchlist import add_manual_snapshot, analyze_snapshots, load_summary as load_pionex_bot_summary, summary_for_display
from flask import Flask, render_template_string, redirect, request, url_for

APP_TITLE = "NEXUS AI Trading Assistant • LONG + Synthetic SHORT"
ATHENS_TZ = ZoneInfo("Europe/Athens")

ARCHIVE_DIR = resolve_project_path("ARCHIVE_DIR", "archive")
STATE_DIR = resolve_project_path("PAPER_STATE_DIR", "paper_state")

POSITIONS_FILE = STATE_DIR / "paper_positions.json"
TRADES_FILE = STATE_DIR / "paper_trade_log.json"
ACCOUNT_FILE = STATE_DIR / "paper_account.json"
SIMULATION_HISTORY_FILE = STATE_DIR / "simulation_history.json"

DEX_API_BASE = "https://api.dexscreener.com"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
AUTO_REFRESH_SECONDS = int(os.getenv("AUTO_REFRESH_SECONDS", "15"))
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "1000"))

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ai-trading-assistant-local-dashboard/1.0"})

app = Flask(__name__)
app.secret_key = "ai_trading_assistant_local"

LAST_RUN_MESSAGE = ""


HTML_TEMPLATE = """
<!doctype html>
<html lang="el">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{{ auto_refresh_seconds }}">
  <title>{{ title }}</title>

  <style>
    body {
      font-family: Arial, sans-serif;
      background: #0f172a;
      color: #e5e7eb;
      margin: 0;
      padding: 24px;
    }

    .container {
      max-width: 1550px;
      margin: 0 auto;
    }

    h1, h2 {
      margin-top: 0;
    }

    .subtitle {
      color: #94a3b8;
      margin-bottom: 24px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }

    .card {
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    }

    .metric-label {
      color: #93c5fd;
      font-size: 14px;
      margin-bottom: 8px;
    }

    .metric-value {
      font-size: 28px;
      font-weight: bold;
    }

    .metric-sub {
      margin-top: 6px;
      font-size: 13px;
      color: #94a3b8;
    }

    .positive { color: #22c55e; }
    .negative { color: #ef4444; }
    .neutral { color: #f8fafc; }

    .section-grid {
      display: grid;
      grid-template-columns: 1.55fr 1fr;
      gap: 16px;
      margin-bottom: 24px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }

    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid #1f2937;
      vertical-align: top;
    }

    th {
      color: #93c5fd;
      font-weight: 600;
    }

    .mono {
      font-family: Consolas, monospace;
      word-break: break-word;
    }

    .badge {
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: #374151;
      color: #e5e7eb;
    }

    .badge.long { background: #14532d; color: #bbf7d0; }
    .badge.short { background: #7f1d1d; color: #fecaca; }
    .badge.neutral { background: #374151; color: #e5e7eb; }

    .small {
      font-size: 13px;
      color: #94a3b8;
    }

    .prebox {
      white-space: pre-wrap;
      background: #0b1220;
      border: 1px solid #1f2937;
      border-radius: 12px;
      padding: 14px;
      font-size: 13px;
      line-height: 1.45;
      max-height: 520px;
      overflow: auto;
    }

    .muted {
      color: #94a3b8;
      font-size: 12px;
    }

    .btn {
      color: white;
      border: none;
      padding: 12px 18px;
      border-radius: 10px;
      font-weight: bold;
      cursor: pointer;
      margin-right: 10px;
      margin-bottom: 10px;
    }

    .btn-green { background: #16a34a; }
    .btn-red { background: #dc2626; }
    .btn-blue { background: #2563eb; }
    .btn-mini {
      padding: 6px 8px;
      font-size: 11px;
      margin: 2px;
      border-radius: 7px;
    }
    .table-wrap { overflow-x: auto; }
    .status-fresh { background: #14532d; color: #bbf7d0; }
    .status-stale { background: #78350f; color: #fde68a; }
    .status-unavailable { background: #7f1d1d; color: #fecaca; }
    .control-row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
    .control-form { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:0; }
    .field {
      background:#0b1220; color:#e5e7eb; border:1px solid #334155;
      border-radius:9px; padding:10px 12px; min-width:150px;
    }
    details.settings { margin-top:14px; border-top:1px solid #1f2937; padding-top:12px; }
    details.settings summary { cursor:pointer; color:#93c5fd; font-weight:700; }

    @media (max-width: 1200px) {
      .section-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>

<body>
<div class="container">

  <h1>📊 {{ title }}</h1>

  <div class="subtitle">
    Demo εικονικού πορτοφολιού • Τελευταία ενημέρωση: {{ now_str }} • Αυτόματη ανανέωση: {{ auto_refresh_seconds }}s
  </div>

  <div class="card" style="margin-bottom: 24px;">
    <h2>🎮 Έλεγχος Demo</h2>
    <div class="control-row">
      <form method="post" action="/run-paper-trader" class="control-form">
        <button type="submit" class="btn btn-green">▶ Εκτέλεση Paper Trader</button>
      </form>
      <form method="post" action="/run-intelligence" class="control-form">
        <button type="submit" class="btn btn-blue">📡 Ανανέωση Radars</button>
      </form>
    </div>

    <details class="settings">
      <summary>⚙️ Νέα προσομοίωση και αποθεματικό</summary>
      <div style="margin-top:12px;" class="control-row">
        <form method="post" action="/new-simulation" class="control-form"
              onsubmit="return confirm('Η τρέχουσα προσομοίωση θα αρχειοθετηθεί και οι ανοιχτές demo θέσεις θα μηδενιστούν. Συνέχεια;');">
          <input class="field" type="number" name="initial_capital" min="1" step="0.01" value="{{ initial_capital_raw }}" aria-label="Αρχικό εικονικό κεφάλαιο">
          <button type="submit" class="btn btn-red">🆕 Νέα Προσομοίωση</button>
        </form>

        <form method="post" action="/release-reserve" class="control-form"
              onsubmit="return confirm('Να μεταφερθεί το ποσό από το αποθεματικό στο ενεργό demo cash;');">
          <input class="field" type="number" name="amount" min="0.01" step="0.01" placeholder="Ποσό από αποθεματικό" {% if reserve_raw <= 0 %}disabled{% endif %}>
          <button type="submit" class="btn btn-blue" {% if reserve_raw <= 0 %}disabled{% endif %}>🛟 Χρήση Αποθεματικού</button>
        </form>
      </div>
      <div class="small">Η νέα θέση ορίζεται αυτόματα στο {{ position_size_pct }}% του αρχικού κεφαλαίου. Το {{ reserve_rate_pct }}% του νέου peak καθαρού πραγματοποιημένου κέρδους μεταφέρεται έξω από το ενεργό trading cash.</div>
    </details>

    {% if last_run_message %}
      <div class="prebox" style="margin-top:14px;">{{ last_run_message }}</div>
    {% endif %}
  </div>

  <div class="grid">
    <div class="card">
      <div class="metric-label">🏁 Αρχικό Κεφάλαιο</div>
      <div class="metric-value neutral">{{ initial_capital }}</div>
      <div class="metric-sub">Βάση της τρέχουσας προσομοίωσης</div>
    </div>

    <div class="card">
      <div class="metric-label">⚙️ Ενεργό Πορτοφόλι</div>
      <div class="metric-value neutral">{{ active_equity }}</div>
      <div class="metric-sub">Cash + ανοιχτές θέσεις, χωρίς αποθεματικό</div>
    </div>

    <div class="card">
      <div class="metric-label">🏦 Αποθεματικό Κερδών</div>
      <div class="metric-value positive">{{ reserve_cash }}</div>
      <div class="metric-sub">Εκτός κανονικών νέων θέσεων</div>
    </div>

    <div class="card">
      <div class="metric-label">💼 Συνολικό Κεφάλαιο</div>
      <div class="metric-value {{ total_pnl_class }}">{{ equity }}</div>
      <div class="metric-sub">Ενεργό πορτοφόλι + αποθεματικό</div>
    </div>

    <div class="card">
      <div class="metric-label">📅 Σήμερα</div>
      <div class="metric-value {{ daily_pnl_class }}">{{ daily_pnl }}</div>
      <div class="metric-sub {{ daily_pnl_class }}">{{ daily_pnl_pct }}</div>
    </div>

    <div class="card">
      <div class="metric-label">📊 Από την Αρχή</div>
      <div class="metric-value {{ total_pnl_class }}">{{ total_from_start }}</div>
      <div class="metric-sub {{ total_pnl_class }}">{{ total_from_start_pct }}</div>
    </div>
  </div>

  <div class="card" style="margin-bottom:24px;">
    <h2>📡 Positioning, Squeeze & Whale Radars</h2>
    <div class="small" style="margin-bottom:12px;">
      Pionex δημόσια δεδομένα: <strong>{{ intelligence_status }}</strong> •
      Whale events: <strong>{{ whale_event_count }}</strong> •
      Early Bot Radar: <strong>{{ leaderboard_status }}</strong>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Αγορά</th><th>Κατεύθυνση</th><th>Κατάσταση</th><th>Βεβαιότητα</th><th>Whales</th><th>Δυναμική θέση</th></tr></thead>
        <tbody>
        {% for row in radar_rows %}
          <tr>
            <td><strong>{{ row.chain }}</strong><br><span class="small">{{ row.symbol }}</span></td>
            <td><span class="badge {{ row.side_class }}">{{ row.direction }}</span></td>
            <td>{{ row.state }}</td>
            <td>{{ row.confidence }}%</td>
            <td>{{ row.whale_score }}</td>
            <td>{{ row.allocation_hint }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    <div class="small" style="margin-top:12px;">
      Παρατήρηση: <strong>{{ observed_days }}/{{ minimum_days }} ημέρες</strong> •
      Κλεισίματα: <strong>{{ evaluated_closed }}/{{ minimum_closed }}</strong> •
      Έτοιμο για shadow review: <strong>{{ shadow_ready }}</strong>.
      Το αποθεματικό δεν συμμετέχει στη δυναμική κατανομή.
    </div>
  </div>

  <div class="card" style="margin-bottom:24px;">
    <h2>🤖 Pionex Bot Watchlist</h2>
    <div class="small" style="margin-bottom:12px;">
      Manual Pionex snapshots only. No API key. No real orders. PAPER TRADING ONLY.
    </div>
    <div class="grid" style="margin-bottom:12px;">
      <div><strong>Bots watched:</strong> {{ pionex_bots.total_bots_watched }}</div>
      <div><strong>LONG bots:</strong> {{ pionex_bots.long_count }}</div>
      <div><strong>SHORT bots:</strong> {{ pionex_bots.short_count }}</div>
      <div><strong>Bias:</strong> {{ pionex_bots.leaderboard_bias }}</div>
      <div><strong>Crowding:</strong> {{ pionex_bots.crowding_state }}</div>
      <div><strong>Freshness:</strong> {{ pionex_bots.freshness_state }}</div>
      <div><strong>Action effect:</strong> {{ pionex_bots.action_effect }}</div>
      <div><strong>Snapshots:</strong> {{ pionex_bots.snapshot_count }}</div>
    </div>
    <div class="small">
      <strong>Top LONG pairs:</strong> {{ pionex_bots.top_long_pairs_text }}<br>
      <strong>Top SHORT pairs:</strong> {{ pionex_bots.top_short_pairs_text }}<br>
      <strong>Repeated pairs:</strong> {{ pionex_bots.repeated_pairs_text }}<br>
      <strong>Τελευταίο snapshot:</strong> {{ pionex_bots.last_snapshot_time or "-" }}
    </div>
    <div style="margin-top:12px;">
      <a class="btn btn-blue" style="text-decoration:none; display:inline-block;" href="/pionex-bots">➕ Άνοιγμα Pionex Watchlist</a>
    </div>
  </div>

  {% if valuation_notice %}
  <div class="card" style="margin-bottom:24px; border-color:#78350f;">
    <div class="small">⚠️ {{ valuation_notice }}</div>
  </div>
  {% endif %}

  <div class="card" style="margin-bottom:24px;">
    <h2>📌 Ανοιχτές Θέσεις</h2>

    {% if positions %}
      <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Σύμβολο</th>
            <th>Πλευρά</th>
            <th>Δίκτυο</th>
            <th>Τιμή Εισόδου</th>
            <th>Τρέχουσα Τιμή</th>
            <th>Κατάσταση Τιμής</th>
            <th>Τελευταία Τιμή</th>
            <th>Υπόλοιπο</th>
            <th>Κεφάλαιο / Margin</th>
            <th>Τρέχουσα Αξία</th>
            <th>Μη Πραγμ. PnL</th>
            <th>Κατοχυρωμένο PnL</th>
            <th>Καλύτερη Τιμή / Απόσταση</th>
            <th>Take Profit</th>
            <th>Trailing</th>
            <th>Επόμενη Ενέργεια</th>
            <th>Σήμα / Βαθμός</th>
            <th>Χειροκίνητο Κλείσιμο</th>
          </tr>
        </thead>
        <tbody>
          {% for p in positions %}
          <tr>
            <td><strong>{{ p.symbol }}</strong><br><span class="small">{{ p.name }}</span></td>
            <td><span class="badge {{ p.side_class }}">{{ p.side }}</span></td>
            <td>{{ p.chain }}<br><span class="small">{{ p.dex }}</span></td>
            <td>{{ p.entry_price_fmt }}</td>
            <td>{{ p.current_price_fmt }}</td>
            <td><span class="badge {{ p.price_status_class }}">{{ p.price_status_gr }}</span></td>
            <td>{{ p.last_price_update_at or "-" }}</td>
            <td class="mono">{{ p.remaining_quantity_fmt }}</td>
            <td>{{ p.remaining_cost_basis_fmt }}</td>
            <td>{{ p.market_value_fmt }}</td>
            <td class="{{ p.unrealized_class }}">
              {{ p.unrealized_fmt }}<br><span class="small {{ p.unrealized_class }}">{{ p.unrealized_pct_fmt }}</span>
            </td>
            <td class="{{ p.realized_class }}">{{ p.position_realized_fmt }}</td>
            <td>{{ p.best_price_fmt }}<br><span class="small">{{ p.distance_from_best_fmt }}</span></td>
            <td>{{ p.take_profit_status }}</td>
            <td>{{ p.trailing_status }}</td>
            <td>{{ p.next_exit_action }}</td>
            <td><span class="badge {{ p.signal_class }}">{{ p.signal_gr }}</span><br>{{ p.current_score }}</td>
            <td>
              {% for pct in (25, 50, 75, 100) %}
              <form method="post" action="{{ url_for('manual_close_position', position_key=p.key) }}" style="display:inline;" onsubmit="return confirm('Να κλείσει το {{ pct }}% της θέσης {{ p.symbol }} στο demo wallet;');">
                <input type="hidden" name="percent" value="{{ pct }}">
                <button type="submit" class="btn btn-blue btn-mini">{{ pct }}%</button>
              </form>
              {% endfor %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      </div>
      <div class="muted" style="margin-top:12px;">
        Αυτόματο κλείσιμο LONG ή synthetic SHORT εκτελείται μόνο με τιμή FRESH. Σε STALE ή UNAVAILABLE κατάσταση η θέση παραμένει ανέγγιχτη.
      </div>
    {% else %}
      <div class="small">Δεν υπάρχουν ανοιχτές θέσεις αυτή τη στιγμή.</div>
    {% endif %}
  </div>

  <div class="section-grid">
    <div class="card">
      <h2>🧾 Αναλυτικά Στοιχεία</h2>
      <p><strong>Αρχικό κεφάλαιο:</strong> {{ initial_capital }}</p>
      <p><strong>Διαθέσιμο trading cash:</strong> {{ cash }}</p>
      <p><strong>Αξία ανοιχτών θέσεων:</strong> {{ open_positions_value }}</p>
      <p><strong>Ενεργό πορτοφόλι:</strong> {{ active_equity }}</p>
      <p><strong>Αποθεματικό:</strong> <span class="positive">{{ reserve_cash }}</span></p>
      <p><strong>Συνολικό κεφάλαιο:</strong> <span class="{{ total_pnl_class }}">{{ equity }}</span></p>
      <p><strong>Από την αρχή:</strong> <span class="{{ total_pnl_class }}">{{ total_from_start }} / {{ total_from_start_pct }}</span></p>
      <p><strong>Σήμερα:</strong> <span class="{{ daily_pnl_class }}">{{ daily_pnl }} / {{ daily_pnl_pct }}</span></p>
      <p><strong>Ημερήσια βάση:</strong> {{ day_start_equity }} <span class="small">({{ account.day_start_recorded_at or "θα δημιουργηθεί στο επόμενο run" }})</span></p>
      <p><strong>Πραγματοποιημένο PnL:</strong> <span class="{{ realized_pnl_class }}">{{ realized_pnl }}</span></p>
      <p><strong>Μη πραγματοποιημένο PnL:</strong> <span class="{{ unrealized_pnl_class }}">{{ unrealized_pnl }}</span></p>
      <p><strong>Μέγεθος νέας θέσης:</strong> {{ position_size }}</p>
      <p><strong>Ανοιχτές LONG / SHORT:</strong> {{ long_positions_count }} / {{ short_positions_count }}</p>
      <p><strong>Κερδισμένα / χαμένα:</strong> {{ wins }} / {{ losses }}</p>
      <p><strong>Τελευταία ενημέρωση λογαριασμού:</strong> {{ account.last_update or "-" }}</p>
      <p><strong>Τελευταίο αρχείο scanner:</strong> {{ latest_archive_name }}</p>
      <p><strong>Υποψήφια / report:</strong> {{ archive_candidates_count }} / {{ archive_reported_count }}</p>
    </div>

    <div class="card">
      <h2>🛡️ Κανόνες Διαχείρισης Ρίσκου</h2>
      <p><strong>Stop Loss:</strong> -{{ risk.stop_loss_pct }}%</p>
      <p><strong>Take Profit 1:</strong> +{{ risk.take_profit_1_pct }}%, πώληση {{ risk.take_profit_1_sell_pct }}%</p>
      <p><strong>Take Profit 2:</strong> +{{ risk.take_profit_2_pct }}%, πώληση {{ risk.take_profit_2_sell_pct }}%</p>
      <p><strong>Take Profit 3:</strong> +{{ risk.take_profit_3_pct }}%, πώληση {{ risk.take_profit_3_sell_pct }}%</p>
      <p><strong>Trailing Stop:</strong> ενεργοποίηση +{{ risk.trailing_stop_activation_pct }}%, απόσταση {{ risk.trailing_stop_distance_pct }}%</p>
      <p><strong>Μέγιστη διάρκεια θέσης:</strong> {{ risk.max_position_age_hours }} ώρες</p>
      <p><strong>Όριο φρέσκιας τιμής:</strong> {{ risk.stale_price_minutes }} λεπτά</p>
      <p><strong>Μέγιστες ανοιχτές θέσεις:</strong> {{ risk.max_open_positions }}</p>
      <p><strong>Αποθεματικό κερδών:</strong> {{ reserve_rate_pct }}% του νέου peak καθαρού realized PnL</p>
      <p><strong>Μέγεθος θέσης:</strong> {{ position_size_pct }}% του αρχικού κεφαλαίου</p>
      <hr style="border-color:#1f2937;">
      <p><strong>Synthetic SHORT Stop Loss:</strong> -{{ risk.short_stop_loss_pct }}%</p>
      <p><strong>SHORT Take Profit 1:</strong> +{{ risk.short_take_profit_1_pct }}%, κλείσιμο {{ risk.short_take_profit_1_sell_pct }}%</p>
      <p><strong>SHORT Take Profit 2:</strong> +{{ risk.short_take_profit_2_pct }}%, κλείσιμο {{ risk.short_take_profit_2_sell_pct }}%</p>
      <p><strong>SHORT Take Profit 3:</strong> +{{ risk.short_take_profit_3_pct }}%, κλείσιμο {{ risk.short_take_profit_3_sell_pct }}%</p>
      <p><strong>SHORT Trailing:</strong> ενεργοποίηση +{{ risk.short_trailing_stop_activation_pct }}%, απόσταση {{ risk.short_trailing_stop_distance_pct }}%</p>
      <p><strong>SHORT μέγιστη διάρκεια:</strong> {{ risk.short_max_position_age_hours }} ώρες</p>
      <p><strong>SHORT synthetic liquidation:</strong> adverse move +{{ risk.short_liquidation_pct }}%</p>
      <p><strong>Μέγιστα synthetic SHORT:</strong> {{ risk.max_short_positions }}</p>
      <div class="small">Τα SHORT είναι 1x συνθετική προσομοίωση. Δεν υπάρχει πραγματικός δανεισμός, leverage, funding ή exchange order.</div>
    </div>
  </div>

  <div class="section-grid">
    <div class="card">
      <h2>🕘 Τελευταίες Κινήσεις</h2>

      {% if trades %}
        <table>
          <thead>
            <tr>
              <th>Τύπος</th>
              <th>Σύμβολο</th>
              <th>Τιμή</th>
              <th>Κέρδος / Ζημία</th>
              <th>Αιτία</th>
              <th>Ώρα</th>
            </tr>
          </thead>

          <tbody>
            {% for t in trades %}
            <tr>
              <td>
                {% if t.side == "SHORT" %}
                  <span class="badge short">{{ t.type_gr }}</span>
                {% elif t.side == "CASH" %}
                  <span class="badge neutral">{{ t.type_gr }}</span>
                {% else %}
                  <span class="badge long">{{ t.type_gr }}</span>
                {% endif %}
              </td>

              <td><strong>{{ t.symbol }}</strong><br><span class="small">{{ t.chain }}</span></td>
              <td>{{ t.display_price_fmt }}</td>
              <td class="{{ t.pnl_class }}">{{ t.pnl_fmt }}</td>
              <td>{{ t.reason_gr }}</td>
              <td>{{ t.time }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      {% else %}
        <div class="small">Δεν υπάρχουν ακόμα κινήσεις.</div>
      {% endif %}
    </div>

    <div class="card">
      <h2>📡 Τελευταίο Scanner Report</h2>

      {% if latest_report_text %}
        <div class="prebox">{{ latest_report_text }}</div>
      {% else %}
        <div class="small">Δεν βρέθηκε archive report.</div>
      {% endif %}
    </div>
  </div>

  <div class="card">
    <h2>⚠️ Σημείωση</h2>
    <div class="small">
      Το εργαλείο είναι demo paper trading. Τα SHORT είναι synthetic 1x simulation. Δεν εκτελεί πραγματικές αγορές, πωλήσεις, δανεισμό ή exchange orders και δεν αποτελεί οικονομική συμβουλή.
    </div>
  </div>

</div>
</body>
</html>
"""


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ)


def now_local_str() -> str:
    return now_local().strftime("%d/%m/%Y %H:%M:%S")


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: Path, data: Any) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def format_money(value: float) -> str:
    if abs(value) < 0.005:
        value = 0.0
    sign = "-" if value < 0 else ""
    value = abs(value)

    if value >= 1_000_000_000:
        return f"{sign}${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.1f}K"

    return f"{sign}${value:.2f}"


def format_price(value: float) -> str:
    value = safe_float(value)

    if value >= 1:
        return f"${value:.4f}"
    if value >= 0.01:
        return f"${value:.6f}"
    if value >= 0.0001:
        return f"${value:.8f}"
    if value >= 0.00000001:
        return f"${value:.12f}"

    return f"${value:.16f}"


def format_pct(value: float) -> str:
    if abs(value) < 0.0005:
        value = 0.0
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def pnl_class(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "neutral"


def gr_signal(value: Any) -> str:
    value = str(value or "").upper().strip()

    mapping = {
        "LONG": "ΑΓΟΡΑΣΤΙΚΟ",
        "SHORT": "ΠΤΩΤΙΚΟ",
        "SHORT_WATCH": "ΠΤΩΤΙΚΗ ΠΑΡΑΚΟΛΟΥΘΗΣΗ",
        "NEUTRAL": "ΟΥΔΕΤΕΡΟ",
        "N/A": "ΧΩΡΙΣ ΣΗΜΑ",
    }

    return mapping.get(value, value or "ΧΩΡΙΣ ΣΗΜΑ")


def signal_class(value: Any) -> str:
    value = str(value or "").upper().strip()

    if value == "LONG":
        return "long"
    if value in ("SHORT", "SHORT_WATCH"):
        return "short"

    return "neutral"


def gr_trade_type(value: Any) -> str:
    value = str(value or "").upper().strip()

    mapping = {
        "BUY": "LONG ΑΝΟΙΓΜΑ",
        "SELL": "LONG ΚΛΕΙΣΙΜΟ",
        "SHORT_OPEN": "SHORT ΑΝΟΙΓΜΑ",
        "SHORT_CLOSE": "SHORT ΚΛΕΙΣΙΜΟ",
        "RESERVE_RELEASE": "ΧΡΗΣΗ ΑΠΟΘΕΜΑΤΙΚΟΥ",
    }

    return mapping.get(value, value or "-")


def gr_reason(value: Any) -> str:
    value = str(value or "").upper().strip()

    mapping = {
        "STOP_LOSS": "Ενεργοποίηση Stop Loss",
        "TAKE_PROFIT": "Επίτευξη Take Profit",
        "TAKE_PROFIT_1": "Μερική κατοχύρωση Take Profit 1",
        "TAKE_PROFIT_2": "Μερική κατοχύρωση Take Profit 2",
        "TAKE_PROFIT_3": "Μερική κατοχύρωση Take Profit 3",
        "TRAILING_STOP": "Κλείσιμο με Trailing Stop",
        "MAX_HOLD_TIME": "Κλείσιμο λόγω μέγιστης διάρκειας",
        "MANUAL_CLOSE": "Χειροκίνητο κλείσιμο demo θέσης",
        "SIGNAL_EXIT": "Έξοδος λόγω σήματος",
        "SIGNAL_FLIP": "Αλλαγή σήματος",
        "MANUAL": "Χειροκίνητη ενέργεια",
        "AUTO_BUY": "Αυτόματη αγορά από scanner",
        "AUTO_SELL": "Αυτόματη πώληση από κανόνα",
        "FILTERED_ENTRY": "Είσοδος μετά από φίλτρα",
        "MAX_POSITIONS": "Όριο μέγιστων θέσεων",
        "LOW_CASH": "Χαμηλό διαθέσιμο κεφάλαιο",
        "SYNTHETIC_SHORT_ENTRY": "Άνοιγμα synthetic SHORT 1x",
        "SHORT_STOP_LOSS": "Κλείσιμο synthetic SHORT με Stop Loss",
        "SHORT_TAKE_PROFIT_1": "Μερικό κλείσιμο SHORT Take Profit 1",
        "SHORT_TAKE_PROFIT_2": "Μερικό κλείσιμο SHORT Take Profit 2",
        "SHORT_TAKE_PROFIT_3": "Μερικό κλείσιμο SHORT Take Profit 3",
        "SHORT_TRAILING_STOP": "Κλείσιμο synthetic SHORT με Trailing Stop",
        "SHORT_MAX_HOLD_TIME": "Κλείσιμο SHORT λόγω μέγιστης διάρκειας",
        "SHORT_LIQUIDATION": "Synthetic liquidation προστασίας",
        "EMERGENCY_RESERVE_USE": "Χειροκίνητη χρήση αποθεματικού",
    }

    return mapping.get(value, value or "-")


def latest_archive_payload() -> Dict[str, Any]:
    path = find_latest_scanner_archive(ARCHIVE_DIR)
    if path is None:
        return {}

    data = load_json_file(path, {})
    if isinstance(data, dict):
        data["_filename"] = path.name
        return data
    return {}


def build_archive_price_map(archive: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    market_map: Dict[str, Dict[str, Any]] = {}
    items = archive.get("items", [])

    if not isinstance(items, list):
        return market_map

    for item in items:
        if not isinstance(item, dict):
            continue

        symbol = str(item.get("symbol", "")).upper().strip()
        chain = str(item.get("chain", "")).lower().strip()
        dex = str(item.get("dex", "")).lower().strip()

        if not symbol:
            continue

        key = f"{chain}:{dex}:{symbol}".lower()
        market_map[key] = item

    return market_map


def parse_pair_ref_from_url(url: str) -> Optional[Tuple[str, str]]:
    try:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]

        if len(parts) < 2:
            return None

        chain = parts[0].lower().strip()
        pair_id = parts[1].strip()

        if not chain or not pair_id:
            return None

        return chain, pair_id

    except Exception:
        return None


def fetch_live_pair(chain: str, pair_id: str) -> Dict[str, Any]:
    try:
        url = f"{DEX_API_BASE}/latest/dex/pairs/{chain}/{pair_id}"
        response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()

        pairs = payload.get("pairs", []) if isinstance(payload, dict) else []

        if isinstance(pairs, list) and pairs:
            pair = pairs[0]
            if isinstance(pair, dict):
                return pair

    except Exception:
        return {}

    return {}


def classify_live_pair(pair: Dict[str, Any]) -> str:
    if not pair:
        return "N/A"

    change_h24 = safe_float(pair.get("priceChange", {}).get("h24"))
    buys_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("buys"))
    sells_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("sells"))

    if change_h24 >= 0 and buys_h1 > sells_h1:
        return "LONG"

    if change_h24 < 0 and sells_h1 > buys_h1:
        return "SHORT_WATCH"

    return "NEUTRAL"


def score_live_pair(pair: Dict[str, Any]) -> float:
    if not pair:
        return 0.0

    liquidity = safe_float(pair.get("liquidity", {}).get("usd"))
    volume_h24 = safe_float(pair.get("volume", {}).get("h24"))
    change_h24 = safe_float(pair.get("priceChange", {}).get("h24"))
    buys_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("buys"))
    sells_h1 = safe_float(pair.get("txns", {}).get("h1", {}).get("sells"))
    boosts = safe_float(pair.get("boosts", {}).get("active"))

    score = 0.0
    score += min(liquidity / 50_000, 3.0) * 18
    score += min(volume_h24 / max(liquidity, 1.0), 3.0) * 16

    if change_h24 > 0:
        score += min(change_h24, 80) * 0.35
    else:
        score += max(change_h24, -30) * 0.20

    if buys_h1 > sells_h1:
        score += min(buys_h1 - sells_h1, 40) * 0.7
    else:
        score -= min(sells_h1 - buys_h1, 40) * 0.5

    score += min(boosts, 10) * 2.5

    return round(max(score, 0.0), 2)


def build_live_pair_map_from_positions(raw_positions: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    live_map: Dict[str, Dict[str, Any]] = {}

    if not isinstance(raw_positions, dict):
        return live_map

    for _, p in raw_positions.items():
        if not isinstance(p, dict):
            continue

        symbol = str(p.get("symbol", "")).upper().strip()
        chain = str(p.get("chain", "")).lower().strip()
        dex = str(p.get("dex", "")).lower().strip()
        url = str(p.get("url", "")).strip()

        if not symbol or not chain:
            continue

        key = str(p.get("key") or f"{chain}:{dex}:{symbol}")

        pair_ref = parse_pair_ref_from_url(url)

        if not pair_ref:
            live_map[key] = {}
            continue

        pair_chain, pair_id = pair_ref
        live_map[key] = fetch_live_pair(pair_chain, pair_id)

    return live_map


def is_today_gr(time_text: Any) -> bool:
    try:
        raw = str(time_text or "").strip()

        if not raw:
            return False

        date_part = raw.split(" ")[0]
        trade_date = datetime.strptime(date_part, "%d/%m/%Y").date()

        return trade_date == now_local().date()

    except Exception:
        return False


def daily_realized_from_trades(raw_trades: Any) -> float:
    if not isinstance(raw_trades, list):
        return 0.0

    total = 0.0

    for t in raw_trades:
        if not isinstance(t, dict):
            continue

        if str(t.get("type", "")).upper() == "SELL" and is_today_gr(t.get("time")):
            total += safe_float(t.get("pnl_usd"))

    return total


def price_status_gr(status: str) -> str:
    return {
        "FRESH": "ΦΡΕΣΚΙΑ",
        "STALE": "ΠΑΛΙΑ",
        "UNAVAILABLE": "ΜΗ ΔΙΑΘΕΣΙΜΗ",
    }.get(str(status or "").upper(), "ΜΗ ΔΙΑΘΕΣΙΜΗ")


def price_status_class(status: str) -> str:
    return {
        "FRESH": "status-fresh",
        "STALE": "status-stale",
        "UNAVAILABLE": "status-unavailable",
    }.get(str(status or "").upper(), "status-unavailable")


def format_optional_price(value: Any) -> str:
    number = safe_float(value, 0.0)
    return format_price(number) if number > 0 else "Μη διαθέσιμη"


def format_optional_money(value: Any, available: bool = True) -> str:
    return format_money(safe_float(value)) if available else "Μη διαθέσιμη"


@app.route("/")
def dashboard():
    account = load_json_file(
        ACCOUNT_FILE,
        {
            "cash": INITIAL_CAPITAL,
            "realized_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "last_update": None,
        },
    )
    if not isinstance(account, dict):
        account = {}
    account = normalize_account(account, default_initial=INITIAL_CAPITAL)

    raw_positions = normalize_positions(load_json_file(POSITIONS_FILE, {}))
    raw_trades = load_json_file(TRADES_FILE, [])
    if not isinstance(raw_trades, list):
        raw_trades = []

    archive = latest_archive_payload()
    intelligence = load_latest_intelligence()
    pionex_bot_summary = summary_for_display(load_pionex_bot_summary())
    archive_map = build_archive_price_map(archive)
    live_pair_map = build_live_pair_map_from_positions(raw_positions)
    view_now = now_local()

    positions: List[Dict[str, Any]] = []
    valuation_positions: Dict[str, Dict[str, Any]] = {}
    unrealized_total = 0.0
    total_market_value = 0.0
    unavailable_count = 0

    for key, original in raw_positions.items():
        p = dict(original)
        normalize_position(p, key)

        entry_price = safe_float(p.get("entry_price"))
        remaining_quantity = safe_float(p.get("remaining_quantity"))
        remaining_cost_basis = safe_float(p.get("remaining_cost_basis"))
        live_pair = live_pair_map.get(key, {})
        archive_item = archive_map.get(key, {})

        live_price = safe_float(live_pair.get("priceUsd"))
        archive_price = safe_float(archive_item.get("price_usd"))
        if live_price > 0:
            set_fresh_price(p, live_price, view_now)
        elif safe_float(p.get("current_price")) <= 0 and archive_price > 0:
            # Archive fallback is display-only and never considered safe for automatic exit.
            p["current_price"] = archive_price
            p["price_status"] = "STALE"

        status = effective_price_status(p, view_now, RISK_SETTINGS)
        if not live_pair and status == "FRESH":
            status = "STALE"
        p["price_status"] = status

        current_price = safe_float(p.get("current_price"))
        price_available = current_price > 0
        side = position_side(p)
        market_value = position_equity_value(p, current_price) if price_available else remaining_cost_basis
        unrealized = risk_safe_float(p.get("unrealized_pnl")) if price_available else 0.0
        unrealized_pct = position_pnl_pct(p, current_price) if price_available else 0.0
        if not price_available:
            unavailable_count += 1

        best_price, distance_from_best = best_price_metrics(p, current_price)

        current_signal_raw = (
            classify_live_pair(live_pair)
            if live_pair
            else str(archive_item.get("signal", "N/A")).upper()
        )
        current_score = (
            score_live_pair(live_pair)
            if live_pair
            else safe_float(archive_item.get("score"), safe_float(p.get("entry_score")))
        )

        completed = p.get("take_profit_levels_completed", [])
        take_profit_status = ", ".join(completed) if completed else "Κανένα επίπεδο"
        trailing_status = "ΕΝΕΡΓΟ" if p.get("trailing_stop_active") else "ΑΝΕΝΕΡΓΟ"

        unrealized_total += unrealized
        total_market_value += market_value
        valuation_positions[key] = p

        positions.append(
            {
                **p,
                "entry_price_fmt": format_price(entry_price),
                "current_price_fmt": format_optional_price(current_price),
                "price_status_gr": price_status_gr(status),
                "price_status_class": price_status_class(status),
                "remaining_quantity_fmt": f"{remaining_quantity:.6f}",
                "remaining_cost_basis_fmt": format_money(remaining_cost_basis),
                "market_value_fmt": format_optional_money(market_value, price_available),
                "unrealized_fmt": format_optional_money(unrealized, price_available),
                "unrealized_pct_fmt": format_pct(unrealized_pct) if price_available else "-",
                "unrealized_class": pnl_class(unrealized) if price_available else "neutral",
                "position_realized_fmt": format_money(safe_float(p.get("realized_pnl"))),
                "realized_class": pnl_class(safe_float(p.get("realized_pnl"))),
                "side": side,
                "side_class": "short" if side == "SHORT" else "long",
                "best_price_fmt": format_optional_price(best_price),
                "distance_from_best_fmt": format_pct(distance_from_best) if price_available else "-",
                "take_profit_status": take_profit_status,
                "trailing_status": trailing_status,
                "next_exit_action": next_exit_action(p, RISK_SETTINGS),
                "signal_gr": gr_signal(current_signal_raw),
                "signal_class": signal_class(current_signal_raw),
                "current_score": round(current_score, 2),
            }
        )

    trades: List[Dict[str, Any]] = []
    for trade in raw_trades[-12:][::-1]:
        if not isinstance(trade, dict):
            continue
        pnl_usd = safe_float(trade.get("pnl_usd"))
        trade_side = str(trade.get("side") or ("SHORT" if str(trade.get("type", "")).upper().startswith("SHORT") else "LONG")).upper()
        trade_type = str(trade.get("type", "")).upper()
        if trade_type == "RESERVE_RELEASE":
            display_price_fmt = format_money(safe_float(trade.get("cash_change_usd")))
        elif trade_type in {"BUY", "SHORT_OPEN"}:
            display_price_fmt = format_price(safe_float(trade.get("price")))
        else:
            display_price_fmt = format_price(safe_float(trade.get("exit_price")))
        trades.append(
            {
                **trade,
                "side": trade_side,
                "type_gr": gr_trade_type(trade.get("type")),
                "reason_gr": gr_reason(trade.get("reason")),
                "price_fmt": format_price(safe_float(trade.get("price"))),
                "exit_price_fmt": format_price(safe_float(trade.get("exit_price"))),
                "display_price_fmt": display_price_fmt,
                "pnl_fmt": format_money(pnl_usd) if trade_type in {"SELL", "SHORT_CLOSE"} else "-",
                "pnl_class": pnl_class(pnl_usd),
            }
        )

    initial_capital_value = safe_float(account.get("initial_capital"), INITIAL_CAPITAL)
    cash_value = safe_float(account.get("cash"), initial_capital_value)
    reserve_value = safe_float(account.get("reserve_cash"))
    realized_value = safe_float(account.get("realized_pnl"))
    active_equity_value = calculate_equity(
        account,
        valuation_positions,
        use_cost_basis_when_unavailable=True,
    )
    equity_value = total_equity(active_equity_value, account)
    total_from_start_value = equity_value - initial_capital_value
    total_from_start_pct_value = (
        total_from_start_value / initial_capital_value * 100.0
        if initial_capital_value > 0
        else 0.0
    )

    today_iso = view_now.date().isoformat()
    if str(account.get("day_start_date") or "") == today_iso:
        daily_values = calculate_daily_pnl(account, equity_value)
    else:
        daily_values = {"pnl": 0.0, "pct": 0.0, "base": 0.0}

    long_positions_count = sum(1 for item in positions if item.get("side") == "LONG")
    short_positions_count = sum(1 for item in positions if item.get("side") == "SHORT")

    fusion_rows = ((intelligence.get("fusion") or {}).get("by_chain") or {}) if isinstance(intelligence, dict) else {}
    radar_rows = []
    for chain in ("solana", "bsc"):
        row = fusion_rows.get(chain, {}) if isinstance(fusion_rows, dict) else {}
        direction = str(row.get("direction", "NEUTRAL")).upper()
        confidence = safe_float(row.get("confidence"))
        whale_score = safe_float(row.get("whale_score"))
        blocked = bool(row.get("block_long")) and direction == "LONG" or bool(row.get("block_short")) and direction == "SHORT"
        if blocked:
            allocation_hint = "0% • squeeze/conflict"
        elif confidence >= 88 and abs(whale_score) > 0.15:
            allocation_hint = "έως 5%"
        elif confidence >= 75:
            allocation_hint = "έως 4%"
        elif confidence >= 55:
            allocation_hint = "2,5%"
        elif confidence >= 40:
            allocation_hint = "1%"
        else:
            allocation_hint = "βάση 2,5%"
        radar_rows.append({
            "chain": chain.upper(),
            "symbol": row.get("symbol", "-"),
            "direction": direction,
            "side_class": "long" if direction == "LONG" else "short" if direction == "SHORT" else "",
            "state": row.get("state", "NO DATA"),
            "confidence": f"{confidence:.1f}",
            "whale_score": f"{whale_score:+.2f}",
            "allocation_hint": allocation_hint,
        })
    evaluation = intelligence.get("evaluation", {}) if isinstance(intelligence, dict) else {}
    pionex_status = ((intelligence.get("pionex") or {}).get("status", "NOT RUN")) if isinstance(intelligence, dict) else "NOT RUN"
    leaderboard_status_value = ((intelligence.get("leaderboard") or {}).get("status", "NO_SNAPSHOTS")) if isinstance(intelligence, dict) else "NO_SNAPSHOTS"
    whale_events_count = ((intelligence.get("whales") or {}).get("event_count", 0)) if isinstance(intelligence, dict) else 0

    valuation_notice = ""
    if unavailable_count:
        valuation_notice = (
            f"{unavailable_count} θέση/θέσεις χωρίς διαθέσιμη τιμή αποτιμώνται προσωρινά "
            "στο υπόλοιπο κόστος μόνο για το συνολικό κεφάλαιο."
        )

    return render_template_string(
        HTML_TEMPLATE,
        title=APP_TITLE,
        now_str=now_local_str(),
        auto_refresh_seconds=AUTO_REFRESH_SECONDS,
        account=account,
        initial_capital=format_money(initial_capital_value),
        initial_capital_raw=f"{initial_capital_value:.2f}",
        cash=format_money(cash_value),
        active_equity=format_money(active_equity_value),
        reserve_cash=format_money(reserve_value),
        reserve_raw=reserve_value,
        reserve_rate_pct=f"{safe_float(account.get('reserve_rate_pct')):.0f}",
        position_size_pct=f"{safe_float(account.get('position_size_pct')):.2f}",
        position_size=format_money(position_size_usd(account)),
        realized_pnl=format_money(realized_value),
        realized_pnl_class=pnl_class(realized_value),
        unrealized_pnl=format_money(unrealized_total),
        unrealized_pnl_class=pnl_class(unrealized_total),
        open_positions_value=format_money(total_market_value),
        equity=format_money(equity_value),
        total_from_start=format_money(total_from_start_value),
        total_from_start_pct=format_pct(total_from_start_pct_value),
        total_pnl_class=pnl_class(total_from_start_value),
        daily_pnl=format_money(daily_values["pnl"]),
        daily_pnl_pct=format_pct(daily_values["pct"]),
        daily_pnl_class=pnl_class(daily_values["pnl"]),
        day_start_equity=format_money(daily_values["base"]) if daily_values["base"] > 0 else "-",
        open_positions_count=len(positions),
        long_positions_count=long_positions_count,
        short_positions_count=short_positions_count,
        wins=int(account.get("wins", 0)),
        losses=int(account.get("losses", 0)),
        positions=positions,
        trades=trades,
        risk=RISK_SETTINGS.as_dict(),
        valuation_notice=valuation_notice,
        intelligence_status=pionex_status,
        whale_event_count=whale_events_count,
        leaderboard_status=leaderboard_status_value,
        radar_rows=radar_rows,
        observed_days=int(evaluation.get("observed_days", 0) or 0),
        minimum_days=int(evaluation.get("minimum_days", 30) or 30),
        evaluated_closed=int(evaluation.get("closed_positions", 0) or 0),
        minimum_closed=int(evaluation.get("minimum_closed_positions", 50) or 50),
        shadow_ready="ΝΑΙ" if evaluation.get("ready_for_shadow_review") else "ΟΧΙ",
        pionex_bots=pionex_bot_summary,
        latest_archive_name=archive.get("_filename", "-"),
        archive_candidates_count=archive.get("candidates_count", "-"),
        archive_reported_count=archive.get("reported_count", "-"),
        latest_report_text=archive.get("report_text", ""),
        last_run_message=LAST_RUN_MESSAGE,
    )


@app.route("/positions/<path:position_key>/close", methods=["POST"])
def manual_close_position(position_key: str):
    global LAST_RUN_MESSAGE
    try:
        percent = int(request.form.get("percent", "0"))
        if percent not in {25, 50, 75, 100}:
            raise ValueError("Μη επιτρεπτό ποσοστό κλεισίματος.")

        account = load_json_file(ACCOUNT_FILE, {})
        positions = normalize_positions(load_json_file(POSITIONS_FILE, {}))
        trades = load_json_file(TRADES_FILE, [])
        if not isinstance(account, dict):
            account = {}
        account = normalize_account(account, default_initial=INITIAL_CAPITAL)
        if not isinstance(trades, list):
            trades = []

        position = positions.get(position_key)
        if not isinstance(position, dict):
            raise ValueError("Η θέση δεν βρέθηκε.")

        pair_ref = parse_pair_ref_from_url(str(position.get("url", "")))
        live_pair = fetch_live_pair(*pair_ref) if pair_ref else {}
        live_price = safe_float(live_pair.get("priceUsd"))
        if live_price <= 0:
            raise ValueError("Δεν υπάρχει έγκυρη φρέσκια τιμή. Η θέση δεν άλλαξε.")

        at = now_local()
        set_fresh_price(position, live_price, at)
        active_equity_before = calculate_equity(account, positions)
        ensure_daily_snapshot(account, total_equity(active_equity_before, account), at)
        result = execute_sale(
            account,
            positions,
            trades,
            position_key,
            live_price,
            "MANUAL_CLOSE",
            sell_fraction_remaining=percent / 100.0,
            at=at,
        )
        if not result:
            raise ValueError("Δεν ολοκληρώθηκε η demo πώληση.")

        reserve_result = apply_profit_reserve(account, at=at)
        account["last_update"] = now_local_str()
        save_json_file(ACCOUNT_FILE, account)
        save_json_file(POSITIONS_FILE, positions)
        save_json_file(TRADES_FILE, trades)
        reserve_note = ""
        if reserve_result["transferred"] > 0:
            reserve_note = f" Στο αποθεματικό μεταφέρθηκαν {format_money(reserve_result['transferred'])}."
        LAST_RUN_MESSAGE = (
            f"✅ Demo κλείσιμο {percent}% για {result.get('symbol', '')}. "
            f"PnL: {format_money(safe_float(result.get('pnl_usd')))}.{reserve_note}"
        )
    except Exception as exc:
        LAST_RUN_MESSAGE = f"❌ Η θέση δεν άλλαξε: {exc}"
    return redirect(url_for("dashboard"))


@app.route("/run-intelligence", methods=["POST"])
def run_intelligence_radars():
    global LAST_RUN_MESSAGE
    try:
        result = subprocess.run(
            [sys.executable, "run_intelligence.py"],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        output = result.stdout.strip()
        error = result.stderr.strip()
        if result.returncode == 0:
            LAST_RUN_MESSAGE = "✅ Τα Radars ανανεώθηκαν.\n\n" + (output or "Χωρίς μήνυμα.")
        else:
            LAST_RUN_MESSAGE = "⚠️ Τα Radars δεν ανανεώθηκαν. Το paper wallet δεν άλλαξε.\n\n" + (error or output or "Άγνωστο σφάλμα.")
    except subprocess.TimeoutExpired:
        LAST_RUN_MESSAGE = "⏳ Η ανανέωση Radars σταμάτησε λόγω timeout. Το paper wallet δεν άλλαξε."
    return redirect(url_for("dashboard"))


@app.route("/run-paper-trader", methods=["POST"])
def run_paper_trader():
    global LAST_RUN_MESSAGE

    try:
        result = subprocess.run(
            [sys.executable, "paper_trader.py"],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )

        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode == 0:
            LAST_RUN_MESSAGE = (
                "✅ Ο Paper Trader εκτελέστηκε επιτυχώς.\n\n"
                f"{output or 'Δεν επέστρεψε μήνυμα.'}"
            )
        else:
            LAST_RUN_MESSAGE = (
                "❌ Ο Paper Trader έβγαλε σφάλμα.\n\n"
                f"{error or output or 'Άγνωστο σφάλμα.'}"
            )

    except subprocess.TimeoutExpired:
        LAST_RUN_MESSAGE = "⏳ Ο Paper Trader άργησε πολύ και σταμάτησε λόγω timeout."

    except Exception as e:
        LAST_RUN_MESSAGE = f"❌ Σφάλμα εκτέλεσης Paper Trader:\n{e}"

    return redirect(url_for("dashboard"))


@app.route("/new-simulation", methods=["POST"])
def new_simulation():
    global LAST_RUN_MESSAGE
    try:
        initial_capital = validate_initial_capital(request.form.get("initial_capital"))
        at = now_local()

        old_account = normalize_account(
            load_json_file(ACCOUNT_FILE, {}),
            default_initial=INITIAL_CAPITAL,
        )
        old_positions = normalize_positions(load_json_file(POSITIONS_FILE, {}))
        old_trades = load_json_file(TRADES_FILE, [])
        if not isinstance(old_trades, list):
            old_trades = []

        history = load_json_file(SIMULATION_HISTORY_FILE, [])
        if not isinstance(history, list):
            history = []
        active_equity = calculate_equity(
            old_account,
            old_positions,
            use_cost_basis_when_unavailable=True,
        )
        history.append(
            simulation_summary(
                old_account,
                active_equity=active_equity,
                open_positions=len(old_positions),
                trade_count=len(old_trades),
                at=at,
            )
        )
        save_json_file(SIMULATION_HISTORY_FILE, history[-100:])
        save_json_file(ACCOUNT_FILE, new_simulation_account(initial_capital, at=at))
        save_json_file(POSITIONS_FILE, {})
        save_json_file(TRADES_FILE, [])
        LAST_RUN_MESSAGE = (
            f"✅ Ξεκίνησε νέα προσομοίωση με αρχικό κεφάλαιο {format_money(initial_capital)}. "
            "Η προηγούμενη σύνοψη αρχειοθετήθηκε."
        )
    except Exception as exc:
        LAST_RUN_MESSAGE = f"❌ Δεν ξεκίνησε νέα προσομοίωση: {exc}"
    return redirect(url_for("dashboard"))


@app.route("/release-reserve", methods=["POST"])
def use_profit_reserve():
    global LAST_RUN_MESSAGE
    try:
        at = now_local()
        account = normalize_account(
            load_json_file(ACCOUNT_FILE, {}),
            default_initial=INITIAL_CAPITAL,
        )
        trades = load_json_file(TRADES_FILE, [])
        if not isinstance(trades, list):
            trades = []
        released = release_reserve(account, request.form.get("amount"), at=at)
        trades.append(
            {
                "type": "RESERVE_RELEASE",
                "side": "CASH",
                "time": now_local_str(),
                "symbol": "ΑΠΟΘΕΜΑΤΙΚΟ",
                "chain": "demo",
                "price": released,
                "pnl_usd": 0.0,
                "reason": "EMERGENCY_RESERVE_USE",
                "cash_change_usd": released,
            }
        )
        account["last_update"] = now_local_str()
        save_json_file(ACCOUNT_FILE, account)
        save_json_file(TRADES_FILE, trades)
        LAST_RUN_MESSAGE = (
            f"✅ Μεταφέρθηκαν {format_money(released)} από το αποθεματικό "
            "στο ενεργό demo cash."
        )
    except Exception as exc:
        LAST_RUN_MESSAGE = f"❌ Το αποθεματικό δεν άλλαξε: {exc}"
    return redirect(url_for("dashboard"))


PIONEX_BOTS_TEMPLATE = """
<!doctype html>
<html lang="el">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pionex Bot Watchlist</title>
  <style>
    body { font-family: Arial, sans-serif; background:#0f172a; color:#e5e7eb; margin:0; padding:24px; }
    .container { max-width:1100px; margin:0 auto; }
    .card { background:#111827; border:1px solid #1f2937; border-radius:16px; padding:18px; margin-bottom:18px; }
    textarea { width:100%; min-height:260px; background:#0b1220; color:#e5e7eb; border:1px solid #334155; border-radius:12px; padding:12px; font-family:Consolas, monospace; }
    .btn { color:white; border:none; padding:12px 18px; border-radius:10px; font-weight:bold; cursor:pointer; margin-right:10px; text-decoration:none; display:inline-block; }
    .btn-blue { background:#2563eb; } .btn-green { background:#16a34a; }
    .small { color:#94a3b8; font-size:13px; } .prebox { white-space:pre-wrap; background:#0b1220; border:1px solid #1f2937; border-radius:12px; padding:14px; overflow:auto; }
    code { color:#bfdbfe; }
  </style>
</head>
<body>
<div class="container">
  <h1>🤖 Pionex Bot Watchlist</h1>
  <div class="card">
    <div class="small">Manual Pionex snapshots only. No API key. No real orders. PAPER TRADING ONLY.</div>
    <p><a class="btn btn-blue" href="/">← Πίσω στο Dashboard</a></p>
  </div>

  {% if last_run_message %}<div class="card"><div class="prebox">{{ last_run_message }}</div></div>{% endif %}

  <div class="card">
    <h2>Τρέχουσα σύνοψη</h2>
    <p><strong>Status:</strong> {{ summary.status }}</p>
    <p><strong>Bots watched:</strong> {{ summary.total_bots_watched }} | <strong>LONG:</strong> {{ summary.long_count }} | <strong>SHORT:</strong> {{ summary.short_count }}</p>
    <p><strong>Bias:</strong> {{ summary.leaderboard_bias }} | <strong>Crowding:</strong> {{ summary.crowding_state }} | <strong>Freshness:</strong> {{ summary.freshness_state }} | <strong>Action:</strong> {{ summary.action_effect }}</p>
    <p><strong>High leverage:</strong> {{ summary.high_leverage_count }} | <strong>Extreme ROI:</strong> {{ summary.extreme_roi_count }}</p>
    <p><strong>Top LONG:</strong> {{ summary.top_long_pairs_text }}</p>
    <p><strong>Top SHORT:</strong> {{ summary.top_short_pairs_text }}</p>
    <p><strong>Repeated:</strong> {{ summary.repeated_pairs_text }}</p>
    <p><strong>Τελευταίο snapshot:</strong> {{ summary.last_snapshot_time or "-" }}</p>
  </div>

  <div class="card">
    <h2>Προσθήκη manual snapshot</h2>
    <div class="small">Κάνε paste JSON είτε ως λίστα bots είτε ως <code>{"bots":[...]}</code>. Μη βάζεις API keys ή προσωπικά στοιχεία.</div>
    <form method="post" action="/pionex-bots/add">
      <textarea name="snapshot_json">{{ example_json }}</textarea>
      <p><button class="btn btn-green" type="submit">💾 Αποθήκευση snapshot</button></p>
    </form>
  </div>
</div>
</body>
</html>
"""


@app.route("/pionex-bots", methods=["GET"])
def pionex_bots_page():
    example = {
        "bots": [
            {
                "pair": "RIF/USDT",
                "market": "perp",
                "direction": "SHORT",
                "leverage": 50,
                "roi_percent": 842.0,
                "profit_percent": None,
                "bot_age_text": "6h 20m",
                "rank": 1,
                "source": "manual_pionex_snapshot",
                "notes": "example"
            }
        ]
    }
    return render_template_string(
        PIONEX_BOTS_TEMPLATE,
        summary=summary_for_display(load_pionex_bot_summary()),
        example_json=json.dumps(example, ensure_ascii=False, indent=2),
        last_run_message=LAST_RUN_MESSAGE,
    )


@app.route("/pionex-bots/add", methods=["POST"])
def add_pionex_bots_snapshot():
    global LAST_RUN_MESSAGE
    try:
        if request.is_json:
            payload = request.get_json(silent=True)
        else:
            payload = request.form.get("snapshot_json", "")
        result = add_manual_snapshot(payload)
        summary = result.get("summary", {})
        LAST_RUN_MESSAGE = (
            "✅ Αποθηκεύτηκε manual Pionex snapshot. "
            f"Bots: {summary.get('total_bots_watched', 0)} | "
            f"Bias: {summary.get('leaderboard_bias', 'NEUTRAL')} | "
            f"Action: {summary.get('action_effect', 'WATCH')}. "
            "Παραμένει PAPER TRADING ONLY."
        )
    except Exception as exc:
        LAST_RUN_MESSAGE = f"❌ Δεν αποθηκεύτηκε το Pionex snapshot: {exc}"
    return redirect(url_for("pionex_bots_page"))


@app.route("/reset-demo-wallet", methods=["POST"])
def reset_demo_wallet():
    """Backward-compatible reset με το τρέχον αρχικό κεφάλαιο."""
    account = normalize_account(
        load_json_file(ACCOUNT_FILE, {}),
        default_initial=INITIAL_CAPITAL,
    )
    with app.test_request_context(
        "/new-simulation",
        method="POST",
        data={"initial_capital": account.get("initial_capital", INITIAL_CAPITAL)},
    ):
        return new_simulation()


if __name__ == "__main__":
    print("======================================")
    print("NEXUS AI Trading Assistant - Ελληνικό Demo")
    print("Τοπικά: http://127.0.0.1:5000")
    print("Κινητό στο ίδιο Wi-Fi: http://IP-LAPTOP:5000")
    print("======================================")

    app.run(host="0.0.0.0", port=5000, debug=True)