from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import state_store


def _increment_worker(state_dir: str, iterations: int) -> None:
    directory = Path(state_dir)
    counter_file = directory / "counter.json"
    for _ in range(iterations):
        with state_store.portfolio_lock(directory, timeout=5.0):
            payload = state_store.load_json(counter_file, {"value": 0})
            payload["value"] = int(payload.get("value", 0)) + 1
            state_store.atomic_write_json(counter_file, payload)


class StateConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "paper_state"
        self.state_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_deterministic_interprocess_read_modify_write(self):
        counter = self.state_dir / "counter.json"
        state_store.atomic_write_json(counter, {"value": 0})
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=_increment_worker, args=(str(self.state_dir), 20)) for _ in range(4)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(state_store.load_json(counter, {})["value"], 80)

    def test_lock_timeout_fails_closed(self):
        first = state_store.portfolio_lock(self.state_dir, timeout=1.0)
        first.acquire()
        try:
            with self.assertRaises(state_store.LockTimeoutError):
                state_store.portfolio_lock(self.state_dir, timeout=0.05).acquire()
        finally:
            first.release()

    def test_duplicate_monitor_startup_is_rejected(self):
        with state_store.monitor_instance_guard(self.state_dir, "paper_risk_monitor"):
            with self.assertRaises(state_store.DuplicateMonitorError):
                with state_store.monitor_instance_guard(self.state_dir, "paper_risk_monitor"):
                    pass

    def test_atomic_replace_failure_preserves_original(self):
        target = self.state_dir / "state.json"
        state_store.atomic_write_json(target, {"version": 1})
        with patch.object(state_store.os, "replace", side_effect=OSError("injected replace failure")):
            with self.assertRaises(OSError):
                state_store.atomic_write_json(target, {"version": 2})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1})
        self.assertEqual(list(self.state_dir.glob(".state.json.*.tmp")), [])

    def test_serialization_failure_leaves_no_corrupt_file(self):
        target = self.state_dir / "state.json"
        state_store.atomic_write_json(target, {"version": 1})
        with self.assertRaises(TypeError):
            state_store.atomic_write_json(target, {"bad": object()})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1})


if __name__ == "__main__":
    unittest.main()
