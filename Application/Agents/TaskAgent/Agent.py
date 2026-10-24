from __future__ import annotations

from typing import Any

from Application.Database import database


class TaskAgent:
    """Управляет задачами, хранящимися в events выбранного расписания."""

    def __init__(self, theme_id_file: str | None = None) -> None:
        # Параметр сохранён для совместимости с другими агентами проекта.
        self.theme_id_file = theme_id_file

    @staticmethod
    def _get_schedule_id(data: dict[str, Any], *, required: bool = True) -> int | None:
        value = data.get("schedule_id")
        if value is None:
            if required:
                raise ValueError("Не указан schedule_id — выберите расписание.")
            return None
        try:
            schedule_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule_id должен быть целым числом.") from exc
        if schedule_id <= 0:
            raise ValueError("schedule_id должен быть положительным числом.")
        return schedule_id

    @staticmethod
    def _get_task_id(data: dict[str, Any]) -> int:
        value = data.get("task_id")
        if value is None:
            raise ValueError("Не указан task_id.")
        try:
            task_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("task_id должен быть целым числом.") from exc
        if task_id <= 0:
            raise ValueError("task_id должен быть положительным числом.")
        return task_id

    def run(self, data: dict[str, Any]) -> dict[str, Any]:
        """Поддерживает действия create, update, delete, get и list."""
        if not isinstance(data, dict):
            return {"success": False, "message": "Запрос должен быть JSON-объектом."}

        action = data.get("action")
        actions = {
            "create": self.create_task,
            "update": self.update_task,
            "delete": self.delete_task,
            "get": self.get_task,
            "list": self.list_items,
        }
        handler = actions.get(action)
        if handler is None:
            return {
                "success": False,
                "message": f"Неизвестное действие: {action}. Используйте create, update, delete, get или list.",
            }

        try:
            return handler(data)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
        except Exception as exc:
            # Подробности остаются в консоли для отладки, клиент получает понятное сообщение.
            print(f"[TaskAgent] {type(exc).__name__}: {exc}")
            return {
                "success": False,
                "message": "Не удалось выполнить операцию с задачей. Подробности есть в консоли.",
            }

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        schedule_id = self._get_schedule_id(data)
        title = str(data.get("title", "") or "").strip()
        if not title:
            raise ValueError("У задачи должно быть название.")

        task_data: dict[str, Any] = {
            "title": title,
            "description": data.get("description", ""),
            "deadline": data.get("deadline"),
            "priority": data.get("priority", "medium"),
            "status": data.get("status", "pending"),
        }
        if "day_index" in data:
            task_data["day_index"] = data["day_index"]
        for field in ("start_time_in_minutes", "end_time_in_minutes"):
            if field in data:
                task_data[field] = data[field]

        task = database.create_task(schedule_id, task_data)
        return {
            "success": True,
            "message": "Задача создана.",
            "schedule_id": schedule_id,
            "task": task,
        }

    def update_task(self, data: dict[str, Any]) -> dict[str, Any]:
        schedule_id = self._get_schedule_id(data)
        task_id = self._get_task_id(data)
        editable_fields = (
            "title", "description", "deadline", "priority", "status",
            "start_time_in_minutes", "end_time_in_minutes", "day_index",
        )
        changes = {field: data[field] for field in editable_fields if field in data}
        if not changes:
            raise ValueError("Не указаны поля для изменения.")

        task = database.update_task(schedule_id, task_id, changes)
        return {
            "success": True,
            "message": "Задача обновлена.",
            "schedule_id": schedule_id,
            "task": task,
        }

    def delete_task(self, data: dict[str, Any]) -> dict[str, Any]:
        schedule_id = self._get_schedule_id(data)
        task_id = self._get_task_id(data)
        database.delete_task(schedule_id, task_id)
        return {
            "success": True,
            "message": "Задача удалена.",
            "schedule_id": schedule_id,
            "task_id": task_id,
        }

    def get_task(self, data: dict[str, Any]) -> dict[str, Any]:
        task_id = data.get("task_id")
        if task_id is not None:
            schedule_id = self._get_schedule_id(data)
            task = database.get_task(schedule_id, self._get_task_id(data))
            return {"success": True, "schedule_id": schedule_id, "task": task}

        schedule_id = self._get_schedule_id(data, required=False)
        tasks = self._filter_tasks(database.get_all_tasks(schedule_id), data)
        return {"success": True, "tasks": tasks, "count": len(tasks)}

    def list_items(self, data: dict[str, Any]) -> dict[str, Any]:
        schedule_id = self._get_schedule_id(data, required=False)

        item_type = data.get("item_type", "task")

        if item_type not in {"task", "event", "all"}:
            raise ValueError(
                "item_type должен быть task, event или all."
            )

        items = database.get_schedule_items(
            schedule_id=schedule_id,
            item_type=item_type,
        )

        # Фильтрация по статусу и приоритету применима к задачам.
        if item_type == "task":
            items = self._filter_tasks(items, data)

        tasks = [
            item for item in items
            if item.get("type") == "task"
        ]

        events = [
            item for item in items
            if item.get("type") == "event"
        ]

        return {
            "success": True,
            "item_type": item_type,
            "items": items,
            "tasks": tasks,
            "events": events,
            "count": len(items),
        }

    @staticmethod
    def _filter_tasks(tasks: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
        priority = data.get("priority")
        status = data.get("status")

        if priority is not None:
            if priority not in database.VALID_PRIORITIES:
                raise ValueError("Приоритет должен быть low, medium или high.")
            tasks = [task for task in tasks if task.get("priority", "medium") == priority]
        if status is not None:
            if status not in database.VALID_STATUSES:
                raise ValueError("Статус должен быть pending, in_progress или completed.")
            tasks = [task for task in tasks if task.get("status", "pending") == status]
        return tasks
