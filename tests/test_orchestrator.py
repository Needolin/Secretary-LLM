from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Application.Agents.OrchestratorAgent.Agent import CommandIntent, OrchestratorAgent
from Application.Database import database


class FakeParser:
    def __init__(self, result: CommandIntent) -> None:
        self.result = result

    def invoke(self, messages):
        return self.result


class OrchestratorGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "schedules.json"
        self.file_patch = patch.object(database, "SCHEDULES_FILE", self.data_file)
        self.file_patch.start()
        database.ensure_data_file()
        self.schedule_id = database.create_schedule("Учёба", "Week")["id"]

    def tearDown(self) -> None:
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def make_orchestrator(self, intent: CommandIntent) -> OrchestratorAgent:
        orchestrator = OrchestratorAgent()
        orchestrator._intent_parser = FakeParser(intent)
        return orchestrator

    def test_create_task_routes_to_task_agent(self) -> None:
        orchestrator = self.make_orchestrator(CommandIntent(
            intent="create_task",
            title="Подготовить лабораторную",
            description="Сделать отчёт",
            deadline="2026-11-15",
            priority="high",
        ))

        result = orchestrator.run({
            "command": "Добавь подготовку к лабораторной",
            "schedule_id": self.schedule_id,
        })

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["agents_called"], ["TaskAgent"])
        tasks = database.get_all_tasks(self.schedule_id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "Подготовить лабораторную")
        self.assertEqual(tasks[0]["priority"], "high")

    def test_plan_day_dispatches_to_all_agents_in_order(self) -> None:
        database.create_task(self.schedule_id, {"title": "Лабораторная", "priority": "high"})
        orchestrator = self.make_orchestrator(CommandIntent(intent="plan_day"))

        result = orchestrator.run({
            "command": "Распланируй мой день",
            "schedule_id": self.schedule_id,
            "schedule": {"id": self.schedule_id, "name": "Учёба", "period": "Week"},
        })

        self.assertEqual(result["status"], "not_implemented")
        self.assertEqual(
            result["agents_called"],
            ["TaskAgent", "PlannerAgent", "ConflictAgent", "ExplanationAgent"],
        )
        self.assertIn("пока являются заглушками", result["message"])

    def test_delete_requires_confirmation(self) -> None:
        task = database.create_task(self.schedule_id, {"title": "Удалить меня"})
        orchestrator = self.make_orchestrator(CommandIntent(
            intent="delete_task",
            task_id=task["id"],
        ))

        result = orchestrator.run({
            "command": f"Удали задачу {task['id']}",
            "schedule_id": self.schedule_id,
        })

        self.assertEqual(result["status"], "confirmation_required")
        self.assertEqual(database.get_all_tasks(self.schedule_id)[0]["id"], task["id"])
        self.assertEqual(result["agents_called"], [])

    def test_duration_is_not_silently_dropped(self) -> None:
        orchestrator = self.make_orchestrator(CommandIntent(
            intent="create_task",
            title="Подготовка к экзамену",
            estimated_duration_minutes=120,
        ))

        result = orchestrator.run({
            "command": "Добавь подготовку к экзамену на два часа",
            "schedule_id": self.schedule_id,
        })

        self.assertEqual(result["status"], "not_implemented")
        self.assertEqual(database.get_all_tasks(self.schedule_id), [])
        self.assertIn("не сохраняет оценочную длительность", result["message"])


if __name__ == "__main__":
    unittest.main()
