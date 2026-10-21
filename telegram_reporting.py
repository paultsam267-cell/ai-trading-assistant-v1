"""Canonical PAPER-only Telegram formatting and routing checks."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Mapping, Optional

import requests


BRAND = "P-DJD NEXUS AI TRADING ASSISTANT"
PAPER_NOTICE = "PAPER TRADING ONLY"
EXPECTED_BOT_USERNAME = "PDJD_Nexus_Trading_Bot"
TELEGRAM_API = "https://api.telegram.org"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def money(value: Any, *, signed: bool = False) -> str:
    number = _number(value)
    sign = "+" if signed and number > 0 else ""
    return f"{sign}€{number:,.2f}"


def percent(value: Any) -> str:
    number = _number(value)
    return f"{'+' if number > 0 else ''}{number:.2f}%"


def with_paper_header(text: str) -> str:
    body = str(text or "").strip()
    header = f"{BRAND}\n{PAPER_NOTICE}"
    return body if body.startswith(header) else f"{header}\n\n{body}"


def format_portfolio_report(
    account: Mapping[str, Any],
    positions: Mapping[str, Mapping[str, Any]],
    trades: Iterable[Mapping[str, Any]],
    *,
    current_equity: float,
    unrealized_pnl: Optional[float],
    verified_at: Optional[str] = None,
) -> str:
    initial = _number(account.get("initial_capital"))
    realized = _number(account.get("realized_pnl"))
    total_pnl = current_equity - initial
    total_return = (total_pnl / initial * 100.0) if initial > 0 else None
    trade_rows = list(trades)
    unrealized_text = (
        money(unrealized_pnl, signed=True)
        if unrealized_pnl is not None
        else "N/A (υπάρχει STALE/MISSING αποτίμηση)"
    )
    return with_paper_header(
        "PORTFOLIO REPORT\n"
        f"Simulation ID: {account.get('simulation_id') or 'N/A (δεν έχει καταγραφεί)'}\n"
        f"Initial capital: {money(initial)}\n"
        f"Current equity: {money(current_equity)}\n"
        f"Available cash: {money(account.get('cash'))}\n"
        f"Reserve cash: {money(account.get('reserve_cash'))}\n"
        f"Open PAPER positions: {len(positions)}\n"
        f"Total PAPER trades: {len(trade_rows)}\n"
        f"Realized P/L: {money(realized, signed=True)}\n"
        f"Unrealized P/L: {unrealized_text}\n"
        f"Total P/L: {money(total_pnl, signed=True)}\n"
        f"Total return: {percent(total_return) if total_return is not None else 'N/A (μηδενικό initial capital)'}\n"
        f"Last verified update: {verified_at or account.get('last_update') or 'N/A (δεν έχει καταγραφεί)'}"
    )


def format_trade_report(record: Mapping[str, Any], *, freshness_status: Optional[str] = None) -> str:
    entry = record.get("entry_price")
    exit_price = record.get("exit_price")
    price = record.get("price")
    pnl = record.get("pnl_usd")
    pnl_pct = record.get("pnl_pct")
    size = record.get("allocated_usd", record.get("cost_basis_usd", record.get("margin_usd")))
    side = str(record.get("side") or "N/A").upper()
    title = "TRADE REPORT — SYNTHETIC SHORT" if side == "SHORT" else "TRADE REPORT"
    return with_paper_header(
        f"{title}\n"
        f"Asset class: {record.get('asset_class') or 'N/A (δεν έχει καταγραφεί)'}\n"
        f"Symbol: {record.get('symbol') or 'N/A (δεν έχει καταγραφεί)'}\n"
        f"Side: {side}\n"
        f"Entry price: {entry if entry is not None else price if str(record.get('type')).upper() in {'BUY', 'SHORT_OPEN'} else 'N/A'}\n"
        f"Exit price: {exit_price if exit_price is not None else 'N/A (η θέση δεν έκλεισε)'}\n"
        f"PAPER position size: {money(size) if size is not None else 'N/A (δεν έχει καταγραφεί)'}\n"
        f"P/L: {money(pnl, signed=True) if pnl is not None else 'N/A (δεν έχει πραγματοποιηθεί έξοδος)'}\n"
        f"Return: {percent(pnl_pct) if pnl_pct is not None else 'N/A (ανεπαρκές ιστορικό)'}\n"
        f"Stop loss: {record.get('stop_loss') if record.get('stop_loss') is not None else 'N/A'}\n"
        f"Take profit: {record.get('take_profit') if record.get('take_profit') is not None else 'N/A'}\n"
        f"Exit reason: {record.get('reason') or 'N/A (δεν έχει πραγματοποιηθεί έξοδος)'}\n"
        f"Data freshness: {freshness_status or record.get('price_status') or 'N/A (δεν έχει καταγραφεί)'}"
    )


def routing_status(
    *,
    session: Any = requests,
    timeout: int = 20,
    perform_network_check: bool = False,
) -> Dict[str, Any]:
    """Validate bot identity and chat routing without sending a message."""

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    result: Dict[str, Any] = {
        "credentials_present": bool(token and chat_id),
        "bot_identity_verified": False,
        "chat_routing_verified": False,
        "expected_bot_username": EXPECTED_BOT_USERNAME,
        "network_check_performed": False,
    }
    if not token or not chat_id or not perform_network_check:
        return result

    result["network_check_performed"] = True
    bot_response = session.get(f"{TELEGRAM_API}/bot{token}/getMe", timeout=timeout)
    bot_response.raise_for_status()
    bot_payload = bot_response.json()
    bot = bot_payload.get("result", {}) if isinstance(bot_payload, dict) else {}
    result["bot_identity_verified"] = str(bot.get("username") or "").lstrip("@").lower() == EXPECTED_BOT_USERNAME.lower()

    chat_response = session.get(
        f"{TELEGRAM_API}/bot{token}/getChat",
        params={"chat_id": chat_id},
        timeout=timeout,
    )
    chat_response.raise_for_status()
    chat_payload = chat_response.json()
    result["chat_routing_verified"] = bool(
        isinstance(chat_payload, dict) and chat_payload.get("ok") and chat_payload.get("result")
    )
    return result


def send_notification(text: str, *, session: Any = requests, timeout: int = 20) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    prepared = with_paper_header(text)
    for start in range(0, len(prepared), 3900):
        response = session.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": prepared[start : start + 3900],
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        response.raise_for_status()
    return True
