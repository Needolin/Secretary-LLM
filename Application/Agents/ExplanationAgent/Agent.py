from __future__ import annotations
from typing import Any


class ExplanationAgent:
    """Пока тестовый агент: подтверждает получение данных от оркестратора."""

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file

    def run(self, data: dict[str, Any]) -> dict[str, str]:
        print("[ExplanationAgent] Получил данные от оркестратора:")
        print(data)
        return {"agent": "ExplanationAgent", "status": "received"}

    def get_answer(self) -> str:
        return "ExplanationAgent готов к работе."
