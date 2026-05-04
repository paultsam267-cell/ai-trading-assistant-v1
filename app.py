from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from flask import Flask, render_template_string

APP_TITLE = "AI Trading Assistant V1"
ATHENS_TZ = ZoneInfo("Europe/Athens")

ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "archive"))
STATE_DIR = Path(os.getenv("PAPER_STATE_DIR", "paper_state"))

POSITIONS_FILE = STATE_DIR / "paper_positions.json"
TRADES_FILE = STATE_DIR / "paper_trade_log.json"
ACCOUNT_FILE = STATE_DIR / "paper_account.json"

app = Flask(__name__)


HTML_TEMPLATE = """
<!doctype html>
<html lang="el">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
      max-width: 1300px;
      margin: 0 auto;
    }
    h1, h2, h3 {
      margin-top: 0;
    }
    .subtitle {
      color: #94a3b8;
      margin-bottom: 24px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
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
      color: #94a3b8;
      font-size: 14px;
      margin-bottom: 8px;
    }
    .metric-value {
      font-size: 28px;
      font-weight: bold;
    }
    .positive { color: #22c55e; }
    .negative { color: #ef4444; }
    .neutral { color: #f8fafc; }
    .section-grid {
      display: grid;
      grid-template-columns: 1.3fr 1fr;
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
      background: #1f2937;
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
      max-height: 480px;
      overflow: auto;
    }
    a {
      color: #93c5fd;
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    @media (max-width: 1100px) {
      .grid { grid-template-columns: repeat(2, 1fr); }
      .section-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 700px) {
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>📊 {{ title }}</h1>
    <div class="subtitle">
      Demo dashboard εικονικού πορτοφολιού • Τελευταία ενημέρωση: {{ now_str }}
    </div>

    <div class="grid">
      <div class="card">
        <div class="metric-label">💵 Διαθέσιμο Κεφάλαιο</div>
        <div class="metric-value neutral">{{ cash }}</div>
      </div>
      <div class="card">
        <div class="metric-label">📈 Realized PnL</div>
        <div class="metric-value {{ pnl_class }}">{{ realized_pnl }}</div>
      </div>
      <div class="card">
        <div class="metric-label">📦 Ανοιχτές Θέσεις</div>
        <div class="metric-value neutral">{{ open_positions_count }}</div>
      </div>
      <div class="card">
        <div class="metric-label">🏁 Win / Loss</div>
        <div class="metric-value neutral">{{ wins }} / {{ losses }}</div>
      </div>
    </div>

    <div class="section-grid">
      <div class="card">
        <h2>📌 Ανοιχτές Θέσεις</h2>
        {% if positions %}
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Chain</th>
                <th>Entry</th>
                <th>Qty</th>
                <th>Allocated</th>
                <th>Score</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {% for p in positions %}
              <tr>
                <td><strong>{{ p.symbol }}</strong><br><span class="small">{{ p.name }}</span></td>
                <td>{{ p.chain }}<br><span class="small">{{ p.dex }}</span></td>
                <td>{{ p.entry_price_fmt }}</td>
                <td class="mono">{{ p.quantity_fmt }}</td>
                <td>{{ p.allocated_fmt }}</td>
                <td>{{ p.entry_score }}</td>
                <td>{{ p.entry_time }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="small">Δεν υπάρχουν ανοιχτές θέσεις αυτή τη στιγμή.</div>
        {% endif %}
      </div>

      <div class="card">
        <h2>🧾 Λογαριασμός</h2>
        <p><strong>Cash:</strong> {{ cash }}</p>
        <p><strong>Realized PnL:</strong> <span class="{{ pnl_class }}">{{ realized_pnl }}</span></p>
        <p><strong>Wins:</strong> {{ wins }}</p>
        <p><strong>Losses:</strong> {{ losses }}</p>
        <p><strong>Last update:</strong> {{ account.last_update or "-" }}</p>
        <p><strong>Archive file:</strong> {{ latest_archive_name }}</p>
        <p><strong>Candidates στο τελευταίο run:</strong> {{ archive_candidates_count }}</p>
        <p><strong>Reported count:</strong> {{ archive_reported_count }}</p>
      </div>
    </div>

    <div class="section-grid">
      <div class="card">
        <h2>🕘 Τελευταία Trades</h2>
        {% if trades %}
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Symbol</th>
                <th>Price</th>
                <th>PnL</th>
                <th>Reason</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {% for t in trades %}
              <tr>
                <td>
                  {% if t.type == "BUY" %}
                    <span class="badge long">BUY</span>
                  {% else %}
                    <span class="badge short">SELL</span>
                  {% endif %}
                </td>
                <td><strong>{{ t.symbol }}</strong><br><span class="small">{{ t.chain }}</span></td>
                <td>
                  {% if t.type == "BUY" %}
                    {{ t.price_fmt }}
                  {% else %}
                    {{ t.exit_price_fmt }}
                  {% endif %}
                </td>
                <td class="{{ t.pnl_class }}">{{ t.pnl_fmt }}</td>
                <td>{{ t.reason or "-" }}</td>
                <td>{{ t.time }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <div class="small">Δεν υπάρχουν ακόμα trades.</div>
        {% endif %}
      </div>

      <div class="card">
        <h2>📡 Τελευταίο Report</h2>
        {% if latest_report_text %}
          <div class="prebox">{{ latest_report_text }}</div>
        {% else %}
          <div class="small">Δεν βρέθηκε archive report.</div>
        {% endif %}
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


def now_local_str() -> str:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ).strftime("%d/%m/%Y %H:%M:%S")


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def format_money(value: float) -> str:
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
    if value >= 1:
        return f"${value:.4f}"
    if value >= 0.01:
        return f"${value:.6f}"
    if value >= 0.0001:
        return f"${value:.8f}"
    return f"${value:.10f}"


def latest_archive_payload() -> Dict[str, Any]:
    if not ARCHIVE_DIR.exists():
        return {}

    files = sorted(ARCHIVE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {}

    path = files[0]
    data = load_json_file(path, {})
    if isinstance(data, dict):
        data["_filename"] = path.name
        return data
    return {}


def pnl_class(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "neutral"


@app.route("/")
def dashboard():
    account = load_json_file(
        ACCOUNT_FILE,
        {"cash": 1000.0, "realized_pnl": 0.0, "wins": 0, "losses": 0, "last_update": None},
    )
    raw_positions = load_json_file(POSITIONS_FILE, {})
    raw_trades = load_json_file(TRADES_FILE, [])
    archive = latest_archive_payload()

    positions: List[Dict[str, Any]] = []
    if isinstance(raw_positions, dict):
        for _, p in raw_positions.items():
            if not isinstance(p, dict):
                continue
            positions.append(
                {
                    **p,
                    "entry_price_fmt": format_price(safe_float(p.get("entry_price"))),
                    "quantity_fmt": f"{safe_float(p.get('quantity')):.6f}",
                    "allocated_fmt": format_money(safe_float(p.get("allocated_usd"))),
                }
            )

    trades: List[Dict[str, Any]] = []
    if isinstance(raw_trades, list):
        for t in raw_trades[-10:][::-1]:
            if not isinstance(t, dict):
                continue
            pnl_usd = safe_float(t.get("pnl_usd"))
            trades.append(
                {
                    **t,
                    "price_fmt": format_price(safe_float(t.get("price"))),
                    "exit_price_fmt": format_price(safe_float(t.get("exit_price"))),
                    "pnl_fmt": format_money(pnl_usd) if t.get("type") == "SELL" else "-",
                    "pnl_class": pnl_class(pnl_usd),
                }
            )

    realized = safe_float(account.get("realized_pnl"))
    return render_template_string(
        HTML_TEMPLATE,
        title=APP_TITLE,
        now_str=now_local_str(),
        account=account,
        cash=format_money(safe_float(account.get("cash"))),
        realized_pnl=format_money(realized),
        pnl_class=pnl_class(realized),
        open_positions_count=len(positions),
        wins=int(account.get("wins", 0)),
        losses=int(account.get("losses", 0)),
        positions=positions,
        trades=trades,
        latest_archive_name=archive.get("_filename", "-"),
        archive_candidates_count=archive.get("candidates_count", "-"),
        archive_reported_count=archive.get("reported_count", "-"),
        latest_report_text=archive.get("report_text", ""),
    )


if __name__ == "__main__":
    app.run(debug=True)
