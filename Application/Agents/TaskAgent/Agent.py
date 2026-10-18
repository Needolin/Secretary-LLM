from __future__ import annotations


class TaskAgent:
    def __init__(self: TaskAgent, theme_id_file: str) -> None:
        self.theme_id_file = theme_id_file

    def get_answer(self: TaskAgent) -> str:
        pass