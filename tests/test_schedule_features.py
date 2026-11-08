from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Application.Database import database


class ScheduleFeaturesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "schedules.json"
        self.file_patch = patch.object(database, "SCHEDULES_FILE", self.data_file)
        self.file_patch.start()
        database.ensure_data_file()

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_schedule_period_is_fixed_after_creation(self) -> None:
        schedule = database.create_schedule("Учёба", "Week")
        self.assertEqual(schedule["period"], "Week")
        with self.assertRaises(ValueError):
            database.update_schedule(schedule["id"], {"period": "Month"})
        self.assertEqual(database.get_schedule(schedule["id"])["period"], "Week")

    def test_schedule_can_be_pinned_and_unpinned(self) -> None:
        schedule = database.create_schedule("Работа", "Day")
        pinned = database.toggle_schedule_pin(schedule["id"])
        self.assertTrue(pinned["pinned"])
        unpinned = database.toggle_schedule_pin(schedule["id"])
        self.assertFalse(unpinned["pinned"])

    def test_events_and_deadlines_keep_custom_neon_colors(self) -> None:
        schedule = database.create_schedule("Личное", "Week")
        event = database.add_event(schedule["id"], {
            "title": "Тренировка",
            "day_index": 1,
            "start_time_in_minutes": 1440 + 600,
            "end_time_in_minutes": 1440 + 660,
            "color": "#12e8ff",
        })
        deadline = database.create_task(schedule["id"], {
            "title": "Сдать проект",
            "deadline": "2026-11-15",
            "color": "#ff32c8",
        })
        self.assertEqual(event["color"], "#12e8ff")
        self.assertEqual(deadline["color"], "#ff32c8")

    def test_invalid_color_is_rejected(self) -> None:
        schedule = database.create_schedule("Личное", "Day")
        with self.assertRaises(ValueError):
            database.add_event(schedule["id"], {
                "title": "Некорректный цвет",
                "color": "rgb(1,2,3)",
            })


if __name__ == "__main__":
    unittest.main()
