from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent
LATEST_POINTER_NAME = "_LATEST_SCANNER.txt"
ARCHIVE_FILENAME_FORMAT = "%Y-%m-%d_%H-%M-%S.json"


def resolve_project_path(env_name: str, default_relative: str) -> Path:
    """Resolve data paths relative to the project, never to the caller's cwd."""
    raw = os.getenv(env_name, default_relative).strip() or default_relative
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def latest_pointer_path(archive_dir: Path) -> Path:
    return archive_dir / LATEST_POINTER_NAME


def write_latest_pointer(archive_dir: Path, archive_path: Path) -> None:
    """Atomically records the exact scanner archive produced by the latest run."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    pointer = latest_pointer_path(archive_dir)
    temp = pointer.with_suffix(pointer.suffix + ".tmp")
    temp.write_text(archive_path.name, encoding="utf-8")
    temp.replace(pointer)


def _timestamp_from_filename(path: Path) -> Optional[datetime]:
    try:
        return datetime.strptime(path.name, ARCHIVE_FILENAME_FORMAT)
    except ValueError:
        return None


def _timestamp_from_payload(path: Path) -> Optional[datetime]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("timestamp") if isinstance(payload, dict) else None
        if not raw:
            return None
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _sort_key(path: Path) -> Tuple[int, float, str]:
    filename_ts = _timestamp_from_filename(path)
    if filename_ts is not None:
        return (3, filename_ts.timestamp(), path.name)

    payload_ts = _timestamp_from_payload(path)
    if payload_ts is not None:
        return (2, payload_ts.timestamp(), path.name)

    try:
        return (1, path.stat().st_mtime, path.name)
    except OSError:
        return (0, 0.0, path.name)


def find_latest_archive(archive_dir: Path) -> Optional[Path]:
    """
    Returns the exact file pointed to by the last successful scanner run.
    Falls back to the timestamp embedded in the filename, not filesystem mtime.
    """
    if not archive_dir.exists():
        return None

    pointer = latest_pointer_path(archive_dir)
    try:
        pointed_name = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        pointed_name = ""

    if pointed_name:
        candidate = archive_dir / Path(pointed_name).name
        if candidate.is_file() and candidate.suffix.lower() == ".json":
            return candidate

    files = [p for p in archive_dir.glob("*.json") if p.is_file()]
    return max(files, key=_sort_key) if files else None
