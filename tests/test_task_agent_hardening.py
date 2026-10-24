from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from Application.Agents.TaskAgent.Agent import TaskAgent
from Application.Database import database


class TaskAgentHardeningTests(unittest.TestCase):
    """Boundary and regression tests for TaskAgent and the JSON database."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "schedules.json"
        self.file_patch = patch.object(database, "SCHEDULES_FILE", self.data_file)
        self.file_patch.start()
        database.ensure_data_file()
        self.agent = TaskAgent()
        self.schedule = database.create_schedule("Проверка", "Week")
        self.schedule_id = self.schedule["id"]
        self.event = database.add_event(self.schedule_id, {
            "title": "Лекция",
            "day_index": 0,
            "start_time_in_minutes": 600,
            "end_time_in_minutes": 660,
        })
        response = self.agent.run({
            "action": "create",
            "schedule_id": self.schedule_id,
            "title": "Лабораторная",
            "deadline": "2026-11-03",
        })
        self.assertTrue(response["success"], response)
        self.task = response["task"]

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_accepts_normalized_action_name(self) -> None:
        result = self.agent.run({"action": " GET ", "schedule_id": self.schedule_id})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["count"], 2)

    def test_rejects_multiple_element_ids_in_get(self) -> None:
        result = self.agent.run({
            "action": "get",
            "schedule_id": self.schedule_id,
            "task_id": self.task["id"],
            "item_id": self.event["id"],
        })
        self.assertFalse(result["success"])
        self.assertIn("только одно поле ID", result["message"])

    def test_rejects_multiple_element_ids_in_update(self) -> None:
        result = self.agent.run({
            "action": "update",
            "schedule_id": self.schedule_id,
            "task_id": self.task["id"],
            "item_id": self.task["id"],
            "title": "Переименовано",
        })
        self.assertFalse(result["success"])
        self.assertIn("только одно поле ID", result["message"])
        stored = database.get_task(self.schedule_id, self.task["id"])
        self.assertEqual(stored["title"], "Лабораторная")

    def test_rejects_fractional_schedule_and_item_ids(self) -> None:
        bad_schedule = self.agent.run({"action": "get", "schedule_id": 1.5})
        bad_item = self.agent.run({
            "action": "get", "schedule_id": self.schedule_id,
            "item_id": 1.5,
        })
        self.assertFalse(bad_schedule["success"])
        self.assertFalse(bad_item["success"])

    def test_rejects_boolean_ids(self) -> None:
        self.assertFalse(self.agent.run({"action": "get", "schedule_id": True})["success"])
        self.assertFalse(self.agent.run({"action": "get", "schedule_id": self.schedule_id, "item_id": False})["success"])

    def test_rejects_unhashable_filter_values_cleanly(self) -> None:
        for key, value in (("priority", ["high"]), ("status", {"status": "pending"})):
            with self.subTest(key=key):
                result = self.agent.run({
                    "action": "get", "schedule_id": self.schedule_id,
                    "item_type": "task", key: value,
                })
                self.assertFalse(result["success"])
                self.assertTrue(result["message"])

    def test_rejects_unhashable_priority_and_status_on_create(self) -> None:
        for field, value in (("priority", ["high"]), ("status", ["pending"])):
            with self.subTest(field=field):
                result = self.agent.run({
                    "action": "create", "schedule_id": self.schedule_id,
                    "title": "Не должна сохраниться", field: value,
                })
                self.assertFalse(result["success"])
        tasks = database.get_all_tasks(self.schedule_id)
        self.assertEqual(len(tasks), 1)

    def test_rejects_misspelled_fields_on_create_and_update(self) -> None:
        create = self.agent.run({
            "action": "create", "schedule_id": self.schedule_id,
            "title": "Тест", "deadlne": "2026-11-03",
        })
        update = self.agent.run({
            "action": "update", "schedule_id": self.schedule_id,
            "task_id": self.task["id"], "deadlne": "2026-11-03",
        })
        self.assertFalse(create["success"])
        self.assertFalse(update["success"])
        self.assertIn("deadlne", create["message"])
        self.assertEqual(database.get_task(self.schedule_id, self.task["id"])["title"], "Лабораторная")

    def test_deadline_must_be_iso_date(self) -> None:
        for deadline in ("tomorrow", "2026-02-30", "2026-1-03", 20261103, ["2026-11-03"]):
            with self.subTest(deadline=deadline):
                result = self.agent.run({
                    "action": "create", "schedule_id": self.schedule_id,
                    "title": "Некорректная дата", "deadline": deadline,
                })
                self.assertFalse(result["success"])
        self.assertEqual(len(database.get_all_tasks(self.schedule_id)), 1)

    def test_empty_deadline_clears_date(self) -> None:
        updated = self.agent.run({
            "action": "update", "schedule_id": self.schedule_id,
            "task_id": self.task["id"], "deadline": "",
        })
        self.assertTrue(updated["success"], updated)
        self.assertIsNone(updated["task"]["deadline"])

    def test_invalid_deadline_update_does_not_mutate_task(self) -> None:
        updated = self.agent.run({
            "action": "update", "schedule_id": self.schedule_id,
            "task_id": self.task["id"], "deadline": "next week",
        })
        self.assertFalse(updated["success"])
        self.assertEqual(database.get_task(self.schedule_id, self.task["id"])["deadline"], "2026-11-03")

    def test_rejects_invalid_item_type_shapes(self) -> None:
        for item_type in (["task"], {"type": "task"}, 1):
            with self.subTest(item_type=item_type):
                result = self.agent.run({"action": "get", "item_type": item_type})
                self.assertFalse(result["success"])
                self.assertIn("item_type", result["message"])

    def test_rejects_unknown_action_and_blank_action(self) -> None:
        for action in (None, "", "unknown", 3):
            with self.subTest(action=action):
                result = self.agent.run({"action": action})
                self.assertFalse(result["success"])

    def test_task_delete_does_not_delete_event_when_ids_are_mismatched(self) -> None:
        result = self.agent.run({
            "action": "delete", "schedule_id": self.schedule_id,
            "task_id": self.event["id"],
        })
        self.assertFalse(result["success"])
        remaining = database.get_schedule_items(self.schedule_id, "all")
        self.assertEqual(len(remaining), 2)
        self.assertTrue(any(item["type"] == "event" and item["id"] == self.event["id"] for item in remaining))

    def test_get_returns_copies_not_mutable_database_records(self) -> None:
        response = self.agent.run({"action": "get", "schedule_id": self.schedule_id})
        response["items"][0]["title"] = "Не сохранять"
        again = database.get_schedule_items(self.schedule_id, "all")
        self.assertNotIn("Не сохранять", {item["title"] for item in again})

    def test_corrupt_json_returns_controlled_error(self) -> None:
        self.data_file.write_text("{ broken", encoding="utf-8")
        result = self.agent.run({"action": "get", "schedule_id": self.schedule_id})
        self.assertFalse(result["success"])
        self.assertIn("Ошибка хранилища данных", result["message"])

    def test_bulk_crud_stress_keeps_ids_unique_and_events_intact(self) -> None:
        created_ids: list[int] = []
        for number in range(80):
            result = self.agent.run({
                "action": "create", "schedule_id": self.schedule_id,
                "title": f"Задача {number}",
                "priority": ("low", "medium", "high")[number % 3],
                "status": ("pending", "in_progress", "completed")[number % 3],
            })
            self.assertTrue(result["success"], result)
            created_ids.append(result["task"]["id"])

        self.assertEqual(len(created_ids), len(set(created_ids)))
        self.assertNotIn(self.event["id"], created_ids)
        all_items = self.agent.run({"action": "get", "schedule_id": self.schedule_id})
        self.assertEqual(all_items["count"], 82)

        for task_id in created_ids[::2]:
            updated = self.agent.run({
                "action": "update", "schedule_id": self.schedule_id,
                "task_id": task_id, "status": "completed",
            })
            self.assertTrue(updated["success"], updated)
        for task_id in created_ids[1::2]:
            deleted = self.agent.run({
                "action": "delete", "schedule_id": self.schedule_id,
                "task_id": task_id,
            })
            self.assertTrue(deleted["success"], deleted)

        final_items = self.agent.run({"action": "get", "schedule_id": self.schedule_id})
        self.assertEqual(final_items["count"], 42)  # 40 survivors + original task + event
        self.assertEqual(sum(item["type"] == "event" for item in final_items["items"]), 1)

    def test_concurrent_creates_do_not_duplicate_ids(self) -> None:
        results: list[dict] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def create_one(number: int) -> None:
            try:
                result = self.agent.run({
                    "action": "create", "schedule_id": self.schedule_id,
                    "title": f"Параллельная задача {number}",
                })
                with result_lock:
                    results.append(result)
            except BaseException as exc:  # pragma: no cover - defensive guard
                with result_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=create_one, args=(i,)) for i in range(30)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 30)
        self.assertTrue(all(result["success"] for result in results))
        ids = [result["task"]["id"] for result in results]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
