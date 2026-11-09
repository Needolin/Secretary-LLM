from __future__ import annotations

from typing import Any


class ConflictAgent:
    """Проверяет предложения PlannerAgent для дедлайнов и новых событий."""

    PERIOD_DAYS = {"Day": 1, "Week": 7, "Month": 28}

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return self._error("Ожидался объект с расписанием.")
        schedule = data.get("schedule") or {}
        if not isinstance(schedule, dict):
            return self._error("Параметры расписания должны быть объектом.")
        day_count = self.PERIOD_DAYS.get(schedule.get("period", "Week"))
        if day_count is None:
            return self._error("Неизвестный период расписания.")

        planner = data.get("planner_result") or {}
        if not isinstance(planner, dict):
            return self._error("Результат PlannerAgent повреждён.")
        assignments = planner.get("assignments", [])
        event_creations = planner.get("event_creations", [])
        items = data.get("items")
        if items is None:
            events = data.get("events") or []
            tasks = data.get("tasks") or []
            if not isinstance(events, list) or not isinstance(tasks, list):
                return self._error("События и дедлайны должны быть списками.")
            items = [*events, *tasks]

        if not all(isinstance(value, list) for value in (items, assignments, event_creations)):
            return self._error("Назначения и элементы расписания должны быть списками.")
        if any(not isinstance(item, dict) for item in [*items, *assignments, *event_creations]):
            return self._error("Список элементов расписания повреждён.")

        proposed_task_ids = [self._int(item.get("task_id")) for item in assignments]
        if any(value is None or value <= 0 for value in proposed_task_ids):
            return self._error("В назначении дедлайна отсутствует корректный ID.")
        if len(set(proposed_task_ids)) != len(proposed_task_ids):
            return self._error("В плане повторяется ID дедлайна.")
        proposed_task_id_set = set(proposed_task_ids)

        conflicts: list[dict[str, Any]] = []
        occupied: list[tuple[int, int, str, str]] = []
        for item in items:
            item_id = self._int(item.get("id"))
            if item.get("type") == "task" and item_id in proposed_task_id_set:
                continue
            raw_start, raw_end = item.get("start_time_in_minutes"), item.get("end_time_in_minutes")
            if raw_start is None and raw_end is None:
                continue
            start, end = self._int(raw_start), self._int(raw_end)
            title = str(item.get("title", "Без названия"))
            if (start is None or end is None or start < 0 or end <= start
                    or end > day_count * 1440
                    or start // 1440 >= day_count
                    or start // 1440 != (end - 1) // 1440):
                conflicts.append({
                    "item_id": item_id,
                    "title": title,
                    "reason": "У существующей записи некорректный или неполный временной интервал.",
                })
                continue
            occupied.append((start, end, title, "existing"))

        proposed: list[tuple[int, int, str, str, int | None, int]] = []
        for assignment in assignments:
            task_id = self._int(assignment.get("task_id"))
            start = self._int(assignment.get("start_time_in_minutes"))
            end = self._int(assignment.get("end_time_in_minutes"))
            day_index = self._int(assignment.get("day_index"))
            title = str(assignment.get("title", "Без названия"))
            if None in (task_id, start, end, day_index):
                conflicts.append({"item_id": task_id, "title": title, "reason": "В назначении отсутствуют обязательные поля."})
                continue
            if task_id <= 0 or not 0 <= day_index < day_count:
                conflicts.append({"item_id": task_id, "title": title, "reason": "ID или день выходит за допустимые границы."})
                continue
            if (start < 0 or end <= start or end > day_count * 1440
                    or start // 1440 != day_index or (end - 1) // 1440 != day_index):
                conflicts.append({"item_id": task_id, "title": title, "reason": "Некорректный временной интервал."})
                continue
            proposed.append((start, end, title, "task", task_id, day_index))

        for index, event in enumerate(event_creations):
            start = self._int(event.get("start_time_in_minutes"))
            end = self._int(event.get("end_time_in_minutes"))
            day_index = self._int(event.get("day_index"))
            title = str(event.get("title", "Без названия"))
            if None in (start, end, day_index) or not title.strip():
                conflicts.append({"title": title, "reason": "В новом событии отсутствуют название или время."})
                continue
            if not 0 <= day_index < day_count:
                conflicts.append({"title": title, "reason": "День нового события выходит за границы расписания."})
                continue
            if (start < 0 or end <= start or end > day_count * 1440
                    or start // 1440 != day_index or (end - 1) // 1440 != day_index):
                conflicts.append({"title": title, "reason": "Некорректный интервал нового события."})
                continue
            duration = self._int(event.get("duration_minutes"))
            if duration is not None and duration != end - start:
                conflicts.append({"title": title, "reason": "Длительность нового события не совпадает с интервалом."})
                continue
            proposed.append((start, end, title, "event", None, day_index))

        for index, candidate in enumerate(proposed):
            start, end, title, kind, item_id, _day = candidate
            for busy_start, busy_end, busy_title, _busy_kind in occupied:
                if start < busy_end and busy_start < end:
                    conflicts.append({
                        "item_id": item_id,
                        "title": title,
                        "conflicts_with": busy_title,
                        "reason": "Временные интервалы пересекаются.",
                    })
            for other in proposed[:index]:
                other_start, other_end, other_title = other[0], other[1], other[2]
                if start < other_end and other_start < end:
                    conflicts.append({
                        "item_id": item_id,
                        "title": title,
                        "conflicts_with": other_title,
                        "reason": "Два новых назначения пересекаются.",
                    })

        return {
            "agent": "ConflictAgent",
            "status": "ok" if not conflicts else "conflict",
            "is_valid": not conflicts,
            "conflicts": conflicts,
            "checked_assignments": len(assignments),
            "checked_event_creations": len(event_creations),
        }

    @staticmethod
    def _int(value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, float) and not value.is_integer():
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "agent": "ConflictAgent",
            "status": "error",
            "is_valid": False,
            "conflicts": [{"reason": message}],
        }
