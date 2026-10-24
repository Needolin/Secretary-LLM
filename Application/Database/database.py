from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


DATABASE_DIR = Path(__file__).resolve().parent
SCHEDULES_FILE = DATABASE_DIR / "schedules.json"
_LOCK = threading.RLock()
VALID_PERIODS = {"Day", "Week", "Month"}
VALID_PRIORITIES = {"low", "medium", "high"}
VALID_STATUSES = {"pending", "in_progress", "completed"}
VALID_EVENT_TYPES = {"event", "task"}


# ---------------------------------------------------------------------
# Общие функции хранения
# ---------------------------------------------------------------------

def _ensure_data_file_unlocked() -> None:
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    if not SCHEDULES_FILE.exists():
        SCHEDULES_FILE.write_text("[]\n", encoding="utf-8")


def _save_unlocked(schedules: list[dict[str, Any]]) -> None:
    _ensure_data_file_unlocked()
    temporary_file = SCHEDULES_FILE.with_suffix(".json.tmp")
    content = json.dumps(schedules, ensure_ascii=False, indent=2) + "\n"
    temporary_file.write_text(content, encoding="utf-8")
    temporary_file.replace(SCHEDULES_FILE)


def _load_unlocked() -> list[dict[str, Any]]:
    """Загружает расписания и мягко обновляет старые события до нового формата."""
    _ensure_data_file_unlocked()

    try:
        schedules = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Файл schedules.json содержит некорректный JSON.") from exc

    if not isinstance(schedules, list) or any(not isinstance(s, dict) for s in schedules):
        raise RuntimeError("В schedules.json должен находиться список расписаний.")

    changed = False
    for schedule in schedules:
        events = schedule.get("events")
        if events is None:
            schedule["events"] = []
            events = schedule["events"]
            changed = True
        if not isinstance(events, list):
            raise RuntimeError("Поле events каждого расписания должно быть списком.")

        used_ids: set[int] = set()
        for event in events:
            if not isinstance(event, dict):
                raise RuntimeError("Каждый элемент events должен быть объектом.")

            event_type = event.get("type")
            if event_type not in VALID_EVENT_TYPES:
                event["type"] = "event"
                event_type = "event"
                changed = True

            # Дополняем старые задачи полями, которые появились в новой версии.
            if event_type == "task":
                task_defaults = {
                    "description": "",
                    "deadline": None,
                    "priority": "medium",
                    "status": "pending",
                }
                for field, default in task_defaults.items():
                    if field not in event:
                        event[field] = default
                        changed = True

                # Задача может иметь интервал целиком, только начало, только конец
                # или не иметь назначенного времени вообще. Последние варианты
                # отображаются квадратным маркером на таймлайне.

            try:
                event_id = int(event.get("id"))
            except (TypeError, ValueError):
                event_id = 0

            if event_id <= 0 or event_id in used_ids:
                event_id = max(used_ids, default=0) + 1
                while event_id in used_ids:
                    event_id += 1
                event["id"] = event_id
                changed = True
            elif event.get("id") != event_id:
                event["id"] = event_id
                changed = True

            used_ids.add(event_id)

    if changed:
        _save_unlocked(schedules)

    return schedules


def _normalise_id(value: Any, field_name: str = "ID") -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} должен быть целым числом.") from exc
    if result <= 0:
        raise ValueError(f"{field_name} должен быть положительным числом.")
    return result


def _find_schedule(schedules: list[dict[str, Any]], schedule_id: Any) -> dict[str, Any]:
    wanted_id = _normalise_id(schedule_id, "ID расписания")
    for schedule in schedules:
        try:
            if int(schedule.get("id")) == wanted_id:
                return schedule
        except (TypeError, ValueError):
            continue
    raise ValueError(f"Расписание с ID {wanted_id} не найдено.")


def _find_event(
    schedule: dict[str, Any],
    event_id: Any,
    *,
    event_type: str | None = None,
) -> dict[str, Any]:
    wanted_id = _normalise_id(event_id, "ID события")
    for event in schedule.get("events", []):
        try:
            matches_id = int(event.get("id")) == wanted_id
        except (TypeError, ValueError):
            matches_id = False
        if matches_id and (event_type is None or event.get("type") == event_type):
            return event
    if event_type == "task":
        raise ValueError(f"Задача с ID {wanted_id} не найдена в этом расписании.")
    raise ValueError(f"Событие с ID {wanted_id} не найдено в этом расписании.")


