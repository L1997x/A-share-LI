from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.refresh_guard import decide_refresh


CN_TZ = ZoneInfo("Asia/Shanghai")


class RefreshGuardTests(unittest.TestCase):
    def test_primary_slot_runs(self) -> None:
        decision = decide_refresh(
            "schedule",
            "53 1 * * 1-5",
            {"generated_at": "2026-07-10T20:00:00+08:00", "update_phase": "evening_watch"},
            datetime(2026, 7, 13, 9, 53, tzinfo=CN_TZ),
        )
        self.assertTrue(decision.run_generator)
        self.assertEqual(decision.effective_schedule, "53 1 * * 1-5")

    def test_backup_skips_a_completed_target_slot(self) -> None:
        decision = decide_refresh(
            "schedule",
            "11 2 * * 1-5",
            {"generated_at": "2026-07-13T09:59:00+08:00", "update_phase": "morning_entry"},
            datetime(2026, 7, 13, 10, 11, tzinfo=CN_TZ),
        )
        self.assertFalse(decision.run_generator)
        self.assertFalse(decision.deploy_required)

    def test_late_slot_skips_when_a_recent_snapshot_exists(self) -> None:
        decision = decide_refresh(
            "schedule",
            "53 1 * * 1-5",
            {"generated_at": "2026-07-13T13:29:00+08:00", "update_phase": "morning_entry"},
            datetime(2026, 7, 13, 13, 45, tzinfo=CN_TZ),
        )
        self.assertFalse(decision.run_generator)

    def test_late_slot_recovers_when_the_snapshot_is_stale(self) -> None:
        decision = decide_refresh(
            "schedule",
            "53 1 * * 1-5",
            {"generated_at": "2026-07-13T10:00:00+08:00", "update_phase": "morning_entry"},
            datetime(2026, 7, 13, 16, 30, tzinfo=CN_TZ),
        )
        self.assertTrue(decision.run_generator)
        self.assertEqual(decision.effective_schedule, "")

    def test_previous_morning_slot_does_not_hide_the_next_one(self) -> None:
        decision = decide_refresh(
            "schedule",
            "13 3 * * 1-5",
            {"generated_at": "2026-07-13T10:00:00+08:00", "update_phase": "morning_entry"},
            datetime(2026, 7, 13, 11, 13, tzinfo=CN_TZ),
        )
        self.assertTrue(decision.run_generator)


if __name__ == "__main__":
    unittest.main()
