from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Slot:
    start: int
    end: int
    title: str
    item_id: int | None = None


class ConflictAgent:

    PERIOD_DAYS = {"Day": 1, "Week": 7, "Month": 28}
    DEFAULT_TITLE = "Без названия"

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        parsed = self._parse_input(data)
        if isinstance(parsed, dict):
            return parsed

        items, assignments, event_creations, day_count = parsed

        planned_ids = [self._int(a.get("task_id")) for a in assignments]
        if any(tid is None or tid <= 0 for tid in planned_ids):
            return self._error("В назначении дедлайна отсутствует корректный ID.")
        if len(set(planned_ids)) != len(planned_ids):
            return self._error("В плане повторяется ID дедлайна.")

        occupied, conflicts = self._collect_occupied(
            items, day_count, set(planned_ids)
        )
        proposed, proposed_conflicts = self._collect_proposed(
            assignments, event_creations, day_count
        )
        conflicts.extend(proposed_conflicts)
        conflicts.extend(self._find_overlaps(occupied, proposed))

        return {
            "agent": "ConflictAgent",
            "status": "ok" if not conflicts else "conflict",
            "is_valid": not conflicts,
            "conflicts": conflicts,
            "checked_assignments": len(assignments),
            "checked_event_creations": len(event_creations),
        }

    # ---------- разбор входа ----------

    def _parse_input(
        self, data: Any
    ) -> tuple[list, list, list, int] | dict[str, Any]:
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

        items = data.get("items")
        if items is None:
            events = data.get("events") or []
            tasks = data.get("tasks") or []
            if not isinstance(events, list) or not isinstance(tasks, list):
                return self._error("События и дедлайны должны быть списками.")
            items = [*events, *tasks]

        assignments = planner.get("assignments", [])
        event_creations = planner.get("event_creations", [])

        lists = (items, assignments, event_creations)
        if not all(isinstance(v, list) for v in lists):
            return self._error("Назначения и элементы расписания должны быть списками.")
        if any(not isinstance(x, dict) for group in lists for x in group):
            return self._error("Список элементов расписания повреждён.")

        return items, assignments, event_creations, day_count

    # ---------- сбор слотов ----------

    def _collect_occupied(
        self,
        items: list[dict[str, Any]],
        day_count: int,
        planned_ids: set[int | None],
    ) -> tuple[list[Slot], list[dict[str, Any]]]:
        occupied: list[Slot] = []
        conflicts: list[dict[str, Any]] = []

        for item in items:
            item_id = self._int(item.get("id"))
            if item.get("type") == "task" and item_id in planned_ids:
                continue

            raw_start = item.get("start_time_in_minutes")
            raw_end = item.get("end_time_in_minutes")
            if raw_start is None and raw_end is None:
                continue

            start, end = self._int(raw_start), self._int(raw_end)
            title = str(item.get("title", self.DEFAULT_TITLE))

            if not self._valid_interval(start, end, day_count):
                conflicts.append({
                    "item_id": item_id,
                    "title": title,
                    "reason": (
                        "У существующей записи некорректный "
                        "или неполный временной интервал."
                    ),
                })
                continue

            occupied.append(Slot(start, end, title, item_id))

        return occupied, conflicts

    def _collect_proposed(
        self,
        assignments: list[dict[str, Any]],
        event_creations: list[dict[str, Any]],
        day_count: int,
    ) -> tuple[list[Slot], list[dict[str, Any]]]:
        proposed: list[Slot] = []
        conflicts: list[dict[str, Any]] = []

        for a in assignments:
            task_id = self._int(a.get("task_id"))
            start = self._int(a.get("start_time_in_minutes"))
            end = self._int(a.get("end_time_in_minutes"))
            day_index = self._int(a.get("day_index"))
            title = str(a.get("title", self.DEFAULT_TITLE))

            if None in (task_id, start, end, day_index):
                conflicts.append({
                    "item_id": task_id,
                    "title": title,
                    "reason": "В назначении отсутствуют обязательные поля.",
                })
                continue
            if task_id <= 0 or not 0 <= day_index < day_count:
                conflicts.append({
                    "item_id": task_id,
                    "title": title,
                    "reason": "ID или день выходит за допустимые границы.",
                })
                continue
            if not self._valid_interval(start, end, day_count, day_index):
                conflicts.append({
                    "item_id": task_id,
                    "title": title,
                    "reason": "Некорректный временной интервал.",
                })
                continue

            proposed.append(Slot(start, end, title, task_id))

        for e in event_creations:
            start = self._int(e.get("start_time_in_minutes"))
            end = self._int(e.get("end_time_in_minutes"))
            day_index = self._int(e.get("day_index"))
            title = str(e.get("title", self.DEFAULT_TITLE))

            if None in (start, end, day_index) or not title.strip():
                conflicts.append({
                    "title": title,
                    "reason": "В новом событии отсутствуют название или время.",
                })
                continue
            if not 0 <= day_index < day_count:
                conflicts.append({
                    "title": title,
                    "reason": "День нового события выходит за границы расписания.",
                })
                continue
            if not self._valid_interval(start, end, day_count, day_index):
                conflicts.append({
                    "title": title,
                    "reason": "Некорректный интервал нового события.",
                })
                continue

            duration = self._int(e.get("duration_minutes"))
            if duration is not None and duration != end - start:
                conflicts.append({
                    "title": title,
                    "reason": "Длительность нового события не совпадает с интервалом.",
                })
                continue

            proposed.append(Slot(start, end, title))

        return proposed, conflicts

    # ---------- пересечения ----------

    def _find_overlaps(
        self,
        occupied: list[Slot],
        proposed: list[Slot],
    ) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []

        for i, cand in enumerate(proposed):
            for busy in occupied:
                if self._overlaps(cand, busy):
                    conflicts.append({
                        "item_id": cand.item_id,
                        "title": cand.title,
                        "conflicts_with": busy.title,
                        "reason": "Временные интервалы пересекаются.",
                    })
            for other in proposed[:i]:
                if self._overlaps(cand, other):
                    conflicts.append({
                        "item_id": cand.item_id,
                        "title": cand.title,
                        "conflicts_with": other.title,
                        "reason": "Два новых назначения пересекаются.",
                    })

        return conflicts

    # ---------- утилиты ----------

    @staticmethod
    def _overlaps(a: Slot, b: Slot) -> bool:
        return a.start < b.end and b.start < a.end

    @staticmethod
    def _valid_interval(
        start: int | None,
        end: int | None,
        day_count: int,
        expected_day: int | None = None,
    ) -> bool:
        if start is None or end is None:
            return False
        if start < 0 or end <= start or end > day_count * 1440:
            return False

        start_day = start // 1440
        end_day = (end - 1) // 1440

        if expected_day is None:
            return start_day < day_count and start_day == end_day
        return start_day == expected_day and end_day == expected_day

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