def _next_event_id(events: list[dict[str, Any]]) -> int:
    ids = []
    for event in events:
        try:
            ids.append(int(event.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return max(ids, default=0) + 1


def _validate_time_range(data: dict[str, Any], *, required: bool) -> tuple[int, int] | None:
    start_value = data.get("start_time_in_minutes")
    end_value = data.get("end_time_in_minutes")

    if start_value is None and end_value is None and not required:
        return None
    if start_value is None or end_value is None:
        raise ValueError(
            "Укажите и start_time_in_minutes, и end_time_in_minutes."
        )

    try:
        start = int(start_value)
        end = int(end_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Время должно быть указано в минутах целыми числами.") from exc

    if start < 0 or end <= start:
        raise ValueError("Время события указано некорректно.")
    if start // 1440 != (end - 1) // 1440:
        raise ValueError("Событие или задача не могут пересекать полночь.")
    return start, end


def _validate_optional_time_fields(
    data: dict[str, Any],
    schedule: dict[str, Any],
) -> dict[str, int]:
    """Проверяет время элемента: обе границы, одна граница или ни одной."""
    start_value = data.get("start_time_in_minutes")
    end_value = data.get("end_time_in_minutes")

    if start_value is not None and end_value is not None:
        time_range = _validate_time_range(data, required=True)
        assert time_range is not None
        _validate_range_in_schedule(time_range, schedule)
        return {
            "start_time_in_minutes": time_range[0],
            "end_time_in_minutes": time_range[1],
        }

    period_limit = {
        "Day": 1440,
        "Week": 7 * 1440,
        "Month": 28 * 1440,
    }.get(schedule.get("period", "Week"))
    result: dict[str, int] = {}

    for field, value in (
        ("start_time_in_minutes", start_value),
        ("end_time_in_minutes", end_value),
    ):
        if value is None:
            continue
        try:
            minute = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Время должно быть указано в минутах целыми числами.") from exc

        upper_bound = period_limit if field == "end_time_in_minutes" else (period_limit - 1 if period_limit else None)
        if minute < 0 or (upper_bound is not None and minute > upper_bound):
            raise ValueError("Время выходит за пределы выбранного расписания.")
        result[field] = minute

    return result


def _validate_task_day_index(value: Any, schedule: dict[str, Any]) -> int | None:
    if value is None:
        return None
    try:
        day_index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("day_index должен быть целым числом.") from exc
    day_count = {"Day": 1, "Week": 7, "Month": 28}.get(schedule.get("period", "Week"), 7)
    if not 0 <= day_index < day_count:
        raise ValueError("day_index выходит за пределы выбранного расписания.")
    return day_index

def _validate_range_in_schedule(time_range: tuple[int, int] | None, schedule: dict[str, Any]) -> None:
    if time_range is None:
        return
    period_minutes = {"Day": 1440, "Week": 7 * 1440, "Month": 28 * 1440}.get(
        schedule.get("period", "Week")
    )
    if period_minutes is not None and time_range[1] > period_minutes:
        raise ValueError("Время выходит за пределы выбранного расписания.")


# ---------------------------------------------------------------------
# Расписания
# ---------------------------------------------------------------------

def ensure_data_file() -> None:
    with _LOCK:
        _ensure_data_file_unlocked()


def load_schedules() -> list[dict[str, Any]]:
    with _LOCK:
        return _load_unlocked()


def save_schedules(schedules: list[dict[str, Any]]) -> None:
    if not isinstance(schedules, list):
        raise ValueError("Расписания должны передаваться в виде списка.")
    with _LOCK:
        # После сохранения следующий load добавит type и id для старых событий.
        _save_unlocked(schedules)


def get_all_schedules() -> list[dict[str, Any]]:
    return load_schedules()


def get_schedule(schedule_id: int) -> dict[str, Any]:
    with _LOCK:
        schedules = _load_unlocked()
        return _find_schedule(schedules, schedule_id)


def create_schedule(name: str, period: str = "Week") -> dict[str, Any]:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Название расписания не может быть пустым.")
    if period not in VALID_PERIODS:
        raise ValueError("Период должен быть Day, Week или Month.")

    with _LOCK:
        schedules = _load_unlocked()
        schedule_ids = []
        for item in schedules:
            try:
                schedule_ids.append(int(item.get("id", 0)))
            except (TypeError, ValueError):
                continue
        schedule = {
            "id": max(schedule_ids, default=0) + 1,
            "name": name,
            "period": period,
            "events": [],
        }
        schedules.append(schedule)
        _save_unlocked(schedules)
        return schedule


def update_schedule(schedule_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = {"name", "period"}
    if not changes:
        raise ValueError("Не указаны изменения.")
    if set(changes) - allowed_fields:
        raise ValueError("Можно менять только name и period.")

    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)

        if "name" in changes:
            name = str(changes["name"] or "").strip()
            if not name:
                raise ValueError("Название расписания не может быть пустым.")
            schedule["name"] = name

        if "period" in changes:
            period = changes["period"]
            if period not in VALID_PERIODS:
                raise ValueError("Период должен быть Day, Week или Month.")
            schedule["period"] = period

        _save_unlocked(schedules)
        return schedule


def delete_schedule(schedule_id: int) -> None:
    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        schedules.remove(schedule)
        _save_unlocked(schedules)


# ---------------------------------------------------------------------
# События расписаний (type = "event")
# ---------------------------------------------------------------------

def get_events(schedule_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        schedule = _find_schedule(_load_unlocked(), schedule_id)

        return [
            dict(item)
            for item in schedule.get("events", [])
            if item.get("type") == "event"
        ]


def add_event(schedule_id: int, event: dict[str, Any]) -> dict[str, Any]:
    """Добавляет обычное событие; начало и конец необязательны."""
    title = str(event.get("title", "") or "").strip()
    if not title:
        raise ValueError("У события должно быть название.")

    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        time_fields = _validate_optional_time_fields(event, schedule)
        day_index = _validate_task_day_index(event.get("day_index"), schedule)

        new_event: dict[str, Any] = {
            "id": _next_event_id(schedule.setdefault("events", [])),
            "type": "event",
            "title": title,
            "description": str(event.get("description", "") or "").strip(),
        }
        new_event.update(time_fields)
        if day_index is not None:
            new_event["day_index"] = day_index

        schedule["events"].append(new_event)
        schedule["events"].sort(
            key=lambda item: (
                item.get("start_time_in_minutes") is None,
                item.get("start_time_in_minutes", 0) or 0,
            )
        )
        _save_unlocked(schedules)
        return dict(new_event)



def update_event(
    schedule_id: int,
    event_index: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    allowed_fields = {
        "title", "description", "start_time_in_minutes", "end_time_in_minutes",
        "day_index",
    }
    if not changes or set(changes) - allowed_fields:
        raise ValueError("Укажите допустимые поля для изменения события.")

    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        events = schedule.setdefault("events", [])
        event_positions = [
            index for index, item in enumerate(events)
            if item.get("type", "event") == "event"
        ]
        if not isinstance(event_index, int) or not 0 <= event_index < len(event_positions):
            raise ValueError("Событие с таким индексом не найдено.")
        storage_index = event_positions[event_index]

        updated = {**events[storage_index], **changes}
        updated["title"] = str(updated.get("title", "") or "").strip()
        updated["description"] = str(updated.get("description", "") or "").strip()
        if not updated["title"]:
            raise ValueError("У события должно быть название.")

        time_fields = _validate_optional_time_fields(updated, schedule)
        updated.pop("start_time_in_minutes", None)
        updated.pop("end_time_in_minutes", None)
        updated.update(time_fields)
        if "day_index" in changes and changes["day_index"] is None:
            updated.pop("day_index", None)
        else:
            day_index = _validate_task_day_index(updated.get("day_index"), schedule)
            if day_index is None:
                updated.pop("day_index", None)
            else:
                updated["day_index"] = day_index

        updated["type"] = "event"
        events[storage_index] = updated
        events.sort(
            key=lambda item: (
                item.get("start_time_in_minutes") is None,
                item.get("start_time_in_minutes", 0) or 0,
            )
        )
        _save_unlocked(schedules)
        return dict(updated)



def delete_event(schedule_id: int, event_index: int) -> None:
    """Удаляет обычное событие по индексу (для совместимости с прежним API)."""
    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        events = schedule.setdefault("events", [])
        event_positions = [
            index for index, item in enumerate(events)
            if item.get("type", "event") == "event"
        ]
        if not isinstance(event_index, int) or not 0 <= event_index < len(event_positions):
            raise ValueError("Событие с таким индексом не найдено.")
        events.pop(event_positions[event_index])
        _save_unlocked(schedules)


# ---------------------------------------------------------------------
# Задачи внутри расписания (type = "task")
# ---------------------------------------------------------------------

def get_all_tasks(schedule_id: int | None = None) -> list[dict[str, Any]]:
    """Возвращает задачи выбранного расписания или из всех расписаний, если ID не задан."""
    with _LOCK:
        schedules = _load_unlocked()
        if schedule_id is not None:
            schedules = [_find_schedule(schedules, schedule_id)]

        tasks: list[dict[str, Any]] = []
        for schedule in schedules:
            for item in schedule.get("events", []):
                if item.get("type") == "task":
                    # schedule_id добавляется только в возвращаемую копию, в JSON он не дублируется.
                    tasks.append({**item, "schedule_id": schedule.get("id")})
        return tasks


def get_task(schedule_id: int, task_id: int) -> dict[str, Any]:
    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        return dict(_find_event(schedule, task_id, event_type="task"))


def create_task(schedule_id: int, data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title", "") or "").strip()
    if not title:
        raise ValueError("У задачи должно быть название.")

    priority = data.get("priority", "medium")
    if priority not in VALID_PRIORITIES:
        raise ValueError("Приоритет должен быть low, medium или high.")

    status = data.get("status", "pending")
    if status not in VALID_STATUSES:
        raise ValueError("Статус должен быть pending, in_progress или completed.")

    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        time_fields = _validate_optional_time_fields(data, schedule)
        day_index = _validate_task_day_index(data.get("day_index"), schedule)
        events = schedule.setdefault("events", [])
        task: dict[str, Any] = {
            "id": _next_event_id(events),
            "type": "task",
            "title": title,
            "description": str(data.get("description", "") or "").strip(),
            "deadline": data.get("deadline"),
            "priority": priority,
            "status": status,
        }
        task.update(time_fields)
        if day_index is not None:
            task["day_index"] = day_index
        events.append(task)
        _save_unlocked(schedules)
        return dict(task)


def update_task(
    schedule_id: int,
    task_id: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    allowed_fields = {
        "title", "description", "deadline", "priority", "status",
        "start_time_in_minutes", "end_time_in_minutes", "day_index",
    }
    if not changes:
        raise ValueError("Не указаны изменения.")
    if set(changes) - allowed_fields:
        raise ValueError("Переданы недопустимые поля задачи.")

    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        task = _find_event(schedule, task_id, event_type="task")
        updated = {**task, **changes}

        if "title" in changes:
            updated["title"] = str(changes["title"] or "").strip()
        if not str(updated.get("title", "")).strip():
            raise ValueError("У задачи должно быть название.")
        if updated.get("priority", "medium") not in VALID_PRIORITIES:
            raise ValueError("Приоритет должен быть low, medium или high.")
        if updated.get("status", "pending") not in VALID_STATUSES:
            raise ValueError("Статус должен быть pending, in_progress или completed.")

        # Значение null сбрасывает одну границу; оставшаяся граница сохраняется.
        time_fields = _validate_optional_time_fields(updated, schedule)
        updated.pop("start_time_in_minutes", None)
        updated.pop("end_time_in_minutes", None)
        updated.update(time_fields)

        if "day_index" in changes and changes["day_index"] is None:
            updated.pop("day_index", None)
        else:
            day_index = _validate_task_day_index(updated.get("day_index"), schedule)
            if day_index is None:
                updated.pop("day_index", None)
            else:
                updated["day_index"] = day_index

        updated["type"] = "task"
        index = schedule["events"].index(task)
        schedule["events"][index] = updated
        _save_unlocked(schedules)
        return dict(updated)




def delete_task(schedule_id: int, task_id: int) -> None:
    """Удаляет задачу, не затрагивая обычные события расписания."""
    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        task = _find_event(schedule, task_id, event_type="task")
        schedule["events"].remove(task)
        _save_unlocked(schedules)


def update_item(schedule_id: int, item_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Обновляет задачу или обычное событие по ID, не смешивая индексы типов."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Не указаны изменения.")

    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        item = _find_event(schedule, item_id)
        item_type = item.get("type", "event")

        if item_type == "task":
            return update_task(schedule_id, item_id, changes)

        event_positions = [
            index for index, candidate in enumerate(schedule.get("events", []))
            if candidate.get("type", "event") == "event"
        ]
        event_index = next(
            (position for position, storage_index in enumerate(event_positions)
             if schedule["events"][storage_index] is item),
            None,
        )
        if event_index is None:
            raise ValueError(f"Событие с ID {item_id} не найдено в этом расписании.")
        return update_event(schedule_id, event_index, changes)


def delete_item(schedule_id: int, item_id: int) -> None:
    """Удаляет задачу или событие по его ID."""
    with _LOCK:
        schedules = _load_unlocked()
        schedule = _find_schedule(schedules, schedule_id)
        item = _find_event(schedule, item_id)
        if item.get("type", "event") == "task":
            delete_task(schedule_id, item_id)
            return

        event_positions = [
            index for index, candidate in enumerate(schedule.get("events", []))
            if candidate.get("type", "event") == "event"
        ]
        event_index = next(
            (position for position, storage_index in enumerate(event_positions)
             if schedule["events"][storage_index] is item),
            None,
        )
        if event_index is None:
            raise ValueError(f"Событие с ID {item_id} не найдено в этом расписании.")
        delete_event(schedule_id, event_index)
