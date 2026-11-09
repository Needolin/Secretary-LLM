from __future__ import annotations

from typing import Any


class ExplanationAgent:
    """Формирует пользователю объяснение результатов планирования и проверок."""

    WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"agent": "ExplanationAgent", "status": "error", "message": "Нет данных для объяснения результата."}

        intent = data.get("intent", "plan_day")
        planner = data.get("planner_result") or {}
        conflict = data.get("conflict_result") or {}

        if not conflict.get("is_valid", True):
            issues = conflict.get("conflicts", [])
            details = []
            for issue in issues[:3] if isinstance(issues, list) else []:
                if not isinstance(issue, dict):
                    continue
                title = str(issue.get("title") or "Запись")
                other = str(issue.get("conflicts_with") or "")
                reason = str(issue.get("reason") or "конфликт интервалов")
                details.append(f"«{title}»" + (f" пересекается с «{other}»" if other else "") + f": {reason}")
            suffix = " " + " ".join(details) if details else ""
            return {
                "agent": "ExplanationAgent",
                "status": "conflict",
                "message": f"Не применил план: обнаружено конфликтов — {len(issues) if isinstance(issues, list) else 0}. Исправьте ограничения и повторите запрос." + suffix,
            }

        if intent == "check_load":
            rows = planner.get("load", [])
            if not rows:
                message = "Пока нет событий с временными интервалами, поэтому загрузку оценить нечего."
            else:
                busiest = max(rows, key=lambda row: row.get("load_percent", 0))
                message = (
                    f"Проверил расписание. Самый загруженный день — {self._day_label(busiest.get('day_index'), data.get('schedule', {}))}: "
                    f"занято {busiest['busy_minutes'] // 60} ч {busiest['busy_minutes'] % 60:02d} мин "
                    f"({busiest['load_percent']}% рабочего окна)."
                )
                if busiest.get("load_percent", 0) >= 85:
                    message += " Это плотный день — лучше оставить резерв."
            return {"agent": "ExplanationAgent", "status": "ok", "message": message}

        if intent == "plan_events":
            creations = planner.get("event_creations", [])
            requested = planner.get("requested_count", len(creations))
            unscheduled = planner.get("unscheduled_events", [])
            title = str((creations[0].get("title") if creations else data.get("event_request", {}).get("title")) or "события")
            duration = planner.get("duration_minutes")
            if creations:
                message = f"Подготовил {len(creations)} из {requested} событий «{title}»."
                preview = creations[:7]
                places = [
                    f"{self._day_label(item.get('day_index'), data.get('schedule', {}))} "
                    f"{self._time(item.get('start_time_in_minutes'))}–{self._time(item.get('end_time_in_minutes'))}"
                    for item in preview
                ]
                message += " " + "; ".join(places) + "."
                if duration:
                    message += f" Длительность каждого — {duration // 60} ч {duration % 60:02d} мин."
            else:
                message = planner.get("message", "Не удалось подобрать интервалы для новых событий.")
            if unscheduled:
                message += f" Не удалось разместить: {len(unscheduled)}."
                for item in unscheduled[:3]:
                    message += f" {self._day_label(item.get('day_index'), data.get('schedule', {}))}: {item.get('reason', 'нет свободного окна')}"
            return {"agent": "ExplanationAgent", "status": "ok" if creations else "needs_input", "message": message}

        assignments = planner.get("assignments", [])
        needs_duration = planner.get("needs_duration", [])
        overdue = planner.get("overdue", [])
        unscheduled = planner.get("unscheduled", [])
        invalid_tasks = planner.get("invalid_tasks", [])
        invalid_events = planner.get("invalid_events", [])
        already = planner.get("already_scheduled", [])

        if not assignments and not (needs_duration or overdue or unscheduled or invalid_tasks or invalid_events or already):
            message = "В расписании нет дедлайнов, которым нужно назначить время."
        elif not assignments and already and not (needs_duration or overdue or unscheduled or invalid_tasks or invalid_events):
            message = "Подходящие дедлайны уже имеют назначенное время — расписание оставил без изменений."
        elif assignments:
            message = f"Подготовил план: размещено {len(assignments)} дедлайнов."
            blocks = [
                f"«{item['title']}» — {self._day_label(item.get('day_index'), data.get('schedule', {}))}, "
                f"{self._time(item['start_time_in_minutes'])}–{self._time(item['end_time_in_minutes'])}"
                for item in assignments[:5]
            ]
            message += " " + ", ".join(blocks) + "."
            if len(assignments) > len(blocks):
                message += f" Ещё {len(assignments) - len(blocks)} — в остальных днях."
        else:
            message = "Пока не удалось безопасно разместить новые дедлайны."

        if needs_duration:
            message += f" У {len(needs_duration)} дедлайнов не указана длительность — добавьте её, чтобы их можно было разместить."
        if overdue:
            message += f" {len(overdue)} дедлайнов уже просрочено."
        if unscheduled:
            message += f" Для {len(unscheduled)} дедлайнов не нашлось свободного окна до срока."
        if invalid_tasks:
            message += f" У {len(invalid_tasks)} дедлайнов некорректные данные или неполный интервал — исправьте их перед планированием."
        if invalid_events:
            message += f" У {len(invalid_events)} событий некорректные или неполные интервалы — проверьте их перед планированием."
        if already and assignments:
            message += f" Ещё {len(already)} уже имеют назначенное время и оставлены без изменений."
        return {"agent": "ExplanationAgent", "status": "ok", "message": message}

    @classmethod
    def _day_label(cls, day_index: Any, schedule: Any) -> str:
        try:
            index = int(day_index)
        except (TypeError, ValueError):
            return "день не указан"
        period = schedule.get("period", "Week") if isinstance(schedule, dict) else "Week"
        if period == "Day":
            return "сегодня"
        if period == "Week" and 0 <= index < len(cls.WEEKDAYS):
            return cls.WEEKDAYS[index]
        return f"день {index + 1}"

    @staticmethod
    def _time(minutes: int) -> str:
        local = int(minutes) % 1440
        return f"{local // 60:02d}:{local % 60:02d}"
