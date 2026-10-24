from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

from Application.Database import database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = PROJECT_ROOT / "Application" / "Site" / "app.py"


def load_build_rows():
    """Load only build_rows from app.py, without importing Flask routes."""
    module = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_rows"
    )
    namespace = {
        "Any": Any,
        "date": date,
        "MINUTES_PER_DAY": 1440,
        "WEEKDAYS": [
            "Понедельник", "Вторник", "Среда", "Четверг",
            "Пятница", "Суббота", "Воскресенье",
        ],
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(APP_SOURCE), "exec"), namespace)
    return namespace["build_rows"]


class TimelineLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "schedules.json"
        self.file_patch = patch.object(database, "SCHEDULES_FILE", self.data_file)
        self.file_patch.start()
        database.ensure_data_file()

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_ranges_partial_boundaries_and_unscheduled_markers(self) -> None:
        build_rows = load_build_rows()
        schedule = {
            "id": 1,
            "name": "Неделя",
            "period": "Week",
            "events": [
                {
                    "id": 1, "type": "event", "title": "Лекция",
                    "start_time_in_minutes": 9 * 60,
                    "end_time_in_minutes": 12 * 60,
                },
                {
                    "id": 2, "type": "event", "title": "Встреча",
                    "start_time_in_minutes": 10 * 60,
                    "end_time_in_minutes": 13 * 60,
                },
                {
                    "id": 3, "type": "task", "title": "Только начало",
                    "start_time_in_minutes": 15 * 60,
                },
                {
                    "id": 4, "type": "task", "title": "Сдать отчёт",
                    "deadline": "2026-10-28",
                },
                {
                    "id": 5, "type": "task", "title": "Без даты",
                },
            ],
        }
        rows = build_rows(schedule, "Week")

        self.assertEqual(rows[0]["label"], "Понедельник")
        self.assertEqual(rows[0]["events"][0]["mode"], "range")
        self.assertEqual(rows[0]["events"][0]["start_label"], "09:00")
        self.assertEqual(rows[0]["events"][0]["end_label"], "12:00")
        self.assertEqual(rows[0]["events"][1]["mode"], "range")
        self.assertLess(
            rows[0]["events"][0]["left_percent"],
            rows[0]["events"][1]["left_percent"] + rows[0]["events"][1]["width_percent"],
        )
        self.assertEqual(rows[0]["events"][2]["mode"], "marker")
        self.assertEqual(rows[0]["events"][2]["start_label"], "15:00")
        self.assertEqual(rows[0]["events"][2]["end_label"], "")
        self.assertEqual(rows[2]["events"][0]["title"], "Сдать отчёт")  # Deadline is Wednesday.
        self.assertEqual(rows[-1]["label"], "Без дня")
        self.assertEqual(rows[-1]["events"][0]["title"], "Без даты")
        self.assertEqual(rows[-1]["events"][0]["mode"], "marker")
        self.assertEqual(rows[-1]["events"][0]["left_percent"], 50.0)

    def test_task_agent_can_create_task_with_one_time_boundary(self) -> None:
        schedule = database.create_schedule("Учёба", "Week")
        task = database.create_task(schedule["id"], {
            "title": "Позвонить преподавателю",
            "start_time_in_minutes": 15 * 60,
        })
        self.assertEqual(task["start_time_in_minutes"], 900)
        self.assertNotIn("end_time_in_minutes", task)

        updated = database.update_task(schedule["id"], task["id"], {
            "end_time_in_minutes": 16 * 60,
        })
        self.assertEqual(updated["start_time_in_minutes"], 900)
        self.assertEqual(updated["end_time_in_minutes"], 960)

        partial = database.update_task(schedule["id"], task["id"], {
            "end_time_in_minutes": None,
        })
        self.assertEqual(partial["start_time_in_minutes"], 900)
        self.assertNotIn("end_time_in_minutes", partial)

    def test_event_with_missing_time_boundary_is_a_marker(self) -> None:
        schedule = database.create_schedule("Неделя", "Week")
        schedule_id = schedule["id"]
        event = database.add_event(schedule_id, {
            "title": "Событие без конца",
            "day_index": 1,
            "start_time_in_minutes": 10 * 60 + 1440,
        })
        self.assertNotIn("end_time_in_minutes", event)

        rows = load_build_rows()(database.get_schedule(schedule_id), "Week")
        self.assertEqual(rows[1]["label"], "Вторник")
        self.assertEqual(rows[1]["events"][0]["mode"], "marker")
        self.assertEqual(rows[1]["events"][0]["start_label"], "10:00")
        self.assertEqual(rows[1]["events"][0]["end_label"], "")

        end_only = database.add_event(schedule_id, {
            "title": "Событие только с концом",
            "day_index": 3,
            "end_time_in_minutes": 3 * 1440 + 11 * 60,
        })
        self.assertNotIn("start_time_in_minutes", end_only)
        rows = load_build_rows()(database.get_schedule(schedule_id), "Week")
        thursday_item = next(item for item in rows[3]["events"] if item["title"] == "Событие только с концом")
        self.assertEqual(thursday_item["mode"], "marker")
        self.assertEqual(thursday_item["start_label"], "")
        self.assertEqual(thursday_item["end_label"], "11:00")

        # Полностью не назначенное по времени событие остаётся квадратом на своём дне.
        untimed = database.add_event(schedule_id, {
            "title": "Событие без времени", "day_index": 4,
        })
        self.assertNotIn("start_time_in_minutes", untimed)
        self.assertNotIn("end_time_in_minutes", untimed)
        rows = load_build_rows()(database.get_schedule(schedule_id), "Week")
        friday_item = next(item for item in rows[4]["events"] if item["title"] == "Событие без времени")
        self.assertEqual(friday_item["mode"], "marker")
        self.assertEqual(friday_item["left_percent"], 50.0)

    def test_task_delete_does_not_delete_an_event(self) -> None:
        schedule_id = database.create_schedule("Неделя", "Week")["id"]
        event = database.add_event(schedule_id, {
            "title": "Лекция", "start_time_in_minutes": 600,
            "end_time_in_minutes": 660,
        })
        task = database.create_task(schedule_id, {"title": "Сдать отчёт"})
        database.delete_task(schedule_id, task["id"])
        remaining = database.get_schedule(schedule_id)["events"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], event["id"])
        self.assertEqual(remaining[0]["type"], "event")

    def test_task_day_index_places_unscheduled_task_on_selected_weekday(self) -> None:
        schedule = database.create_schedule("Учёба", "Week")
        task = database.create_task(schedule["id"], {
            "title": "Позвонить",
            "day_index": 3,
        })
        rows = load_build_rows()(database.get_schedule(schedule["id"]), "Week")
        self.assertEqual(rows[3]["label"], "Четверг")
        self.assertEqual(rows[3]["events"][0]["id"], task["id"])
        self.assertEqual(rows[3]["events"][0]["mode"], "marker")


    def test_update_and_delete_item_use_ids_for_both_types(self) -> None:
        schedule_id = database.create_schedule("Неделя", "Week")["id"]
        event = database.add_event(schedule_id, {
            "title": "Лекция",
            "day_index": 0,
            "start_time_in_minutes": 9 * 60,
            "end_time_in_minutes": 10 * 60,
        })
        task = database.create_task(schedule_id, {
            "title": "Сдать отчёт",
            "day_index": 1,
            "start_time_in_minutes": 11 * 60 + 1440,
            "end_time_in_minutes": 12 * 60 + 1440,
        })

        moved_event = database.update_item(schedule_id, event["id"], {
            "day_index": 2,
            "start_time_in_minutes": 14 * 60 + 2 * 1440,
            "end_time_in_minutes": 15 * 60 + 2 * 1440,
        })
        self.assertEqual(moved_event["type"], "event")
        self.assertEqual(moved_event["start_time_in_minutes"], 2 * 1440 + 14 * 60)

        moved_task = database.update_item(schedule_id, task["id"], {
            "day_index": 3,
            "start_time_in_minutes": 16 * 60 + 3 * 1440,
            "end_time_in_minutes": 17 * 60 + 3 * 1440,
        })
        self.assertEqual(moved_task["type"], "task")
        self.assertEqual(moved_task["day_index"], 3)

        database.delete_item(schedule_id, event["id"])
        remaining = database.get_schedule(schedule_id)["events"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], task["id"])
        self.assertEqual(remaining[0]["type"], "task")

    def test_update_item_can_move_undated_marker_to_a_day(self) -> None:
        schedule_id = database.create_schedule("Неделя", "Week")["id"]
        task = database.create_task(schedule_id, {"title": "Без времени"})
        updated = database.update_item(schedule_id, task["id"], {"day_index": 4})
        self.assertEqual(updated["day_index"], 4)
        self.assertNotIn("start_time_in_minutes", updated)
        self.assertNotIn("end_time_in_minutes", updated)

if __name__ == "__main__":
    unittest.main()
