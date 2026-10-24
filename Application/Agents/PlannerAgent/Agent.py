from __future__ import annotations
from typing import Any

from Application.Agents.TaskAgent.Agent import TaskAgent

class PlannerAgent:
    """Пока тестовый агент: подтверждает получение данных от оркестратора."""

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file

    def run(self, data: dict[str, Any]) -> dict[str, str]:
        print("[PlannerAgent] Получил данные от оркестратора:")
        print(data)

        return {"agent": "PlannerAgent", "status": "received"}

    def get_answer(self) -> str:
        return "PlannerAgent готов к работе."
