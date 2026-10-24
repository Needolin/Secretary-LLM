from __future__ import annotations

from typing import Any

from Application.Agents.ConflictAgent.Agent import ConflictAgent
from Application.Agents.ExplanationAgent.Agent import ExplanationAgent
from Application.Agents.PlannerAgent.Agent import PlannerAgent
from Application.Agents.TaskAgent.Agent import TaskAgent


class OrchestratorAgent:
    """Маршрутизирует структурированные действия к агентам.

    Запросы с полем ``action`` передаются TaskAgent. Обработка свободного
    текста в поле ``command`` пока не реализована и честно возвращает
    ``not_implemented``.
    """

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file
        self.conflict_agent = ConflictAgent(theme_id_file=theme_id_file)
        self.explanation_agent = ExplanationAgent(theme_id_file=theme_id_file)
        self.planner_agent = PlannerAgent(theme_id_file=theme_id_file)
        self.task_agent = TaskAgent(theme_id_file=theme_id_file)

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"status": "error", "success": False, "message": "Запрос должен быть JSON-объектом."}

        # Структурированные команды можно вызывать прямо через оркестратор.
        # Например: run({"action": "get", "schedule_id": 1, "item_type": "all"}).
        """
        if data.get("action") is not None:
            result = self.task_agent.run(data)
            return {
                **result,
                "status": "ok" if result.get("success") else "error",
            }"""

        #result = self.task_agent.run({"action": "get", "schedule_id": 1})
        #print(result["items"])

        command = str(data.get("command", "")).strip()
        if not command:
            return {
                "status": "error",
                "success": False,
                "message": "Введите команду или передайте структурированное действие в поле action.",
            }

        return {
            "status": "not_implemented",
            "success": False,
            "message": (
                "Команда получена, но обработка естественного языка пока не подключена. "
                "Для тестирования передайте структурированное действие, например action='get'."
            ),
        }
