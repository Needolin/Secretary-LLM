from __future__ import annotations

from Application.Agents.ConflictAgent.Agent import ConflictAgent
from Application.Agents.ExplanationAgent.Agent import ExplanationAgent
from Application.Agents.PlannerAgent.Agent import PlannerAgent
from Application.Agents.TaskAgent.Agent import TaskAgent


class OrchestratorAgent:
    def __init__(self: OrchestratorAgent, theme_id_file: str) -> None:
        self.theme_id_file = theme_id_file

        self.conflict_agent = ConflictAgent(theme_id_file=theme_id_file)
        self.explanation_agent = ExplanationAgent(theme_id_file=theme_id_file)
        self.planner_agent = PlannerAgent(theme_id_file=theme_id_file)
        self.task_agent = TaskAgent(theme_id_file=theme_id_file)

    def get_answer(self: OrchestratorAgent) -> str:
        pass