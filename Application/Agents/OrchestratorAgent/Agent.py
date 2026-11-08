from __future__ import annotations

import operator
import os
from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from Application.Agents.ConflictAgent.Agent import ConflictAgent
from Application.Agents.ExplanationAgent.Agent import ExplanationAgent
from Application.Agents.PlannerAgent.Agent import PlannerAgent
from Application.Agents.TaskAgent.Agent import TaskAgent


class CommandIntent(BaseModel):
    """Структурированное намерение, которое LLM извлекает из текста."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "create_task",
        "list_tasks",
        "list_schedule",
        "get_task",
        "update_task",
        "delete_task",
        "plan_day",
        "check_load",
        "clarify",
        "unknown",
    ] = Field(description="Главное намерение пользователя.")
    title: str | None = Field(default=None, description="Название задачи.")
    description: str | None = Field(default=None, description="Описание задачи.")
    deadline: str | None = Field(
        default=None,
        description="Дедлайн в формате YYYY-MM-DD. Относительные даты вычисляй от текущей даты из контекста.",
    )
    priority: Literal["low", "medium", "high"] | None = Field(
        default=None, description="Приоритет задачи: low, medium или high."
    )
    status: Literal["pending", "in_progress", "completed"] | None = Field(
        default=None, description="Статус задачи."
    )
    task_id: int | None = Field(
        default=None, ge=1,
        description="ID существующей задачи. Заполняй только если пользователь явно назвал ID.",
    )
    item_type: Literal["task", "event", "all"] | None = Field(
        default=None, description="Какие элементы показать при чтении расписания."
    )
    day_index: int | None = Field(
        default=None, ge=0, le=27,
        description="Индекс дня в периоде расписания, если пользователь явно указал день.",
    )
    start_time_in_minutes: int | None = Field(
        default=None, ge=0,
        description="Начало элемента в минутах от начала периода расписания.",
    )
    end_time_in_minutes: int | None = Field(
        default=None, ge=0,
        description="Конец элемента в минутах от начала периода расписания.",
    )
    estimated_duration_minutes: int | None = Field(
        default=None, ge=1,
        description="Оценочная длительность задачи в минутах. Сейчас TaskAgent ещё не сохраняет это поле.",
    )
    clarification_question: str | None = Field(
        default=None,
        description="Один короткий вопрос, если без уточнения нельзя безопасно выполнить запрос.",
    )


class OrchestratorState(TypedDict, total=False):
    command: str
    schedule_id: int | None
    schedule_meta: dict[str, Any]
    chat_history: list[dict[str, Any]]
    confirmed: bool
    intent: CommandIntent
    task_result: dict[str, Any]
    planner_result: dict[str, Any]
    conflict_result: dict[str, Any]
    explanation_result: dict[str, Any]
    agent_results: Annotated[list[dict[str, Any]], operator.add]
    status: str
    message: str


SYSTEM_PROMPT = """Ты — модуль распознавания команд ИИ-секретаря.
Твоя задача — не отвечать пользователю и не выполнять действия, а вернуть
строго структурированное намерение по заданной схеме.

Доступные намерения:
- create_task: создать новый дедлайн;
- list_tasks: показать дедлайны;
- list_schedule: показать события и дедлайны расписания;
- get_task: получить один дедлайн по явно указанному ID;
- update_task: изменить дедлайн по явно указанному ID;
- delete_task: удалить дедлайн по явно указанному ID;
- plan_day: составить план дня;
- check_load: проверить загруженность расписания;
- clarify: запрос неоднозначен или не хватает обязательных данных;
- unknown: запрос не относится к возможностям секретаря.

Правила:
1. Не выдумывай task_id, schedule_id, даты, время и другие параметры.
2. schedule_id выбирается приложением и не извлекается из текста.
3. Заполняй только параметры, которые прямо следуют из сообщения.
4. Относительные даты (например, «завтра») переводи в YYYY-MM-DD,
   используя текущую дату и часовой пояс из контекста.
