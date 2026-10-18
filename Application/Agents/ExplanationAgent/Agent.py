from __future__ import annotations


class ExplanationAgent:
    def __init__(self: ExplanationAgent, theme_id_file: str) -> None:
        self.theme_id_file = theme_id_file

    def get_answer(self: ExplanationAgent) -> str:
        pass