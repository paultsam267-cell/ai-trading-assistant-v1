from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from archive_utils import find_latest_archive, write_latest_pointer


class ArchiveSelectionTests(unittest.TestCase):
    def _write_archive(self, directory: Path, name: str, timestamp: str) -> Path:
        path = directory / name
        path.write_text(json.dumps({"timestamp": timestamp, "items": []}), encoding="utf-8")
        return path

    def test_pointer_selects_exact_scanner_file_even_when_mtime_is_older(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            old = self._write_archive(
                archive,
                "2026-07-02_14-01-01.json",
                "2026-07-02T14:01:01+03:00",
            )
            new = self._write_archive(
                archive,
                "2026-07-02_14-59-18.json",
                "2026-07-02T14:59:18+03:00",
            )
            os.utime(old, (2_000_000_000, 2_000_000_000))
            os.utime(new, (1_000_000_000, 1_000_000_000))
            write_latest_pointer(archive, new)
            self.assertEqual(find_latest_archive(archive), new)

    def test_filename_timestamp_wins_when_pointer_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            old = self._write_archive(
                archive,
                "2026-07-02_14-01-01.json",
                "2026-07-02T14:01:01+03:00",
            )
            new = self._write_archive(
                archive,
                "2026-07-02_14-59-18.json",
                "2026-07-02T14:59:18+03:00",
            )
            os.utime(old, (2_000_000_000, 2_000_000_000))
            os.utime(new, (1_000_000_000, 1_000_000_000))
            self.assertEqual(find_latest_archive(archive), new)

    def test_invalid_pointer_falls_back_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            expected = self._write_archive(
                archive,
                "2026-07-02_15-02-00.json",
                "2026-07-02T15:02:00+03:00",
            )
            (archive / "_LATEST_SCANNER.txt").write_text("missing.json", encoding="utf-8")
            self.assertEqual(find_latest_archive(archive), expected)


if __name__ == "__main__":
    unittest.main()
