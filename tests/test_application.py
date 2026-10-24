from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Application.Database import database
from Application.Agents.TaskAgent.Agent import TaskAgent
from Application.Site import app as app_module


class DatabaseAndTaskAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "schedules.json"
        self.file_patch = patch.object(database, "SCHEDULES_FILE", self.data_file)
        self.file_patch.start()
        database.ensure_data_file()
        self.agent = TaskAgent()

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_create_schedule_event_and_task(self) -> None:
        schedule = database.create_schedule("Учёба", "Week")
        schedule_id = schedule["id"]
        event = database.add_event(schedule_id, {
            "title": "Лекция", "start_time_in_minutes": 600,
            "end_time_in_minutes": 660,
        })
        result = self.agent.run({
            "action": "create", "schedule_id": schedule_id,
            "title": "Лабораторная", "priority": "high",
        })

        self.assertTrue(result["success"])
        task = result["task"]
        self.assertEqual(task["type"], "task")
        self.assertEqual(task["id"], event["id"] + 1)
        self.assertEqual(len(database.get_events(schedule_id)), 1)
        self.assertEqual(len(database.get_all_tasks(schedule_id)), 1)
        stored = database.get_schedule(schedule_id)
        self.assertEqual(len(stored["events"]), 2)

    def test_task_crud_and_filters(self) -> None:
        schedule_id = database.create_schedule("Дом", "Day")["id"]
        created = self.agent.run({
            "action": "create", "schedule_id": schedule_id,
            "title": "Уборка", "priority": "high",
        })
        task_id = created["task"]["id"]

        listed = self.agent.run({
            "action": "get", "schedule_id": schedule_id,
            "item_type": "task", "priority": "high",
        })
        self.assertTrue(listed["success"])
        self.assertEqual(listed["count"], 1)

        updated = self.agent.run({
            "action": "update", "schedule_id": schedule_id,
            "task_id": task_id, "status": "completed",
        })
        self.assertTrue(updated["success"])
        self.assertEqual(updated["task"]["status"], "completed")

        fetched = self.agent.run({"action": "get", "schedule_id": schedule_id, "task_id": task_id})
        self.assertTrue(fetched["success"])
        self.assertEqual(fetched["task"]["title"], "Уборка")

        deleted = self.agent.run({"action": "delete", "schedule_id": schedule_id, "task_id": task_id})
        self.assertTrue(deleted["success"])
        self.assertEqual(self.agent.run({
            "action": "get", "schedule_id": schedule_id, "item_type": "task",
        })["count"], 0)

    def test_validation_errors(self) -> None:
        schedule_id = database.create_schedule("Работа")["id"]
        self.assertFalse(self.agent.run({"action": "create", "schedule_id": schedule_id, "title": ""})["success"])
        self.assertFalse(self.agent.run({"action": "create", "schedule_id": schedule_id, "title": "X", "priority": "urgent"})["success"])
        self.assertFalse(self.agent.run({"action": "update", "schedule_id": schedule_id, "task_id": 999, "status": "completed"})["success"])
        self.assertFalse(self.agent.run({"action": "explode"})["success"])
        self.assertFalse(self.agent.run(None)["success"])

    def test_event_indexes_ignore_tasks(self) -> None:
        schedule_id = database.create_schedule("Учёба", "Week")["id"]
        task_result = self.agent.run({
            "action": "create", "schedule_id": schedule_id, "title": "Задача без времени",
        })
        self.assertTrue(task_result["success"])
        database.add_event(schedule_id, {
            "title": "Лекция", "start_time_in_minutes": 600, "end_time_in_minutes": 660,
        })

        events = database.get_events(schedule_id)
        self.assertEqual(len(events), 1)
        updated = database.update_event(schedule_id, 0, {"title": "Новая лекция"})
        self.assertEqual(updated["title"], "Новая лекция")
        database.delete_event(schedule_id, 0)

        schedule = database.get_schedule(schedule_id)
        self.assertEqual(len(schedule["events"]), 1)
        self.assertEqual(schedule["events"][0]["type"], "task")

    def test_events_cannot_cross_midnight_or_exceed_schedule(self) -> None:
        schedule_id = database.create_schedule("День", "Day")["id"]
        with self.assertRaises(ValueError):
            database.add_event(schedule_id, {
                "title": "Длинное событие",
                "start_time_in_minutes": 1380,
                "end_time_in_minutes": 1500,
            })
        with self.assertRaises(ValueError):
            database.add_event(schedule_id, {
                "title": "За границей дня",
                "start_time_in_minutes": 1440,
                "end_time_in_minutes": 1500,
            })

    def test_legacy_task_gets_missing_fields(self) -> None:
        self.data_file.write_text(json.dumps([{
            "id": 1, "name": "Старое расписание", "period": "Week",
            "events": [{"id": 1, "type": "task", "title": "Старая задача"}],
        }]), encoding="utf-8")
        tasks = database.get_all_tasks(1)
        self.assertEqual(tasks[0]["priority"], "medium")
        self.assertEqual(tasks[0]["status"], "pending")
        self.assertIn("deadline", tasks[0])


class FlaskEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "schedules.json"
        self.file_patch = patch.object(database, "SCHEDULES_FILE", self.data_file)
        self.file_patch.start()
        database.ensure_data_file()
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_health_and_homepage(self) -> None:
        health = self.client.get("/health")
        page = self.client.get("/")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["status"], "ok")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Секретарь".encode(), page.data)

    def test_schedule_and_task_api(self) -> None:
        created_schedule = self.client.post("/api/schedules", json={"name": "Учёба", "period": "Week"})
        self.assertEqual(created_schedule.status_code, 201)
        schedule_id = created_schedule.get_json()["id"]

        created_task = self.client.post("/api/task-agent", json={
            "action": "create", "schedule_id": schedule_id, "title": "Тест API",
        })
        self.assertEqual(created_task.status_code, 200)
        task_id = created_task.get_json()["task"]["id"]
        database.add_event(schedule_id, {
            "title": "Лекция API",
            "start_time_in_minutes": 600,
            "end_time_in_minutes": 660,
        })

        # TaskAgent.get по умолчанию возвращает и задачи, и события.
        all_items = self.client.post("/api/task-agent", json={
            "action": "get", "schedule_id": schedule_id,
        })
        self.assertEqual(all_items.status_code, 200)
        self.assertEqual(all_items.get_json()["count"], 2)
        self.assertEqual(
            {item["type"] for item in all_items.get_json()["items"]},
            {"task", "event"},
        )

        only_events = self.client.post("/api/task-agent", json={
            "action": "get", "schedule_id": schedule_id, "item_type": "event",
        })
        self.assertEqual(only_events.status_code, 200)
        self.assertEqual(only_events.get_json()["count"], 1)
        self.assertEqual(only_events.get_json()["items"][0]["title"], "Лекция API")

        listed = self.client.get(f"/api/tasks?schedule_id={schedule_id}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.get_json()), 1)

        listed_events = self.client.get(f"/api/schedules/{schedule_id}/events")
        self.assertEqual(listed_events.status_code, 200)
        self.assertEqual(len(listed_events.get_json()), 1)
        self.assertEqual(listed_events.get_json()[0]["title"], "Лекция API")

        updated = self.client.post("/api/task-agent", json={
            "action": "update", "schedule_id": schedule_id,
            "task_id": task_id, "status": "completed",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["task"]["status"], "completed")

        deleted = self.client.post("/api/task-agent", json={
            "action": "delete", "schedule_id": schedule_id, "task_id": task_id,
        })
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/tasks?schedule_id={schedule_id}").get_json(), [])

    def test_invalid_json_shapes_return_400(self) -> None:
        self.assertEqual(self.client.post("/api/schedules", json=["not", "object"]).status_code, 400)
        self.assertEqual(self.client.post("/api/command", json=["not", "object"]).status_code, 400)
        self.assertEqual(self.client.post("/api/task-agent", json=["not", "object"]).status_code, 400)

    def test_static_css_is_served(self) -> None:
        response = self.client.get("/static/styles.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b".sidebar", response.data)
        response.close()

    def test_manual_event_form_and_schedule_delete(self) -> None:
        response = self.client.post("/api/schedules", json={"name": "Работа", "period": "Day"})
        schedule_id = response.get_json()["id"]

        added = self.client.post("/events", data={
            "schedule_id": str(schedule_id), "title": "Встреча",
            "description": "Команда", "day_index": "0",
            "start_time": "10:00", "end_time": "11:00",
        })
        self.assertEqual(added.status_code, 302)
        events = database.get_events(schedule_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "event")

        deleted = self.client.post(f"/schedules/{schedule_id}/delete")
        self.assertEqual(deleted.status_code, 302)
        self.assertEqual(database.load_schedules(), [])

    def test_free_text_command_is_not_reported_as_success(self) -> None:
        schedule_id = database.create_schedule("Учёба")["id"]
        response = self.client.post("/api/command", json={
            "schedule_id": schedule_id, "command": "добавь домашнее задание",
        })
        self.assertEqual(response.status_code, 501)
        self.assertFalse(response.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
