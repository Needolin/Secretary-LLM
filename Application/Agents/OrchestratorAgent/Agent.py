from __future__ import annotations

from typing import Any

from Application.Agents.ConflictAgent.Agent import ConflictAgent
from Application.Agents.ExplanationAgent.Agent import ExplanationAgent
from Application.Agents.PlannerAgent.Agent import PlannerAgent
from Application.Agents.TaskAgent.Agent import TaskAgent


class OrchestratorAgent:
    """Точка входа для команд сайта; интерпретация свободного текста пока не реализована."""

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file
        self.conflict_agent = ConflictAgent(theme_id_file=theme_id_file)
        self.explanation_agent = ExplanationAgent(theme_id_file=theme_id_file)
        self.planner_agent = PlannerAgent(theme_id_file=theme_id_file)
        self.task_agent = TaskAgent(theme_id_file=theme_id_file)

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"status": "error", "message": "Команда должна быть объектом."}

        command = str(data.get("command", "")).strip()
        if not command:
            return {"status": "error", "message": "Введите команду."}

        result = self.task_agent.run({
            "action": "list",
            "schedule_id": data.get("schedule_id"),
            "item_type": "all",
        })

        print(result["items"])
        # Не вызываем TaskAgent с выдуманным action: свободный текст ещё не преобразуется
        # в структурированные действия. Для проверки CRUD есть /api/task-agent.
        return {
            "status": "not_implemented",
            "message": (
                "Команда получена, но обработка естественного языка пока не подключена. "
                "Для проверки задач используйте API /api/task-agent."
            ),
        }

    def get_answer(self) -> str:
        return "Оркестратор готов; обработчик естественного языка ещё не подключён."
