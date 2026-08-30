# -*- coding: utf-8 -*-
import asyncio
import datetime
import unittest

from main import PointSystemPlugin


class PointSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(PointSystemPlugin)
        self.plugin.data = {
            "users": {
                "1": {"points": 10},
                "2": {"points": -5},
            },
            "groups": {
                "100": {"members": {"1": {}, "2": {}}},
            },
            "point_snapshots": [],
        }

    def test_record_snapshot_replaces_current_bucket(self):
        first = datetime.datetime(2026, 8, 9, 10, 1)
        self.plugin._record_point_snapshot(first)
        self.plugin.data["users"]["1"]["points"] = 20
        self.plugin._record_point_snapshot(first + datetime.timedelta(minutes=9))

        self.assertEqual(len(self.plugin.data["point_snapshots"]), 1)
        self.assertEqual(self.plugin.data["point_snapshots"][0]["total_points"], 15)
        self.assertEqual(
            self.plugin.data["point_snapshots"][0]["groups"]["100"]["total_points"],
            15,
        )

        self.plugin._record_point_snapshot(first + datetime.timedelta(minutes=15))
        self.assertEqual(len(self.plugin.data["point_snapshots"]), 2)

    def test_normalize_snapshot_drops_expired_and_keeps_latest_in_bucket(self):
        now = datetime.datetime(2026, 8, 9, 12, 0)
        snapshots = [
            {
                "captured_at": (now - datetime.timedelta(days=91)).isoformat(),
                "total_points": 1,
            },
            {"captured_at": "2026-08-09T11:01:00", "total_points": 10},
            {"captured_at": "2026-08-09T11:12:00", "total_points": 12},
            {"captured_at": "invalid", "total_points": 999},
        ]

        result = self.plugin._normalize_point_snapshots(snapshots, now=now)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["total_points"], 12)

    def test_failed_save_restores_previous_snapshots(self):
        now = datetime.datetime.now().replace(second=0, microsecond=0)
        self.plugin._record_point_snapshot(now)
        previous = self.plugin.data["point_snapshots"]

        def fail_write():
            raise OSError("write failed")

        self.plugin._write_data_sync = fail_write
        saved = asyncio.run(self.plugin._save_data_locked())

        self.assertFalse(saved)
        self.assertIs(self.plugin.data["point_snapshots"], previous)

    def test_deferred_save_coalesces_repeated_requests(self):
        writes = []
        self.plugin.data_file = "unused-in-test.json"
        self.plugin._data_lock = asyncio.Lock()
        self.plugin._deferred_save_task = None
        self.plugin._deferred_save_generation = 0
        self.plugin._deferred_save_stop_requested = False
        self.plugin._write_serialized_sync = writes.append

        async def run():
            self.plugin._schedule_deferred_save(0.01)
            task = self.plugin._deferred_save_task
            self.plugin._schedule_deferred_save(0.01)
            self.plugin._schedule_deferred_save(0.01)
            await task

        asyncio.run(run())

        self.assertEqual(len(writes), 1)
        self.assertEqual(self.plugin._deferred_save_generation, 3)


if __name__ == "__main__":
    unittest.main()
