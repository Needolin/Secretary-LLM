from __future__ import annotations

from datetime import date, datetime
from typing import Any


class PlannerAgent:
    """Детерминированно планирует дедлайны и новые события.

    Агент не обращается к базе и не вызывает LLM. Он получает снимок выбранного
    расписания, строит предложения, а запись делает backend только после проверки
    ConflictAgent. Это позволяет отдельно тестировать алгоритм и безопасно
    применять весь пакет изменений атомарно.
    """

    WORKDAY_START = 9 * 60
    WORKDAY_END = 22 * 60
    PERIOD_DAYS = {"Day": 1, "Week": 7, "Month": 28}
    PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

    def __init__(self, theme_id_file: str | None = None) -> None:
        self.theme_id_file = theme_id_file

    # ------------------------------------------------------------------
    # Точка входа
    # ------------------------------------------------------------------

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return self._error("PlannerAgent ожидает объект с расписанием.")

        schedule = data.get("schedule") or {}
        if not isinstance(schedule, dict):
            return self._error("Параметры расписания должны быть объектом.")

        period = schedule.get("period", "Week")
        day_count = self.PERIOD_DAYS.get(period)
        if day_count is None:
            return self._error(f"Неизвестный период расписания: {period}.")

        workday_start = self._minute(data.get("workday_start_minutes", self.WORKDAY_START))
        workday_end = self._minute(data.get("workday_end_minutes", self.WORKDAY_END))
        if workday_start is None or workday_end is None or workday_end <= workday_start:
            return self._error("Рабочее окно должно содержать корректные минуты начала и окончания.")
        if workday_start >= 1440 or workday_end > 1440:
            return self._error("Рабочее окно должно находиться в пределах одного дня.")

        extracted = self._extract_items(data)
        if isinstance(extracted, dict) and extracted.get("status") == "error":
            return extracted
        tasks, events = extracted  # type: ignore[misc]

        today = self._parse_today(data.get("today"))
        current_day = self._current_day_index(period, today)
        current_time = self._minute(data.get("current_time_minutes", workday_start))
        if current_time is None or current_time > 1439:
            return self._error("Текущее время должно находиться в пределах 00:00–23:59.")

        if data.get("intent") == "check_load":
            return self._check_load(events, tasks, day_count, workday_start, workday_end)

        if data.get("intent") == "plan_events":
            return self._plan_events(
                schedule=schedule,
                events=events,
                tasks=tasks,
                request=data.get("event_request") or {},
                day_count=day_count,
                today=today,
                current_time=current_time,
            )

        return self._plan(
            tasks=tasks,
            events=events,
            period=period,
            day_count=day_count,
            current_day=current_day,
            current_time=current_time,
            today=today,
            workday_start=workday_start,
            workday_end=workday_end,
        )

    # ------------------------------------------------------------------
    # Извлечение и валидация входных элементов
    # ------------------------------------------------------------------

    def _extract_items(
        self, data: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | dict[str, Any]:
        """Извлекает tasks и events из входных данных.

        Поддерживает два формата:
        - раздельные списки events/tasks: имя списка — источник истины,
          поле type проставляется принудительно;
        - общий список items: источник истины — поле type (fallback: event).

        Если в events встретился элемент с type="task" или в tasks — с
        type="event", возвращается ошибка: списки перепутаны.

        Возвращает (tasks, events) либо словарь ошибки (status="error").
        """
        items = data.get("items")

        if items is None:
            raw_events = data.get("events")
            raw_tasks = data.get("tasks")
            if raw_events is None:
                raw_events = []
            if raw_tasks is None:
                raw_tasks = []

            if not isinstance(raw_events, list) or not isinstance(raw_tasks, list):
                return self._error("Списки событий и дедлайнов должны быть списками.")

            for item in raw_events:
                if not isinstance(item, dict):
                    return self._error(
                        "Список событий повреждён: элементы должны быть объектами."
                    )
            for item in raw_tasks:
                if not isinstance(item, dict):
                    return self._error(
                        "Список дедлайнов повреждён: элементы должны быть объектами."
                    )

            # Строгая проверка: перепутанные списки — ошибка данных,
            # а не повод молча перезаписать type.
            misplaced_in_events = [
                item for item in raw_events if item.get("type") == "task"
            ]
            if misplaced_in_events:
                ids = ", ".join(
                    str(item.get("id", "?")) for item in misplaced_in_events
                )
                return self._error(
                    "В списке events найдены элементы с type='task' "
                    f"(id: {ids}). Проверьте, не перепутаны ли events и tasks."
                )

            misplaced_in_tasks = [
                item for item in raw_tasks if item.get("type") == "event"
            ]
            if misplaced_in_tasks:
                ids = ", ".join(
                    str(item.get("id", "?")) for item in misplaced_in_tasks
                )
                return self._error(
                    "В списке tasks найдены элементы с type='event' "
                    f"(id: {ids}). Проверьте, не перепутаны ли events и tasks."
                )

            # Явно проставляем тип по имени списка, не теряя исходные поля.
            events = [{**item, "type": "event"} for item in raw_events]
            tasks = [{**item, "type": "task"} for item in raw_tasks]
            return tasks, events

        if not isinstance(items, list):
            return self._error(
                "Список элементов расписания повреждён: ожидается список."
            )
        for item in items:
            if not isinstance(item, dict):
                return self._error(
                    "Список элементов расписания повреждён: элементы должны быть объектами."
                )

        tasks = [item for item in items if item.get("type") == "task"]
        events = [item for item in items if item.get("type", "event") == "event"]
        return tasks, events

    # ------------------------------------------------------------------
    # Режим 1: Планирование дедлайнов
    # ------------------------------------------------------------------

    def _plan(
        self,
        *,
        tasks: list[dict[str, Any]],
        events: list[dict[str, Any]],
        period: str,
        day_count: int,
        current_day: int,
        current_time: int,
        today: date,
        workday_start: int,
        workday_end: int,
    ) -> dict[str, Any]:
        occupied: dict[int, list[tuple[int, int, str, int | None]]] = {
            day: [] for day in range(day_count)
        }

        invalid_events: list[dict[str, Any]] = []
        unsafe_existing_intervals = False
        for event in events:
            raw_start = event.get("start_time_in_minutes")
            raw_end = event.get("end_time_in_minutes")
            if raw_start is not None or raw_end is not None:
                if not self._reserve_item(occupied, event):
                    invalid_events.append(self._brief(
                        event, "У события некорректный или неполный временной интервал."
                    ))
                    unsafe_existing_intervals = True

        pending: list[tuple[int, int, int, dict[str, Any]]] = []
        already_scheduled: list[dict[str, Any]] = []
        needs_duration: list[dict[str, Any]] = []
        overdue: list[dict[str, Any]] = []
        invalid_tasks: list[dict[str, Any]] = []

        for task in tasks:
            # Завершённые дедлайны не планируем повторно, но их интервалы
            # остаются занятыми и не должны перекрываться новыми назначениями.
            if task.get("status") == "completed":
                if self._has_complete_interval(task):
                    if not self._reserve_item(occupied, task):
                        invalid_tasks.append(self._brief(
                            task, "У завершённого дедлайна некорректный временной интервал."
                        ))
                        unsafe_existing_intervals = True
                elif (task.get("start_time_in_minutes") is not None
                      or task.get("end_time_in_minutes") is not None):
                    invalid_tasks.append(self._brief(
                        task, "У завершённого дедлайна сохранён некорректный или неполный интервал."
                    ))
                    unsafe_existing_intervals = True
                continue

            if self._has_complete_interval(task):
                if self._reserve_item(occupied, task):
                    already_scheduled.append(task)
                else:
                    invalid_tasks.append(self._brief(
                        task, "У существующего интервала некорректное время."
                    ))
                    unsafe_existing_intervals = True
                continue

            raw_start = task.get("start_time_in_minutes")
            raw_end = task.get("end_time_in_minutes")
            if raw_start is not None or raw_end is not None:
                if raw_start is None or raw_end is None:
                    reason = "У дедлайна задана только одна граница времени. Укажите полный интервал."
                else:
                    reason = "У дедлайна некорректный временной интервал."
                invalid_tasks.append(self._brief(task, reason))
                unsafe_existing_intervals = True
                continue

            duration = self._positive_int(task.get("estimated_duration_minutes"))
            if duration is None:
                needs_duration.append(self._brief(task, "Не указана длительность."))
                continue
            if duration > workday_end - workday_start:
                invalid_tasks.append(self._brief(
                    task,
                    f"Длительность больше рабочего окна ({(workday_end - workday_start) // 60} ч).",
                ))
                continue

            task_id = self._positive_int(task.get("id"))
            if task_id is None:
                invalid_tasks.append(self._brief(task, "У дедлайна отсутствует корректный ID."))
                continue

            due_day, is_overdue, due_reason = self._deadline_day(
                task, period, day_count, today, current_day
            )
            if is_overdue:
                overdue.append(self._brief(task, due_reason or "Дедлайн уже прошёл."))
                continue
            if due_reason:
                invalid_tasks.append(self._brief(task, due_reason))
                continue
            if due_day < current_day:
                invalid_tasks.append(self._brief(task, "Указанный день расписания уже прошёл."))
                continue

            priority = self.PRIORITY_ORDER.get(str(task.get("priority", "medium")), 1)
            pending.append((due_day, priority, task_id, task))

        # Сначала ближайшие дедлайны, внутри одинакового срока — высокий приоритет.
        pending.sort(key=lambda row: (row[0], row[1], row[2], str(row[3].get("title", "")).lower()))

        assignments: list[dict[str, Any]] = []
        unscheduled: list[dict[str, Any]] = []

        for due_day, _priority, task_id, task in pending:
            if unsafe_existing_intervals:
                unscheduled.append(self._brief(
                    task,
                    "Сначала исправьте неполные или некорректные интервалы существующих записей."
                ))
                continue
            duration = int(task["estimated_duration_minutes"])
            placement = self._find_slot(
                occupied=occupied,
                start_day=current_day,
                end_day=due_day,
                duration=duration,
                workday_start=workday_start,
                workday_end=workday_end,
                current_time=current_time,
            )
            if placement is None:
                unscheduled.append(self._brief(
                    task,
                    "До дедлайна не осталось подходящего непрерывного свободного окна.",
                ))
                continue

            day_index, start, end = placement
            assignment = {
                "task_id": task_id,
                "title": str(task.get("title", "Без названия")),
                "day_index": day_index,
                "start_time_in_minutes": start,
                "end_time_in_minutes": end,
                "estimated_duration_minutes": duration,
                "deadline": task.get("deadline"),
            }
            assignments.append(assignment)
            occupied[day_index].append((start, end, assignment["title"], task_id))
            occupied[day_index].sort(key=lambda value: (value[0], value[1]))

        unresolved = needs_duration or overdue or unscheduled or invalid_tasks or invalid_events
        if assignments and not unresolved:
            status = "complete"
        elif assignments:
            status = "partial"
        elif unresolved:
            status = "needs_input"
        else:
            status = "nothing_to_plan"

        return {
            "agent": "PlannerAgent",
            "status": status,
            "assignments": assignments,
            "already_scheduled": [self._brief(item) for item in already_scheduled],
            "needs_duration": needs_duration,
            "overdue": overdue,
            "unscheduled": unscheduled,
            "invalid_tasks": invalid_tasks,
            "invalid_events": invalid_events,
            "workday": {"start_minutes": workday_start, "end_minutes": workday_end},
            "current_day_index": current_day,
            "current_time_minutes": current_time,
            "day_count": day_count,
            "message": self._summary_text(
                assignments, needs_duration, overdue, unscheduled,
                already_scheduled, invalid_tasks, invalid_events,
            ),
        }

    # ------------------------------------------------------------------
    # Режим 2: Планирование повторяющихся событий
    # ------------------------------------------------------------------

    def _plan_events(
        self,
        *,
        schedule: dict[str, Any],
        events: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        request: dict[str, Any],
        day_count: int,
        today: date,
        current_time: int,
    ) -> dict[str, Any]:
        """Распределяет новые повторяющиеся события по периоду расписания.

        Для недельного и месячного расписания используются все дни периода:
        это шаблон расписания, а не ограничение только оставшимися днями недели.
        Для расписания одного дня учитывается текущее время.

        distribution="first_available" трактуется буквально: сначала размещаем
        события в самых ранних днях, где реально есть свободное окно рядом с
        preferred_time; в ответе day_indices содержит только использованные дни.
        """
        if not isinstance(request, dict):
            return self._error("Параметры новых событий должны быть объектом.")

        title = str(request.get("title") or "").strip()
        count = self._positive_int(request.get("event_count"))
        preferred_time = self._minute(request.get("preferred_time_minutes"))
        duration = self._positive_int(request.get("estimated_duration_minutes"))
        distribution = request.get("distribution") or "evenly"
        if not title:
            return self._error("Не указано название нового события.")
        if count is None or count > day_count:
            return self._error(f"Количество событий должно быть от 1 до {day_count} для этого периода.")
        if preferred_time is None or preferred_time > 1439:
            return self._error("Укажите время начала события в пределах 00:00–23:59.")
        if duration is None or duration > 1440:
            return self._error("Укажите длительность события от 1 до 1440 минут.")
        if preferred_time + duration > 1440:
            return self._error("Событие выходит за пределы суток. Выберите более раннее время или меньшую длительность.")
        if distribution not in {"evenly", "custom", "first_available"}:
            distribution = "evenly"

        requested_days = request.get("day_indices")
        if distribution == "custom":
            if not isinstance(requested_days, list) or len(requested_days) != count:
                return self._error("Для собственного распределения укажите день для каждого события.")
            day_indices: list[int] = []
            for raw_day in requested_days:
                day = self._nonnegative_int(raw_day)
                if day is None or day >= day_count:
                    return self._error("Один из выбранных дней находится за пределами расписания.")
                if day in day_indices:
                    return self._error("Для собственного распределения выбраны повторяющиеся дни.")
                day_indices.append(day)
        elif distribution == "first_available":
            # Пробуем все дни периода по порядку и берём первые, где реально
            # удалось разместить событие. Остановимся, когда наберём count.
            day_indices = list(range(day_count))
        else:
            # Равномерно выбираем уникальные дни по всему периоду. Для недели
            # и трёх тренировок это Пн/Ср/Пт (индексы 0, 2, 4).
            day_indices = [int(index * day_count / count) for index in range(count)]

        occupied: dict[int, list[tuple[int, int, str, int | None]]] = {
            day: [] for day in range(day_count)
        }
        invalid_existing: list[dict[str, Any]] = []
        for item in [*events, *tasks]:
            raw_start = item.get("start_time_in_minutes")
            raw_end = item.get("end_time_in_minutes")
            if raw_start is None and raw_end is None:
                continue
            if not self._reserve_item(occupied, item):
                invalid_existing.append(self._brief(
                    item, "Существующий интервал неполный или некорректный."
                ))

        if invalid_existing:
            return {
                "agent": "PlannerAgent",
                "status": "needs_input",
                "assignments": [],
                "event_creations": [],
                "requested_count": count,
                "created_count": 0,
                "unscheduled_events": [],
                "invalid_existing": invalid_existing,
                "message": "Сначала исправьте неполные или некорректные интервалы существующих записей.",
            }

        color = request.get("color") or "#8AF3D1"
        description = str(request.get("description") or "").strip()
        creations: list[dict[str, Any]] = []
        unscheduled: list[dict[str, Any]] = []
        chosen_days: list[int] = []

        for index, day_index in enumerate(day_indices):
            if distribution == "first_available" and len(creations) >= count:
                break

            day_offset = day_index * 1440
            candidates = [preferred_time]
            # Слово «примерно/в районе» допускает небольшой сдвиг при конфликте.
            # Начинаем с точного времени, затем проверяем ближайшие четверти часа.
            for delta in range(15, 91, 15):
                candidates.extend((preferred_time + delta, preferred_time - delta))

            placed: tuple[int, int] | None = None
            for local_start in candidates:
                local_end = local_start + duration
                if local_start < 0 or local_end > 1440:
                    continue
                # Для Day расписания нельзя назначать время, которое уже прошло.
                if schedule.get("period") == "Day" and local_start < current_time:
                    continue
                absolute_start = day_offset + local_start
                absolute_end = day_offset + local_end
                collision = any(
                    absolute_start < busy_end and busy_start < absolute_end
                    for busy_start, busy_end, _name, _item_id in occupied[day_index]
                )
                if not collision:
                    placed = (absolute_start, absolute_end)
                    break

            if placed is None:
                # Для first_available день просто пропускаем; для остальных
                # фиксируем причину, чтобы пользователь видел, куда не влезло.
                if distribution != "first_available":
                    unscheduled.append({
                        "title": title,
                        "day_index": day_index,
                        "reason": "В выбранный день нет свободного окна рядом с указанным временем.",
                    })
                continue

            start, end = placed
            creation = {
                "title": title,
                "description": description,
                "day_index": day_index,
                "start_time_in_minutes": start,
                "end_time_in_minutes": end,
                "duration_minutes": duration,
                "color": color,
                # Сквозная нумерация без дыр даже при пропущенных днях.
                "occurrence": len(creations) + 1,
            }
            creations.append(creation)
            chosen_days.append(day_index)
            occupied[day_index].append((start, end, title, None))

        if distribution == "first_available" and len(creations) < count:
            unscheduled.append({
                "title": title,
                "reason": "В оставшихся днях периода не найдено достаточно свободных окон.",
                "missing_count": count - len(creations),
            })

        if len(creations) == count:
            status = "complete"
            message = f"Подготовил {count} событий «{title}» с интервалом {duration // 60} ч {duration % 60:02d} мин."
        elif creations:
            status = "partial"
            message = f"Удалось подобрать время для {len(creations)} из {count} событий «{title}»."
        else:
            status = "needs_input"
            message = f"Не удалось разместить события «{title}» рядом с {preferred_time // 60:02d}:{preferred_time % 60:02d}."

        return {
            "agent": "PlannerAgent",
            "status": status,
            "assignments": [],
            "event_creations": creations,
            "requested_count": count,
            "created_count": len(creations),
            "unscheduled_events": unscheduled,
            # Для first_available отдаём реально использованные дни, для
            # остальных — запрошенные (даже если часть не разместилась).
            "day_indices": chosen_days if distribution == "first_available" else day_indices,
            "preferred_time_minutes": preferred_time,
            "duration_minutes": duration,
            "message": message,
        }

    # ------------------------------------------------------------------
    # Режим 3: Проверка загруженности
    # ------------------------------------------------------------------

    def _check_load(
        self,
        events: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        day_count: int,
        workday_start: int,
        workday_end: int,
    ) -> dict[str, Any]:
        """Считает загрузку по дням. Битые интервалы не теряются, а возвращаются
        в invalid_items: иначе пользователь видел бы заниженную нагрузку без
        предупреждения.
        """
        occupied: dict[int, list[tuple[int, int, str, int | None]]] = {
            day: [] for day in range(day_count)
        }
        invalid_items: list[dict[str, Any]] = []
        for item in [*events, *tasks]:
            raw_start = item.get("start_time_in_minutes")
            raw_end = item.get("end_time_in_minutes")
            if raw_start is None and raw_end is None:
                continue
            if not self._reserve_item(occupied, item):
                invalid_items.append(self._brief(
                    item, "Интервал неполный или некорректный."
                ))

        available = workday_end - workday_start
        rows: list[dict[str, Any]] = []
        for day in range(day_count):
            intervals = sorted(occupied[day], key=lambda row: (row[0], row[1]))
            clipped: list[tuple[int, int]] = []
            for start, end, _title, _item_id in intervals:
                local_start = max(workday_start, start - day * 1440)
                local_end = min(workday_end, end - day * 1440)
                if local_end > local_start:
                    clipped.append((local_start, local_end))
            busy = self._union_minutes(clipped)
            rows.append({
                "day_index": day,
                "busy_minutes": busy,
                "free_minutes": max(0, available - busy),
                "load_percent": round(min(100, busy / available * 100), 1),
                "items": [
                    {"title": title, "start": start, "end": end}
                    for start, end, title, _item_id in intervals
                ],
            })

        return {
            "agent": "PlannerAgent",
            "status": "analysis" if not invalid_items else "partial",
            "assignments": [],
            "load": rows,
            "invalid_items": invalid_items,
            "workday": {"start_minutes": workday_start, "end_minutes": workday_end},
        }

    # ------------------------------------------------------------------
    # Вспомогательные методы расчёта слотов и валидации
    # ------------------------------------------------------------------

    @staticmethod
    def _find_slot(
        *,
        occupied: dict[int, list[tuple[int, int, str, int | None]]],
        start_day: int,
        end_day: int,
        duration: int,
        workday_start: int,
        workday_end: int,
        current_time: int | None = None,
    ) -> tuple[int, int, int] | None:
        if duration <= 0 or duration > workday_end - workday_start:
            return None

        for day_index in range(start_day, end_day + 1):
            intervals = sorted(occupied[day_index], key=lambda row: (row[0], row[1]))
            candidate = workday_start
            if day_index == start_day and current_time is not None:
                candidate = max(candidate, current_time)
            day_offset = day_index * 1440
            for busy_start, busy_end, _busy_title, _busy_id in intervals:
                # Существующие записи хранят абсолютные минуты от начала периода.
                busy_start_local = busy_start - day_offset
                busy_end_local = busy_end - day_offset
                if busy_end_local <= candidate:
                    continue
                if candidate + duration <= busy_start_local:
                    start = day_offset + candidate
                    return day_index, start, start + duration
                candidate = max(candidate, busy_end_local)
                if candidate + duration > workday_end:
                    break
            if candidate + duration <= workday_end:
                start = day_offset + candidate
                return day_index, start, start + duration
        return None

    @staticmethod
    def _reserve_item(
        occupied: dict[int, list[tuple[int, int, str, int | None]]],
        item: dict[str, Any],
    ) -> bool:
        start = PlannerAgent._nonnegative_int(item.get("start_time_in_minutes"))
        end = PlannerAgent._nonnegative_int(item.get("end_time_in_minutes"))
        if start is None or end is None or end <= start:
            return False
        day_index = start // 1440
        if day_index not in occupied or (end - 1) // 1440 != day_index:
            return False
        occupied[day_index].append((
            start, end, str(item.get("title", "Без названия")),
            PlannerAgent._positive_int(item.get("id")),
        ))
        return True

    @staticmethod
    def _has_complete_interval(item: dict[str, Any]) -> bool:
        start = PlannerAgent._nonnegative_int(item.get("start_time_in_minutes"))
        end = PlannerAgent._nonnegative_int(item.get("end_time_in_minutes"))
        return (
            start is not None and end is not None and end > start
            and start // 1440 == (end - 1) // 1440
        )

    @staticmethod
    def _deadline_day(
        task: dict[str, Any],
        period: str,
        day_count: int,
        today: date,
        current_day: int,
    ) -> tuple[int, bool, str | None]:
        raw_deadline = task.get("deadline")
        deadline = None
        if raw_deadline not in (None, ""):
            try:
                deadline = date.fromisoformat(str(raw_deadline))
            except (TypeError, ValueError):
                return day_count - 1, False, "Дата дедлайна задана некорректно."
            if deadline.isoformat() != str(raw_deadline):
                return day_count - 1, False, "Дата дедлайна должна быть в формате YYYY-MM-DD."
            if deadline < today:
                return 0, True, "Дедлайн уже прошёл."

        if task.get("day_index") is not None:
            explicit = PlannerAgent._nonnegative_int(task.get("day_index"))
            if explicit is None:
                return day_count - 1, False, "Индекс дня дедлайна задан некорректно."
            if explicit >= day_count:
                return day_count - 1, False, "Указанный день находится за пределами расписания."
            return explicit, False, None

        if deadline is None:
            return day_count - 1, False, None
        if period == "Day":
            return 0, False, None
        if period == "Week":
            due_day = deadline.weekday()
            # Выбранное расписание — недельный шаблон текущей недели. Если срок
            # находится на следующей неделе, в пределах этого шаблона последняя
            # доступная строка — воскресенье.
            if deadline > today and (deadline - today).days >= 7 - today.weekday():
                return day_count - 1, False, None
            if due_day < current_day and deadline >= today:
                return day_count - 1, False, None
            return due_day, False, None

        if (deadline.year, deadline.month) != (today.year, today.month):
            return day_count - 1, False, None
        return min(deadline.day - 1, day_count - 1), False, None

    @staticmethod
    def _parse_today(value: Any) -> date:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
        return datetime.now().astimezone().date()

    @staticmethod
    def _current_day_index(period: str, today: date) -> int:
        if period == "Day":
            return 0
        if period == "Week":
            return today.weekday()
        return min(today.day - 1, 27)

    # ------------------------------------------------------------------
    # Единый безопасный конвертер в int.
    # _minute / _positive_int / _nonnegative_int — тонкие обёртки над ним.
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_int(
        value: Any,
        *,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> int | None:
        """Приводит значение к int с проверкой границ.

        Отсекает bool, None, дробные float и значения вне [min_value, max_value].
        """
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, float) and not value.is_integer():
            return None
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if min_value is not None and number < min_value:
            return None
        if max_value is not None and number > max_value:
            return None
        return number

    @classmethod
    def _minute(cls, value: Any) -> int | None:
        """Неотрицательное число минут (>= 0)."""
        return cls._parse_int(value, min_value=0)

    @classmethod
    def _positive_int(cls, value: Any) -> int | None:
        """Строго положительное число (> 0)."""
        return cls._parse_int(value, min_value=1)

    @classmethod
    def _nonnegative_int(cls, value: Any) -> int | None:
        """Неотрицательное число (>= 0). Синоним _minute для идентификаторов/индексов."""
        return cls._parse_int(value, min_value=0)

    @staticmethod
    def _union_minutes(intervals: list[tuple[int, int]]) -> int:
        if not intervals:
            return 0
        intervals = sorted(intervals)
        total = 0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                total += end - start
                start, end = next_start, next_end
        return total + end - start

    @staticmethod
    def _brief(task: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
        result = {
            "task_id": task.get("id"),
            "title": task.get("title", "Без названия"),
            "deadline": task.get("deadline"),
            "estimated_duration_minutes": task.get("estimated_duration_minutes"),
        }
        if reason:
            result["reason"] = reason
        return result

    @staticmethod
    def _summary_text(
        assignments: list[dict[str, Any]],
        needs_duration: list[dict[str, Any]],
        overdue: list[dict[str, Any]],
        unscheduled: list[dict[str, Any]],
        already_scheduled: list[dict[str, Any]],
        invalid_tasks: list[dict[str, Any]],
        invalid_events: list[dict[str, Any]],
    ) -> str:
        if not any((
            assignments, needs_duration, overdue, unscheduled,
            already_scheduled, invalid_tasks, invalid_events,
        )):
            return "В расписании нет дедлайнов, которым нужно назначить время."
        parts = [f"Распределено дедлайнов: {len(assignments)}."]
        if needs_duration:
            parts.append(f"Без длительности: {len(needs_duration)}.")
        if overdue:
            parts.append(f"С прошедшим сроком: {len(overdue)}.")
        if unscheduled:
            parts.append(f"Не удалось разместить: {len(unscheduled)}.")
        if invalid_tasks:
            parts.append(f"Некорректных дедлайнов: {len(invalid_tasks)}.")
        if invalid_events:
            parts.append(f"Некорректных событий: {len(invalid_events)}.")
        if already_scheduled:
            parts.append(f"Уже имеют время: {len(already_scheduled)}.")
        return " ".join(parts)

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"agent": "PlannerAgent", "status": "error", "assignments": [], "message": message}