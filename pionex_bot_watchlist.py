"""Manual Pionex Bot Watchlist V1.

Read-only advisory module for manual Pionex leaderboard snapshots.
No API keys, no scraping, no real orders.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from archive_utils import resolve_project_path

ATHENS_TZ = ZoneInfo("Europe/Athens")
STATE_DIR = resolve_project_path("INTELLIGENCE_STATE_DIR", "intelligence_state")
SNAPSHOT_DIR = STATE_DIR / "pionex_bot_snapshots"
WATCHLIST_FILE = STATE_DIR / "pionex_bot_watchlist.json"
SUMMARY_FILE = STATE_DIR / "pionex_bot_summary.json"

DIRECTIONS = {"LONG", "SHORT"}
NO_SNAPSHOT_SUMMARY: Dict[str, Any] = {
    "status": "NO_SNAPSHOTS",
    "total_bots_watched": 0,
    "long_count": 0,
    "short_count": 0,
    "unknown_count": 0,
    "mixed_count": 0,
    "top_long_pairs": [],
    "top_short_pairs": [],
    "repeated_pairs": [],
    "high_leverage_count": 0,
    "extreme_roi_count": 0,
    "leaderboard_bias": "NEUTRAL",
    "crowding_state": "NO_CROWDING",
    "freshness_state": "UNKNOWN",
    "action_effect": "WATCH",
    "last_snapshot_time": None,
    "latest_snapshot_id": None,
    "snapshot_count": 0,
    "notes": "Manual Pionex snapshots only. No API key. No real orders.",
}


def now_athens() -> datetime:
    return datetime.now(timezone.utc).astimezone(ATHENS_TZ)


def iso_now() -> str:
    return now_athens().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def default_watchlist() -> Dict[str, Any]:
    at = iso_now()
    return {
        "created_at": at,
        "updated_at": at,
        "notes": "Manual Pionex leaderboard snapshots only. No real trading.",
        "bots": [],
    }


def ensure_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if not WATCHLIST_FILE.exists():
        atomic_json(WATCHLIST_FILE, default_watchlist())
    if not SUMMARY_FILE.exists():
        atomic_json(SUMMARY_FILE, dict(NO_SNAPSHOT_SUMMARY))


def load_watchlist() -> Dict[str, Any]:
    ensure_state()
    raw = load_json(WATCHLIST_FILE, default_watchlist())
    if not isinstance(raw, dict):
        raw = default_watchlist()
    raw.setdefault("created_at", iso_now())
    raw.setdefault("updated_at", iso_now())
    raw.setdefault("notes", "Manual Pionex leaderboard snapshots only. No real trading.")
    raw.setdefault("bots", [])
    if not isinstance(raw.get("bots"), list):
        raw["bots"] = []
    return raw


def save_watchlist(payload: Mapping[str, Any]) -> None:
    data = dict(payload)
    data["updated_at"] = iso_now()
    data.setdefault("created_at", data["updated_at"])
    data.setdefault("notes", "Manual Pionex leaderboard snapshots only. No real trading.")
    data.setdefault("bots", [])
    atomic_json(WATCHLIST_FILE, data)


def normalize_pair(value: Any) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    if not raw:
        return "UNKNOWN"
    raw = raw.replace("_", "/")
    if raw.endswith("PERP") and "/" not in raw:
        raw = raw.replace("USDT", "/USDT")
    return raw


def normalize_direction(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in DIRECTIONS:
        return raw
    if "LONG" in raw:
        return "LONG"
    if "SHORT" in raw:
        return "SHORT"
    return "UNKNOWN"


def parse_age_hours(value: Any) -> Optional[float]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    total = 0.0
    # Supports formats such as 45m, 1h 20m, 2d 4h, 0.5h, 6 hours.
    for number, unit in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes)", raw):
        n = safe_float(number)
        if unit.startswith("d"):
            total += n * 24.0
        elif unit.startswith("h"):
            total += n
        elif unit.startswith("m"):
            total += n / 60.0
    if total > 0:
        return total
    # Plain number is treated as hours.
    plain = safe_float(raw, -1.0)
    return plain if plain >= 0 else None


def normalize_bot(row: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, Mapping):
        row = {}
    direction = normalize_direction(row.get("direction") or row.get("side"))
    leverage_text = row.get("leverage", 0)
    if isinstance(leverage_text, str):
        leverage_text = leverage_text.upper().replace("X", "")
    age_text = str(row.get("bot_age_text") or row.get("age") or "").strip()
    return {
        "pair": normalize_pair(row.get("pair") or row.get("symbol")),
        "market": str(row.get("market") or "perp").strip().lower() or "perp",
        "direction": direction,
        "leverage": safe_int(leverage_text, 0),
        "roi_percent": safe_float(row.get("roi_percent", row.get("roi", 0.0))),
        "profit_percent": row.get("profit_percent"),
        "bot_age_text": age_text,
        "bot_age_hours": parse_age_hours(age_text),
        "rank": safe_int(row.get("rank"), 0),
        "source": str(row.get("source") or "manual_pionex_snapshot"),
        "notes": str(row.get("notes") or ""),
    }


def normalize_snapshot_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, Mapping):
        rows = payload.get("bots", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    if not isinstance(rows, list):
        rows = []
    return [normalize_bot(row if isinstance(row, Mapping) else {}) for row in rows]


def snapshot_filename(at: Optional[datetime] = None) -> str:
    stamp = (at or now_athens()).strftime("%Y-%m-%d_%H-%M-%S")
    return f"pionex_bot_snapshot_{stamp}.json"


def add_manual_snapshot(payload: Any, *, at: Optional[datetime] = None) -> Dict[str, Any]:
    ensure_state()
    captured = at or now_athens()
    bots = normalize_snapshot_payload(payload)
    snapshot_id = captured.strftime("%Y-%m-%d_%H-%M-%S")
    snapshot = {
        "snapshot_id": snapshot_id,
        "captured_at": captured.isoformat(timespec="seconds"),
        "source": "manual_pionex_snapshot",
        "paper_only": True,
        "notes": "Manual Pionex snapshots only. No API key. No real orders.",
        "bots": bots,
    }
    path = SNAPSHOT_DIR / snapshot_filename(captured)
    atomic_json(path, snapshot)
    watchlist = load_watchlist()
    watchlist["bots"] = bots
    watchlist["last_snapshot_file"] = path.name
    watchlist["last_snapshot_time"] = snapshot["captured_at"]
    save_watchlist(watchlist)
    summary = analyze_snapshots()
    atomic_json(SUMMARY_FILE, summary)
    return {"snapshot": snapshot, "summary": summary, "path": str(path)}


def list_snapshot_files() -> List[Path]:
    ensure_state()
    return sorted(SNAPSHOT_DIR.glob("pionex_bot_snapshot_*.json"))


def load_snapshots(limit: int = 200) -> List[Dict[str, Any]]:
    files = list_snapshot_files()[-limit:]
    snapshots: List[Dict[str, Any]] = []
    for path in files:
        payload = load_json(path, {})
        if isinstance(payload, dict):
            payload.setdefault("_filename", path.name)
            bots = payload.get("bots", [])
            if isinstance(bots, list):
                payload["bots"] = [normalize_bot(bot if isinstance(bot, Mapping) else {}) for bot in bots]
            snapshots.append(payload)
    return snapshots


def _top_pairs(counter: Counter, limit: int = 5) -> List[Dict[str, Any]]:
    return [{"pair": pair, "count": count} for pair, count in counter.most_common(limit)]


def _leaderboard_bias(long_count: int, short_count: int, unknown_count: int) -> str:
    total_known = long_count + short_count
    if total_known == 0:
        return "NEUTRAL"
    if long_count > 0 and short_count > 0:
        if max(long_count, short_count) / max(total_known, 1) < 0.65:
            return "MIXED"
    if long_count >= short_count * 1.5 and long_count >= 2:
        return "LONG"
    if short_count >= long_count * 1.5 and short_count >= 2:
        return "SHORT"
    return "MIXED" if long_count and short_count else "LONG" if long_count else "SHORT" if short_count else "NEUTRAL"


def _freshness(ages: Sequence[Optional[float]], extreme_roi_count: int, total: int) -> str:
    valid = sorted(age for age in ages if age is not None)
    if not valid:
        return "UNKNOWN"
    median_age = valid[len(valid) // 2]
    extreme_ratio = extreme_roi_count / max(total, 1)
    if median_age <= 2 and extreme_ratio < 0.5:
        return "EARLY"
    if median_age <= 12 and extreme_ratio < 0.7:
        return "MID"
    return "LATE"


def _crowding(long_pairs: Counter, short_pairs: Counter) -> str:
    long_crowded = any(count >= 2 for count in long_pairs.values())
    short_crowded = any(count >= 2 for count in short_pairs.values())
    if long_crowded and short_crowded:
        return "MIXED_CROWDING"
    if long_crowded:
        return "LONG_CROWDING"
    if short_crowded:
        return "SHORT_CROWDING"
    return "NO_CROWDING"


def _action_effect(bias: str, crowding: str, freshness: str, extreme_roi_count: int, total: int) -> str:
    if total <= 0:
        return "WATCH"
    extreme_ratio = extreme_roi_count / max(total, 1)
    if crowding in {"MIXED_CROWDING", "LONG_CROWDING", "SHORT_CROWDING"} and extreme_ratio >= 0.5:
        return "REDUCE"
    if bias == "MIXED":
        return "WATCH"
    if freshness == "LATE" or extreme_ratio >= 0.5:
        return "WATCH" if crowding == "NO_CROWDING" else "REDUCE"
    if bias in {"LONG", "SHORT"} and crowding == "NO_CROWDING" and freshness in {"EARLY", "MID"}:
        return "BOOST"
    return "WATCH"


def analyze_snapshots(snapshots: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    ensure_state()
    loaded = list(snapshots) if snapshots is not None else load_snapshots()
    if not loaded:
        summary = dict(NO_SNAPSHOT_SUMMARY)
        atomic_json(SUMMARY_FILE, summary)
        return summary

    latest = loaded[-1]
    latest_bots = latest.get("bots", []) if isinstance(latest, Mapping) else []
    bots = [normalize_bot(bot if isinstance(bot, Mapping) else {}) for bot in latest_bots if isinstance(bot, Mapping)]
    total = len(bots)
    long_bots = [bot for bot in bots if bot.get("direction") == "LONG"]
    short_bots = [bot for bot in bots if bot.get("direction") == "SHORT"]
    unknown_bots = [bot for bot in bots if bot.get("direction") not in DIRECTIONS]
    long_pairs = Counter(bot["pair"] for bot in long_bots if bot.get("pair") != "UNKNOWN")
    short_pairs = Counter(bot["pair"] for bot in short_bots if bot.get("pair") != "UNKNOWN")

    all_pair_counter: Counter = Counter()
    for snap in loaded:
        for bot in snap.get("bots", []) if isinstance(snap, Mapping) else []:
            if isinstance(bot, Mapping):
                pair = normalize_pair(bot.get("pair") or bot.get("symbol"))
                if pair != "UNKNOWN":
                    all_pair_counter[pair] += 1

    repeated = [{"pair": pair, "count": count} for pair, count in all_pair_counter.most_common(10) if count >= 2]
    high_leverage_count = sum(1 for bot in bots if safe_float(bot.get("leverage")) >= 50)
    extreme_roi_count = sum(1 for bot in bots if safe_float(bot.get("roi_percent")) >= 300)
    bias = _leaderboard_bias(len(long_bots), len(short_bots), len(unknown_bots))
    crowding = _crowding(long_pairs, short_pairs)
    freshness = _freshness([bot.get("bot_age_hours") for bot in bots], extreme_roi_count, total)
    action = _action_effect(bias, crowding, freshness, extreme_roi_count, total)

    summary = {
        "status": "OK",
        "total_bots_watched": total,
        "long_count": len(long_bots),
        "short_count": len(short_bots),
        "unknown_count": len(unknown_bots),
        "mixed_count": min(len(long_bots), len(short_bots)),
        "top_long_pairs": _top_pairs(long_pairs),
        "top_short_pairs": _top_pairs(short_pairs),
        "repeated_pairs": repeated,
        "high_leverage_count": high_leverage_count,
        "extreme_roi_count": extreme_roi_count,
        "leaderboard_bias": bias,
        "crowding_state": crowding,
        "freshness_state": freshness,
        "action_effect": action,
        "last_snapshot_time": latest.get("captured_at") if isinstance(latest, Mapping) else None,
        "latest_snapshot_id": latest.get("snapshot_id") if isinstance(latest, Mapping) else None,
        "snapshot_count": len(loaded),
        "notes": "Manual Pionex snapshots only. No API key. No real orders.",
    }
    atomic_json(SUMMARY_FILE, summary)
    return summary


def load_summary() -> Dict[str, Any]:
    ensure_state()
    payload = load_json(SUMMARY_FILE, {})
    if not isinstance(payload, dict) or not payload:
        return analyze_snapshots()
    return {**NO_SNAPSHOT_SUMMARY, **payload}


def scanner_advisory(summary: Optional[Mapping[str, Any]], scanner_signal: str) -> Dict[str, Any]:
    """Conservative advisory hook for future allocation integration."""
    s = summary or load_summary()
    signal = str(scanner_signal or "").upper()
    bias = str(s.get("leaderboard_bias", "NEUTRAL")).upper()
    action = str(s.get("action_effect", "WATCH")).upper()
    if bias in {"LONG", "SHORT"} and signal in {"LONG", "SHORT"} and bias != signal:
        action = "BLOCK" if s.get("crowding_state") != "NO_CROWDING" else "REDUCE"
    elif action == "BOOST" and bias != signal:
        action = "WATCH"
    return {"scanner_signal": signal or "UNKNOWN", "leaderboard_bias": bias, "action_effect": action}


def summary_for_display(summary: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    s = dict(summary or load_summary())
    def pairs_text(rows: Any) -> str:
        if not isinstance(rows, list) or not rows:
            return "-"
        parts = []
        for row in rows[:5]:
            if isinstance(row, Mapping):
                parts.append(f"{row.get('pair', '-') } ({row.get('count', 0)})")
        return ", ".join(parts) if parts else "-"
    return {
        **s,
        "top_long_pairs_text": pairs_text(s.get("top_long_pairs")),
        "top_short_pairs_text": pairs_text(s.get("top_short_pairs")),
        "repeated_pairs_text": pairs_text(s.get("repeated_pairs")),
    }


if __name__ == "__main__":
    ensure_state()
    print(json.dumps(summary_for_display(analyze_snapshots()), ensure_ascii=False, indent=2))
