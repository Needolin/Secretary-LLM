from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Application.Agents.OrchestratorAgent.Agent import OrchestratorAgent
from Application.Agents.TaskAgent.Agent import TaskAgent
from Application.Database import database


class TaskAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "schedules.json"
        self.file_patch = patch.object(database, "SCHEDULES_FILE", self.data_file)
        self.file_patch.start()
        database.ensure_data_file()
        self.agent = TaskAgent()
        self.schedule = database.create_schedule("Тест", "Week")
        self.schedule_id = self.schedule["id"]
        self.event = database.add_event(self.schedule_id, {
            "title": "Лекция",
            "start_time_in_minutes": 600,
            "end_time_in_minutes": 660,
        })
        created = self.agent.run({
            "action": "create",
            "schedule_id": self.schedule_id,
            "title": "Сдать лабораторную",
            "priority": "high",
        })
        self.assertTrue(created["success"], created)
        self.task = created["task"]

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_get_defaults_to_all_items(self) -> None:
        result = self.agent.run({"action": "get", "schedule_id": self.schedule_id})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["count"], 2)
        self.assertEqual({item["type"] for item in result["items"]}, {"task", "event"})
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(len(result["events"]), 1)
        self.assertTrue(all(item["schedule_id"] == self.schedule_id for item in result["items"]))

    def test_get_filters_by_type(self) -> None:
        tasks = self.agent.run({"action": "get", "schedule_id": self.schedule_id, "item_type": "task"})
        events = self.agent.run({"action": "get", "schedule_id": self.schedule_id, "item_type": "event"})
        self.assertEqual([item["id"] for item in tasks["items"]], [self.task["id"]])
        self.assertEqual([item["id"] for item in events["items"]], [self.event["id"]])

    def test_get_single_item_by_specific_or_generic_id(self) -> None:
        task = self.agent.run({"action": "get", "schedule_id": self.schedule_id, "task_id": self.task["id"]})
        event = self.agent.run({"action": "get", "schedule_id": self.schedule_id, "event_id": self.event["id"]})
        generic = self.agent.run({"action": "get", "schedule_id": self.schedule_id, "item_id": self.event["id"]})
        self.assertTrue(task["success"], task)
        self.assertTrue(event["success"], event)
        self.assertTrue(generic["success"], generic)
        self.assertEqual(task["item"]["type"], "task")
        self.assertEqual(event["item"]["type"], "event")
        self.assertEqual(generic["item"]["title"], "Лекция")

    def test_unknown_action_returns_clear_error(self) -> None:
        result = self.agent.run({"action": "unknown", "schedule_id": self.schedule_id})
        self.assertFalse(result["success"])
        self.assertIn("get, create, update или delete", result["message"])

    def test_get_can_return_only_tasks(self) -> None:
        result = self.agent.run({
            "action": "get", "schedule_id": self.schedule_id, "item_type": "task",
        })
        self.assertTrue(result["success"], result)
        self.assertEqual(result["item_type"], "task")
        self.assertEqual(result["count"], 1)

    def test_get_all_schedules_without_schedule_id(self) -> None:
        second_schedule = database.create_schedule("Другое", "Day")
        database.add_event(second_schedule["id"], {
            "title": "Встреча", "start_time_in_minutes": 30, "end_time_in_minutes": 60,
        })
        result = self.agent.run({"action": "get"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["count"], 3)
        self.assertEqual({item["schedule_id"] for item in result["items"]}, {self.schedule_id, second_schedule["id"]})

    def test_filters_apply_to_tasks(self) -> None:
        result = self.agent.run({
            "action": "get", "schedule_id": self.schedule_id,
            "item_type": "task", "priority": "high", "status": "pending",
        })
        self.assertEqual(result["count"], 1)
        no_match = self.agent.run({
            "action": "get", "schedule_id": self.schedule_id,
            "item_type": "task", "priority": "low",
        })
        self.assertEqual(no_match["count"], 0)
        invalid = self.agent.run({
            "action": "get", "schedule_id": self.schedule_id,
            "item_type": "task", "priority": "urgent",
        })
        self.assertFalse(invalid["success"])

    def test_task_update_and_delete_do_not_touch_events(self) -> None:
        updated = self.agent.run({
            "action": "update", "schedule_id": self.schedule_id,
            "task_id": self.task["id"], "status": "completed",
        })
        self.assertTrue(updated["success"], updated)
        self.assertEqual(updated["task"]["status"], "completed")
        deleted = self.agent.run({
            "action": "delete", "schedule_id": self.schedule_id,
            "task_id": self.task["id"],
        })
        self.assertTrue(deleted["success"], deleted)
        remaining = database.get_schedule_items(self.schedule_id, "all")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["type"], "event")

    def test_invalid_inputs_return_clear_errors(self) -> None:
        self.assertFalse(self.agent.run(None)["success"])
        self.assertFalse(self.agent.run({"action": "get", "item_id": 1})["success"])
        self.assertFalse(self.agent.run({"action": "get", "schedule_id": self.schedule_id, "item_type": "unknown"})["success"])
        self.assertFalse(self.agent.run({"action": "delete", "schedule_id": self.schedule_id, "task_id": 987})["success"])

    def test_orchestrator_routes_structured_task_agent_request(self) -> None:
        orchestrator = OrchestratorAgent()
        result = orchestrator.run({
            "action": "get", "schedule_id": self.schedule_id, "item_type": "all",
        })
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)

    def test_orchestrator_reports_free_text_as_not_implemented(self) -> None:
        result = OrchestratorAgent().run({"command": "Покажи расписание"})
        self.assertEqual(result["status"], "not_implemented")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