5. При создании дедлайна обязательно извлеки title. Если его нельзя понять,
   выбери clarify и задай один короткий вопрос.
6. Для изменения или удаления существующей задачи нужен явно указанный task_id.
   Если его нет, выбери clarify; не угадывай задачу по похожему названию.
7. Если пользователь просит продолжительность (например, «на 2 часа»),
   заполни estimated_duration_minutes. Не выдавай её за уже сохранённое время.
8. Для «распланируй день» выбери plan_day, для «проверь загруженность» — check_load.
9. Для «покажи задачи» выбери list_tasks, для «покажи расписание» — list_schedule.
10. Не добавляй свободный текст, внутренние рассуждения или поля вне схемы.
"""


class OrchestratorAgent:
    """Распознаёт намерение и управляет последовательностью специализированных агентов.

    Структурированные запросы с `action` по-прежнему напрямую передаются TaskAgent.
    Для свободного текста используется LangGraph + Cloud.ru Foundation Models.
    """

    TASK_INTENTS = {"create_task", "list_tasks", "list_schedule", "get_task", "update_task", "delete_task"}
    PLANNING_INTENTS = {"plan_day", "check_load"}

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file
        self.conflict_agent = ConflictAgent(theme_id_file=theme_id_file)
        self.explanation_agent = ExplanationAgent(theme_id_file=theme_id_file)
        self.planner_agent = PlannerAgent(theme_id_file=theme_id_file)
        self.task_agent = TaskAgent(theme_id_file=theme_id_file)
        self._intent_parser = None
        self._graph = None

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        """Обрабатывает структурированную команду или естественный язык."""
        if not isinstance(data, dict):
            return self._result("error", "Запрос должен быть JSON-объектом.")

        # Старый контракт оставляем без изменений: готовые действия идут в TaskAgent.
        if data.get("action") is not None:
            result = self.task_agent.run(data)
            return {
                **result,
                "status": "ok" if result.get("success") else "error",
                "agents_called": ["TaskAgent"],
            }

        command = data.get("command")
        if not isinstance(command, str) or not command.strip():
            return self._result(
                "error",
                "Введите команду или передайте структурированное действие в поле action.",
            )
        if len(command) > 2000:
            return self._result("error", "Команда слишком длинная: максимум 2000 символов.")

        schedule_id = self._parse_schedule_id(data.get("schedule_id"))
        if data.get("schedule_id") is not None and schedule_id is None:
            return self._result("error", "schedule_id должен быть положительным целым числом.")

        try:
            graph = self._get_graph()
            output = graph.invoke({
                "command": command.strip(),
                "schedule_id": schedule_id,
                "schedule_meta": self._schedule_meta(data.get("schedule")),
                "chat_history": self._normalise_chat_history(data.get("chat_history")),
                "confirmed": data.get("confirmed") is True,
                "agent_results": [],
            })
        except RuntimeError as exc:
            # Например, не задан CLOUD_API_KEY или не установлены зависимости.
            return self._result("configuration_error", str(exc))
        except Exception as exc:
            print(f"[OrchestratorAgent] {type(exc).__name__}: {exc}")
            return self._result(
                "error",
                "Не удалось обработать команду через LLM. Проверьте API-ключ, модель и подключение; подробности есть в консоли.",
            )

        parsed = output.get("intent")
        return {
            "status": output.get("status", "error"),
            "success": output.get("status") == "ok",
            "message": output.get("message", "Команда обработана."),
            "intent": parsed.model_dump(exclude_none=True) if parsed else None,
            "agent_results": output.get("agent_results", []),
            "agents_called": [
                item["agent"] for item in output.get("agent_results", [])
                if item.get("agent")
            ],
            "task_result": output.get("task_result"),
            "planner_result": output.get("planner_result"),
            "conflict_result": output.get("conflict_result"),
            "explanation_result": output.get("explanation_result"),
        }

    def _get_intent_parser(self):
        if self._intent_parser is None:
            try:
                from .llm_config import create_chat_model
            except ImportError as exc:
                raise RuntimeError(
                    "Не установлены зависимости LLM. Выполните: python -m pip install -r requirements.txt"
                ) from exc

            llm = create_chat_model()
            self._intent_parser = llm.with_structured_output(
                CommandIntent,
                method="function_calling",
            )
        return self._intent_parser

    def _get_graph(self):
        if self._graph is not None:
            return self._graph

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError(
                "Не установлены LangGraph/LangChain. Выполните: python -m pip install -r requirements.txt"
            ) from exc

        def recognize(state: OrchestratorState) -> dict[str, Any]:
            now = self._local_now()
            schedule_meta = state.get("schedule_meta", {})
            context = (
                f"Текущая дата и время: {now.isoformat()}\n"
                f"Часовой пояс: {now.tzinfo}\n"
                f"Выбранное расписание: {schedule_meta or 'не передано'}\n"
                f"ID выбранного расписания (может отсутствовать): {state.get('schedule_id')}"
            )
            history = state.get("chat_history", [])[-8:]
            history_text = "\n".join(
                f"{item.get('role', 'unknown')}: {item.get('content', '')}"
                for item in history
            ) or "История диалога отсутствует."
            parser = self._get_intent_parser()
            parsed = parser.invoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=(
                    f"Контекст:\n{context}\n\n"
                    f"Предыдущие сообщения (только контекст, не выполняй их повторно):\n"
                    f"{history_text}\n\n"
                    f"Новая команда пользователя:\n{state['command']}"
                )),
            ])
            if not isinstance(parsed, CommandIntent):
                parsed = CommandIntent.model_validate(parsed)
            return {
                "intent": parsed,
                "agent_results": [{"step": "intent_recognition", "intent": parsed.intent}],
            }

        def route_after_recognition(state: OrchestratorState) -> str:
            intent = state["intent"].intent
            if intent in self.TASK_INTENTS:
                return "task_operation"
            if intent in self.PLANNING_INTENTS:
                return "get_schedule_items"
            return "finish_without_agent"

        def route_after_get(state: OrchestratorState) -> str:
            result = state.get("task_result", {})
            return "planner" if result.get("success") else "finish_without_agent"

        builder = StateGraph(OrchestratorState)
        builder.add_node("recognize", recognize)
        builder.add_node("task_operation", self._run_task_operation)
        builder.add_node("get_schedule_items", self._get_schedule_items)
        builder.add_node("planner", self._run_planner)
        builder.add_node("conflict", self._run_conflict)
        builder.add_node("explanation", self._run_explanation)
        builder.add_node("finish_without_agent", self._finish_without_agent)
        builder.add_node("finish_planning", self._finish_planning)

        builder.add_edge(START, "recognize")
        builder.add_conditional_edges(
            "recognize",
            route_after_recognition,
            {
                "task_operation": "task_operation",
                "get_schedule_items": "get_schedule_items",
                "finish_without_agent": "finish_without_agent",
            },
        )
        builder.add_edge("task_operation", END)
        builder.add_conditional_edges(
            "get_schedule_items",
            route_after_get,
            {"planner": "planner", "finish_without_agent": "finish_without_agent"},
        )
        builder.add_edge("planner", "conflict")
        builder.add_edge("conflict", "explanation")
        builder.add_edge("explanation", "finish_planning")
        builder.add_edge("finish_without_agent", END)
        builder.add_edge("finish_planning", END)

        self._graph = builder.compile()
        return self._graph

    def _run_task_operation(self, state: OrchestratorState) -> dict[str, Any]:
        intent = state["intent"]
        schedule_id = state.get("schedule_id")

        if intent.estimated_duration_minutes is not None:
            return {
                "status": "not_implemented",
                "message": (
                    f"Я распознал длительность {intent.estimated_duration_minutes} минут, "
                    "но текущая версия TaskAgent пока не сохраняет оценочную длительность. "
                    "Задачу не создавал, чтобы не потерять этот параметр."
                ),
            }

        action_by_intent = {
            "create_task": "create",
            "list_tasks": "get",
            "list_schedule": "get",
            "get_task": "get",
            "update_task": "update",
            "delete_task": "delete",
        }
        action = action_by_intent[intent.intent]
        payload: dict[str, Any] = {"action": action}
        if schedule_id is not None:
            payload["schedule_id"] = schedule_id

        if intent.intent == "create_task":
            if not intent.title or not intent.title.strip():
                return {
                    "status": "clarification_required",
                    "message": "Как назвать задачу?",
                }
            for field in (
                "title", "description", "deadline", "priority", "status",
                "day_index", "start_time_in_minutes", "end_time_in_minutes",
            ):
                value = getattr(intent, field)
                if value is not None:
                    payload[field] = value

        elif intent.intent in {"list_tasks", "list_schedule"}:
            payload["item_type"] = "task" if intent.intent == "list_tasks" else "all"

        elif intent.intent in {"get_task", "update_task", "delete_task"}:
            if intent.task_id is None:
                return {
                    "status": "clarification_required",
                    "message": "Укажите ID задачи. Я не буду угадывать, какую именно задачу нужно изменить.",
                }
            payload["task_id"] = intent.task_id
            if intent.intent == "update_task":
                for field in (
                    "title", "description", "deadline", "priority", "status",
                    "day_index", "start_time_in_minutes", "end_time_in_minutes",
                ):
                    value = getattr(intent, field)
                    if value is not None:
                        payload[field] = value
            if intent.intent == "delete_task" and not state.get("confirmed", False):
                return {
                    "status": "confirmation_required",
                    "message": (
                        f"Подтвердите удаление задачи с ID {intent.task_id}. "
                        "До отдельного подтверждения задача не удалена."
                    ),
                }

        result = self.task_agent.run(payload)
        trace = {
            "agent": "TaskAgent",
            "action": action,
            "status": "ok" if result.get("success") else "error",
        }
        if not result.get("success"):
            return {
                "task_result": result,
                "agent_results": [trace],
                "status": "error",
                "message": result.get("message", "TaskAgent не смог выполнить запрос."),
            }

        if intent.intent == "list_tasks":
            count = len(result.get("tasks", []))
            message = f"Нашёл {count} {self._plural_ru(count, ('дедлайн', 'дедлайна', 'дедлайнов'))}."
        elif intent.intent == "list_schedule":
            deadline_count = len(result.get("tasks", []))
            event_count = len(result.get("events", []))
            message = (
                f"Нашёл {event_count} {self._plural_ru(event_count, ('событие', 'события', 'событий'))} "
                f"и {deadline_count} {self._plural_ru(deadline_count, ('дедлайн', 'дедлайна', 'дедлайнов'))}."
            )
        else:
            message = result.get("message", "Готово.")

        return {
            "task_result": result,
            "agent_results": [trace],
            "status": "ok",
            "message": message,
        }

    def _get_schedule_items(self, state: OrchestratorState) -> dict[str, Any]:
        schedule_id = state.get("schedule_id")
        if schedule_id is None:
            return {
                "task_result": {"success": False, "message": "Для планирования выберите расписание."},
                "status": "clarification_required",
                "message": "Сначала выберите расписание, для которого нужно построить план.",
            }

        result = self.task_agent.run({
            "action": "get",
            "schedule_id": schedule_id,
            "item_type": "all",
        })
        return {
            "task_result": result,
            "agent_results": [{
                "agent": "TaskAgent",
                "action": "get",
                "status": "ok" if result.get("success") else "error",
            }],
        }

    def _run_planner(self, state: OrchestratorState) -> dict[str, Any]:
        context = self._planning_context(state)
        result = self.planner_agent.run(context)
        return {
            "planner_result": result,
            "agent_results": [{"agent": "PlannerAgent", "action": "plan", "status": result.get("status", "unknown")}],
        }

    def _run_conflict(self, state: OrchestratorState) -> dict[str, Any]:
        context = {
            **self._planning_context(state),
            "planner_result": state.get("planner_result", {}),
        }
        result = self.conflict_agent.run(context)
        return {
            "conflict_result": result,
            "agent_results": [{"agent": "ConflictAgent", "action": "check", "status": result.get("status", "unknown")}],
        }

    def _run_explanation(self, state: OrchestratorState) -> dict[str, Any]:
        context = {
            **self._planning_context(state),
            "planner_result": state.get("planner_result", {}),
            "conflict_result": state.get("conflict_result", {}),
        }
        result = self.explanation_agent.run(context)
        return {
            "explanation_result": result,
            "agent_results": [{"agent": "ExplanationAgent", "action": "explain", "status": result.get("status", "unknown")}],
        }

    def _finish_without_agent(self, state: OrchestratorState) -> dict[str, Any]:
        intent = state.get("intent")
        if intent is None:
            return {"status": "error", "message": "Не удалось определить намерение."}

        if intent.intent == "clarify":
            return {
                "status": "clarification_required",
                "message": intent.clarification_question or "Уточните, пожалуйста, что именно нужно сделать.",
            }
        if intent.intent == "unknown":
            return {
                "status": "not_implemented",
                "message": (
                    "Пока я умею работать с задачами, показывать расписание, "
                    "запускать планирование дня и проверять загруженность."
                ),
            }

        task_result = state.get("task_result", {})
        return {
            "status": state.get("status", "error"),
            "message": state.get("message") or task_result.get("message") or "Не удалось получить данные.",
        }

    def _finish_planning(self, state: OrchestratorState) -> dict[str, Any]:
        intent = state["intent"]
        name = "планирование дня" if intent.intent == "plan_day" else "проверку загруженности"
        return {
            "status": "not_implemented",
            "message": (
                f"Запрос на {name} прошёл по цепочке TaskAgent → PlannerAgent → "
                "ConflictAgent → ExplanationAgent. Маршрутизация работает, "
                "но PlannerAgent, ConflictAgent и ExplanationAgent пока являются "
                "заглушками: реальное расписание ещё не рассчитано и не изменено."
            ),
        }

    def _planning_context(self, state: OrchestratorState) -> dict[str, Any]:
        result = state.get("task_result", {})
        return {
            "intent": state["intent"].intent,
            "command": state["command"],
            "schedule_id": state.get("schedule_id"),
            "schedule": state.get("schedule_meta", {}),
            "items": result.get("items", []),
            "tasks": result.get("tasks", []),
            "events": result.get("events", []),
        }

    @staticmethod
    def _normalise_chat_history(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        result: list[dict[str, str]] = []
        for item in value[-8:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            result.append({"role": role, "content": content[:1000]})
        return result

    @staticmethod
    def _plural_ru(number: int, forms: tuple[str, str, str]) -> str:
        number = abs(int(number))
        last_two = number % 100
        last = number % 10
        if 11 <= last_two <= 14:
            return forms[2]
        if last == 1:
            return forms[0]
        if 2 <= last <= 4:
            return forms[1]
        return forms[2]

    @staticmethod
    def _schedule_meta(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        # Не передаём модели весь список задач и событий при распознавании.
        return {
            key: value[key]
            for key in ("id", "name", "period")
            if key in value
        }

    @staticmethod
    def _parse_schedule_id(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if result <= 0 or (isinstance(value, float) and not value.is_integer()):
            return None
        return result

    @staticmethod
    def _local_now() -> datetime:
        timezone_name = os.getenv("SECRETARY_TIMEZONE", "Europe/Stockholm")
        try:
            return datetime.now(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            return datetime.now().astimezone()

    @staticmethod
    def _result(status: str, message: str) -> dict[str, Any]:
        return {
            "status": status,
            "success": status == "ok",
            "message": message,
            "agents_called": [],
            "agent_results": [],
        }
