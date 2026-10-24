from __future__ import annotations
from typing import Any


class ConflictAgent:
    """Пока тестовый агент: подтверждает получение данных от оркестратора."""

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file

    def run(self, data: dict[str, Any]) -> dict[str, str]:
        print("[ConflictAgent] Получил данные от оркестратора:")
        print(data)
        return {"agent": "ConflictAgent", "status": "received"}

    def get_answer(self) -> str:
        return "ConflictAgent готов к работе."
