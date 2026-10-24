from __future__ import annotations

from typing import Any, Callable, Sequence

from Application.Database import database


class TaskAgent:
    """Читает любые элементы расписаний и управляет задачами.

    Поддерживаемые действия:
      - get: получить задачи, события или все элементы;
      - create/update/delete: создать, изменить или удалить задачу.

    Обычные события можно получать через get, но их создание/изменение/удаление
    остаётся в функциях событий базы данных и API календаря.
    """

    ITEM_TYPES = {"task", "event", "all"}
    TASK_FIELDS = {
        "title",
        "description",
        "deadline",
        "priority",
        "status",
        "day_index",
        "start_time_in_minutes",
        "end_time_in_minutes",
    }

    def __init__(self, theme_id_file: str | None = None) -> None:
        # Параметр оставлен для совместимости с остальными агентами.
        self.theme_id_file = theme_id_file

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        """Выполняет запрос TaskAgent и возвращает единообразный словарь-ответ."""
        if not isinstance(data, dict):
            return self._error("Запрос должен быть JSON-объектом.")

        action = data.get("action")
        if not isinstance(action, str) or not action.strip():
            return self._error("Не указано действие: get, create, update или delete.")
        action = action.strip().lower()

        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "get": self._get_items,
            "create": self._create_task,
            "update": self._update_task,
            "delete": self._delete_task,
        }
        handler = handlers.get(action)
        if handler is None:
            return self._error(
                f"Неизвестное действие: {action}. Используйте get, create, update или delete."
            )

        try:
            return handler(data)
        except ValueError as exc:
            return self._error(str(exc))
        except (RuntimeError, OSError) as exc:
            # Ошибки чтения/записи JSON тоже возвращаем как контролируемый ответ.
            print(f"[TaskAgent] {type(exc).__name__}: {exc}")
            return self._error(f"Ошибка хранилища данных: {exc}")
        except Exception as exc:
            # Не раскрываем внутренние детали вызывающему API, но оставляем их в логе.
            print(f"[TaskAgent] {type(exc).__name__}: {exc}")
            return self._error("Не удалось выполнить операцию. Подробности есть в консоли.")

    # ------------------------------------------------------------------
    # Получение данных
    # ------------------------------------------------------------------

    def _get_items(self, data: dict[str, Any]) -> dict[str, Any]:
        schedule_id = self._schedule_id(data, required=False)
        raw_item_type = data.get("item_type", "all")
        if not isinstance(raw_item_type, str):
            raise ValueError("item_type должен быть строкой: task, event или all.")
        item_type = raw_item_type.strip().lower()
        if item_type not in self.ITEM_TYPES:
            raise ValueError("item_type должен быть task, event или all.")

        explicit_type = self._explicit_element_type(data)
        has_element_id = self._has_element_id(data)

        if has_element_id and schedule_id is None:
            raise ValueError("Для получения одного элемента укажите schedule_id.")
        if explicit_type is not None and item_type not in {"all", explicit_type}:
            raise ValueError(
                f"ID типа {explicit_type} не соответствует item_type='{item_type}'."
            )

        query_type = explicit_type or item_type
        items = database.get_schedule_items(
            schedule_id=schedule_id,
            item_type=query_type,
        )

        # Фильтры priority/status имеют смысл только для задач. Не игнорируем
        # их молча, если пользователь запросил события или всё расписание.
        if query_type != "task" and any(
            data.get(field) is not None for field in ("priority", "status")
        ):
            raise ValueError("Фильтры priority и status доступны только для item_type='task'.")
        if query_type == "task":
            items = self._filter_tasks(items, data)

        if has_element_id:
            item_id = self._element_id(data)
            item = next((entry for entry in items if self._same_id(entry.get("id"), item_id)), None)
            if item is None:
                kind = explicit_type or (item_type if item_type != "all" else "элемент")
                raise ValueError(
                    f"{kind.capitalize()} с ID {item_id} не найден(а)"
                    + (f" в расписании {schedule_id}." if schedule_id is not None else ".")
                )
            return self._items_response([item], item_type=query_type, schedule_id=schedule_id, single=True)

        return self._items_response(items, item_type=item_type, schedule_id=schedule_id)

    @staticmethod
    def _items_response(
        items: Sequence[dict[str, Any]],
        *,
        item_type: str,
        schedule_id: int | None,
        single: bool = False,
    ) -> dict[str, Any]:
        tasks = [item for item in items if item.get("type") == "task"]
        events = [item for item in items if item.get("type", "event") == "event"]
        result: dict[str, Any] = {
            "success": True,
            "item_type": item_type,
            "schedule_id": schedule_id,
            "items": items,
            "tasks": tasks,
            "events": events,
            "count": len(items),
        }
        if single and items:
            result["item"] = items[0]
            # Алиасы для уже существующих вызовов, которым нужен один объект.
            if items[0].get("type") == "task":
                result["task"] = items[0]
            else:
                result["event"] = items[0]
        return result

    @staticmethod
    def _filter_tasks(items: Sequence[dict[str, Any]], data: dict[str, Any]) -> Sequence[dict[str, Any]]:
        priority = data.get("priority")
        status = data.get("status")

        if priority is not None and (
            not isinstance(priority, str) or priority not in database.VALID_PRIORITIES
        ):
            raise ValueError("Приоритет должен быть low, medium или high.")
        if status is not None and (
            not isinstance(status, str) or status not in database.VALID_STATUSES
        ):
            raise ValueError("Статус должен быть pending, in_progress или completed.")

        if priority is not None:
            items = [item for item in items if item.get("priority", "medium") == priority]
        if status is not None:
            items = [item for item in items if item.get("status", "pending") == status]
        return items

    # ------------------------------------------------------------------
    # Создание, изменение и удаление задач
    # ------------------------------------------------------------------

    def _create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown_fields(data, {"action", "schedule_id", "id", *self.TASK_FIELDS})
        schedule_id = self._schedule_id(data)
        task_data = {
            field: data[field]
            for field in self.TASK_FIELDS
            if field in data
        }
        task_data.setdefault("priority", "medium")
        task_data.setdefault("status", "pending")
        task = database.create_task(schedule_id, task_data)
        return {
            "success": True,
            "message": "Задача создана.",
            "schedule_id": schedule_id,
            "task": {**task, "schedule_id": schedule_id},
        }

    def _update_task(self, data: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown_fields(
            data,
            {"action", "schedule_id", "id", "task_id", "item_id", *self.TASK_FIELDS},
        )
        schedule_id = self._schedule_id(data)
        task_id = self._element_id(data, allowed_keys=("task_id", "item_id"))
        changes = {
            field: data[field]
            for field in self.TASK_FIELDS
            if field in data
        }
        if not changes:
            raise ValueError("Не указаны поля для изменения задачи.")

        task = database.update_task(schedule_id, task_id, changes)
        return {
            "success": True,
            "message": "Задача обновлена.",
            "schedule_id": schedule_id,
            "task": {**task, "schedule_id": schedule_id},
        }

    def _delete_task(self, data: dict[str, Any]) -> dict[str, Any]:
        self._reject_unknown_fields(
            data, {"action", "schedule_id", "id", "task_id", "item_id"}
        )
        schedule_id = self._schedule_id(data)
        task_id = self._element_id(data, allowed_keys=("task_id", "item_id"))
        database.delete_task(schedule_id, task_id)
        return {
            "success": True,
            "message": "Задача удалена.",
            "schedule_id": schedule_id,
            "task_id": task_id,
        }

    # ------------------------------------------------------------------
    # Валидация аргументов
    # ------------------------------------------------------------------

    @staticmethod
    def _schedule_id(data: dict[str, Any], *, required: bool = True) -> int | None:
        value = data.get("schedule_id")
        # Поддерживаем старые тестовые запросы с id, но рекомендуем schedule_id.
        if value is None:
            value = data.get("id")
        if value is None:
            if required:
                raise ValueError("Не указан schedule_id — выберите расписание.")
            return None
        if isinstance(value, bool):
            raise ValueError("schedule_id должен быть целым числом.")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("schedule_id должен быть целым числом.")
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("schedule_id должен быть целым числом.") from exc
        if result <= 0:
            raise ValueError("schedule_id должен быть положительным числом.")
        return result

    @staticmethod
    def _has_element_id(data: dict[str, Any]) -> bool:
        return any(data.get(key) is not None for key in ("item_id", "task_id", "event_id"))

    @staticmethod
    def _explicit_element_type(data: dict[str, Any]) -> str | None:
        has_task_id = data.get("task_id") is not None
        has_event_id = data.get("event_id") is not None
        if has_task_id and has_event_id:
            raise ValueError("Укажите только одно поле: task_id или event_id.")
        if has_task_id:
            return "task"
        if has_event_id:
            return "event"
        return None

    @staticmethod
    def _element_id(
        data: dict[str, Any],
        *,
        allowed_keys: tuple[str, ...] = ("item_id", "task_id", "event_id"),
    ) -> int:
        supplied = [key for key in allowed_keys if data.get(key) is not None]
        if not supplied:
            expected = " или ".join(allowed_keys)
            raise ValueError(f"Не указан ID элемента ({expected}).")
        if len(supplied) > 1:
            raise ValueError(
                "Укажите только одно поле ID: " + ", ".join(supplied) + "."
            )
        value = data[supplied[0]]
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ValueError("ID элемента должен быть целым числом.")
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("ID элемента должен быть целым числом.") from exc
        if result <= 0:
            raise ValueError("ID элемента должен быть положительным числом.")
        return result

    @staticmethod
    def _same_id(left: Any, right: int) -> bool:
        try:
            return int(left) == right
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _reject_unknown_fields(data: dict[str, Any], allowed: set[str]) -> None:
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(
                "Неизвестные поля запроса: " + ", ".join(unknown) + "."
            )

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"success": False, "message": message}
