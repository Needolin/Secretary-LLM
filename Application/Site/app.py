from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

# Добавляем корень проекта в путь импорта, чтобы запуск из корня работал стабильно.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Application.Agents.OrchestratorAgent.Agent import OrchestratorAgent
from Application.Agents.TaskAgent.Agent import TaskAgent
from Application.Database.database import (
    ensure_data_file,
    load_schedules,
    get_schedule,
    create_schedule,
    delete_schedule,
    add_event,
    get_events,
    get_all_tasks,
    update_item,
    delete_item,
    toggle_schedule_pin,
)
from Application.Database.chat_database import (
    add_chat_message,
    create_chat,
    delete_chat,
    delete_chats_for_schedule,
    get_chat,
    load_chats,
    toggle_chat_pin,
)

SITE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = SITE_DIR / "Frontend"

# HTML-шаблон и CSS находятся в существующей папке Frontend.
app = Flask(
    __name__,
    template_folder=str(FRONTEND_DIR),
    static_folder=str(FRONTEND_DIR),
    static_url_path="/static",
)
app.secret_key = "dev-only-change-this-key"

orchestrator = OrchestratorAgent()
task_agent = TaskAgent()

PERIOD_LABELS = {"Day": "День", "Week": "Неделя", "Month": "Месяц"}
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MINUTES_PER_DAY = 1440
PERIOD_LENGTH = {"Day": 1, "Week": 7, "Month": 28}


def to_minutes(value: str) -> int:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError("Укажите время в формате ЧЧ:ММ.") from exc
    return parsed.hour * 60 + parsed.minute


def build_rows(schedule: dict[str, Any], period: str) -> list[dict[str, Any]]:
    """Строит строки выбранного расписания: события — неоновые линии, дедлайны — крестики."""
    if period == "Day":
        labels = ["Сегодня"]
    elif period == "Week":
        labels = WEEKDAYS.copy()
    else:
        labels = [f"День {i}" for i in range(1, 29)]

    storage_period = schedule.get("period", "Week")
    raw_items = schedule.get("events", [])

    def optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def date_for_item(item: dict[str, Any]) -> date | None:
        value = item.get("deadline")
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None

    def explicit_day(item: dict[str, Any]) -> int | None:
        day_index = optional_int(item.get("day_index"))
        max_days = {"Day": 1, "Week": 7, "Month": 28}.get(storage_period, 7)
        if day_index is not None and 0 <= day_index < max_days:
            return day_index
        due = date_for_item(item)
        if due is None:
            return None
        if storage_period == "Week":
            return due.weekday()
        if storage_period == "Month":
            return min(due.day - 1, 27)
        return 0

    has_undated = any(
        item.get("type", "event") == "task"
        and explicit_day(item) is None
        and optional_int(item.get("start_time_in_minutes")) is None
        and optional_int(item.get("end_time_in_minutes")) is None
        for item in raw_items
    )
    no_day_row_index = None
    if has_undated and storage_period in {"Week", "Month"}:
        no_day_row_index = len(labels)
        labels.append("Без даты")

    short_names = {
        "Понедельник": "Пн", "Вторник": "Вт", "Среда": "Ср",
        "Четверг": "Чт", "Пятница": "Пт", "Суббота": "Сб",
        "Воскресенье": "Вс", "Сегодня": "Сегодня", "Без даты": "Без даты",
    }

    result: list[dict[str, Any]] = []
    for row_index, label in enumerate(labels):
        row_items: list[dict[str, Any]] = []
        for item in raw_items:
            is_deadline = item.get("type", "event") == "task"
            start = optional_int(item.get("start_time_in_minutes"))
            end = optional_int(item.get("end_time_in_minutes"))

            if storage_period == "Day":
                item_day = 0
            elif is_deadline:
                item_day = explicit_day(item)
                if item_day is None and start is not None:
                    item_day = start // MINUTES_PER_DAY
                elif item_day is None and end is not None:
                    item_day = max(0, end - 1) // MINUTES_PER_DAY
                if item_day is None:
                    item_day = no_day_row_index if no_day_row_index is not None else 0
            elif start is not None:
                item_day = start // MINUTES_PER_DAY
            elif end is not None:
                item_day = max(0, end - 1) // MINUTES_PER_DAY
            else:
                item_day = optional_int(item.get("day_index"))
                if item_day is None:
                    item_day = 0

            if item_day != row_index:
                continue

            if is_deadline:
                # Дата дедлайна отмечается в конце дня; старые записи с одним
                # временем сохраняют позицию этого времени, а записи без даты
                # и времени показываются по центру строки «Без даты».
                mode = "deadline"
                width_percent = 0
                due_date = date_for_item(item)
                anchor = start if start is not None else end
                if due_date is not None or (optional_int(item.get("day_index")) is not None and anchor is None):
                    left_percent = 97.0
                elif anchor is not None:
                    local_anchor = anchor if storage_period == "Day" else anchor - row_index * MINUTES_PER_DAY
                    left_percent = round(max(0, min(MINUTES_PER_DAY, local_anchor)) / MINUTES_PER_DAY * 100, 4)
                else:
                    left_percent = 50.0
                start_label = "" if start is None else f"{(start % MINUTES_PER_DAY) // 60:02d}:{(start % 60):02d}"
                end_label = "" if end is None else f"{(end % MINUTES_PER_DAY) // 60:02d}:{(end % 60):02d}"
            else:
                valid_range = (
                    start is not None and end is not None
                    and end > start
                    and start // MINUTES_PER_DAY == (end - 1) // MINUTES_PER_DAY
                )
                if valid_range:
                    start_local = start if storage_period == "Day" else start - row_index * MINUTES_PER_DAY
                    end_local = end if storage_period == "Day" else end - row_index * MINUTES_PER_DAY
                    if 0 <= start_local < MINUTES_PER_DAY and 0 < end_local <= MINUTES_PER_DAY:
                        left_percent = round(start_local / MINUTES_PER_DAY * 100, 4)
                        width_percent = round((end_local - start_local) / MINUTES_PER_DAY * 100, 4)
                        mode = "range"
                        start_label = f"{start_local // 60:02d}:{start_local % 60:02d}"
                        end_label = "24:00" if end_local == MINUTES_PER_DAY else f"{end_local // 60:02d}:{end_local % 60:02d}"
                    else:
                        valid_range = False
                if not valid_range:
                    anchor = start if start is not None else end
                    if anchor is None:
                        anchor_local = MINUTES_PER_DAY // 2
                    else:
                        anchor_local = anchor if storage_period == "Day" else anchor - row_index * MINUTES_PER_DAY
                    left_percent = round(max(0, min(MINUTES_PER_DAY, anchor_local)) / MINUTES_PER_DAY * 100, 4)
                    width_percent = 0
                    mode = "marker"
                    start_label = "" if start is None else f"{(start % MINUTES_PER_DAY) // 60:02d}:{(start % 60):02d}"
                    end_label = "" if end is None else f"{(end % MINUTES_PER_DAY) // 60:02d}:{(end % 60):02d}"

            row_items.append({
                **item,
                "mode": mode,
                "start_label": start_label,
                "end_label": end_label,
                "left_percent": left_percent,
                "width_percent": width_percent,
                "deadline_label": str(item.get("deadline") or ""),
                "color": item.get("color") or ("#ff4fd8" if is_deadline else "#4df6d2"),
            })

        result.append({
            "label": label,
            "short_label": short_names.get(label, label.removeprefix("День ")),
            "index": row_index,
            "events": row_items,
        })
    return result

def execute_command(
    schedule: dict[str, Any],
    command: str,
    chat_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Передаёт команду оркестратору и возвращает полный результат выполнения."""
    payload = {
        "schedule_id": schedule["id"],
        "command": command,
        "schedule": schedule,
        "chat_history": chat_history or [],
    }
    try:
        result = orchestrator.run(payload)
    except Exception as exc:
        app.logger.exception("Ошибка при передаче команды оркестратору")
        return {
            "status": "error",
            "success": False,
            "message": f"Не удалось обработать команду: {exc}",
            "agents_called": [],
        }

    if not isinstance(result, dict):
        app.logger.error("OrchestratorAgent вернул неожиданный ответ: %r", result)
        return {
            "status": "error",
            "success": False,
            "message": "Оркестратор вернул некорректный ответ. Подробности есть в консоли.",
            "agents_called": [],
        }
    return result


def send_command(schedule: dict[str, Any], command: str) -> tuple[bool, str]:
    """Совместимость со старыми HTML/API обработчиками команд."""
    result = execute_command(schedule, command)
    return result.get("status") == "ok", str(
        result.get("message", "Нет сообщения от оркестратора.")
    )


@app.get("/")
def index():
    try:
        schedules = load_schedules()
    except RuntimeError as exc:
        flash(str(exc), "error")
        schedules = []

    requested_id = request.args.get("schedule_id", type=int)
    selected = next((s for s in schedules if s.get("id") == requested_id), None)

    if selected is None and schedules:
        selected = schedules[0]

    # Период является частью расписания и выбирается только при его создании.
    period = selected.get("period", "Week") if selected else "Week"

    rows = build_rows(selected, period) if selected else []
    
    schedule_period = selected.get("period", "Week") if selected else period
    if schedule_period == "Day":
        day_options = [{"value": 0, "label": "Сегодня"}]
    elif schedule_period == "Week":
        day_options = [{"value": i, "label": name} for i, name in enumerate(WEEKDAYS)]
    else:
        day_options = [{"value": i, "label": f"День {i + 1}"} for i in range(28)]

    schedules = sorted(schedules, key=lambda item: (not item.get("pinned", False), item.get("name", "").lower()))
    chats = load_chats(selected.get("id") if selected else None)
    for chat in chats:
        chat["schedule_name"] = selected.get("name", "Расписание") if selected else "Расписание"
        chat["schedule_period"] = selected.get("period", "Week") if selected else "Week"

    requested_chat_id = request.args.get("chat_id", type=int)
    selected_chat = next((chat for chat in chats if chat.get("id") == requested_chat_id), None)
    if selected_chat is None and chats:
        selected_chat = chats[0]

    chat_messages = selected_chat.get("messages", []) if selected_chat else []
    return render_template(
        "index.html",
        schedules=schedules,
        selected=selected,
        period=period,
        schedule_period=schedule_period,
        period_labels=PERIOD_LABELS,
        rows=rows,
        day_options=day_options,
        chats=chats,
        selected_chat=selected_chat,
        chat_messages=chat_messages,
        event_count=sum(1 for item in selected.get("events", []) if item.get("type", "event") == "event") if selected else 0,
        deadline_count=sum(1 for item in selected.get("events", []) if item.get("type") == "task") if selected else 0,
        total_count=len(selected.get("events", [])) if selected else 0,
        backend_url="GPT-5 Mini · Cloud.ru Foundation Models",
    )


@app.post("/chats")
def create_chat_form():
    schedule_id = request.form.get("schedule_id", type=int)
    if not schedule_id:
        flash("Сначала выберите расписание для нового чата.", "error")
        return redirect(url_for("index"))
    try:
        chat = create_chat(schedule_id)
        schedule = get_schedule(schedule_id)
        return redirect(url_for(
            "index",
            schedule_id=schedule_id,
            period=schedule.get("period", "Week"),
            chat_id=chat["id"],
        ))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index", schedule_id=schedule_id))


@app.post("/chats/<int:chat_id>/pin")
def pin_chat_form(chat_id: int):
    try:
        chat = toggle_chat_pin(chat_id)
        schedule = get_schedule(chat["schedule_id"])
        return redirect(url_for(
            "index",
            schedule_id=chat["schedule_id"],
            period=schedule.get("period", "Week"),
            chat_id=chat_id,
        ))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))


@app.post("/chats/<int:chat_id>/delete")
def delete_chat_form(chat_id: int):
    try:
        chat = get_chat(chat_id)
        schedule_id = chat["schedule_id"]
        delete_chat(chat_id)
        flash("Чат удалён.", "success")
        schedule = get_schedule(schedule_id)
        return redirect(url_for(
            "index",
            schedule_id=schedule_id,
            period=schedule.get("period", "Week"),
        ))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))


@app.post("/chats/<int:chat_id>/messages")
def chat_message_form(chat_id: int):
    command = request.form.get("command", "").strip()
    try:
        chat = get_chat(chat_id)
        schedule = get_schedule(chat["schedule_id"])
        if not command:
            raise ValueError("Введите сообщение.")

        # История перед текущей репликой, чтобы LLM могла понимать продолжение диалога.
        history = [
            {"role": message.get("role"), "content": message.get("content", "")}
            for message in chat.get("messages", [])[-8:]
            if message.get("role") in {"user", "assistant"}
        ]
        add_chat_message(chat_id, "user", command)
        result = execute_command(schedule, command, history)
        answer = str(result.get("message", "Не удалось получить ответ от оркестратора."))
        add_chat_message(
            chat_id,
            "assistant",
            answer,
            metadata={
                "status": result.get("status", "unknown"),
                "intent": result.get("intent"),
                "agents_called": result.get("agents_called", []),
            },
        )
    except ValueError as exc:
        flash(str(exc), "error")
    except RuntimeError as exc:
        app.logger.exception("Ошибка при обработке сообщения чата")
        flash(str(exc), "error")

    return redirect(url_for(
        "index",
        schedule_id=chat.get("schedule_id") if "chat" in locals() else None,
        period=schedule.get("period", "Week") if "schedule" in locals() else None,
        chat_id=chat_id,
    ))


@app.post("/schedules")
def create_schedule_form():
    try:
        schedule = create_schedule(
            request.form.get("name", ""),
            request.form.get("period", "Week"),
        )
        flash("Расписание создано.", "success")
        return redirect(url_for(
            "index",
            schedule_id=schedule["id"],
            period=schedule["period"],
        ))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))


@app.post("/schedules/<int:schedule_id>/pin")
def pin_schedule_form(schedule_id: int):
    try:
        toggle_schedule_pin(schedule_id)
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("index", schedule_id=schedule_id))


@app.post("/schedules/<int:schedule_id>/delete")
def delete_schedule_form(schedule_id: int):
    try:
        delete_schedule(schedule_id)
        delete_chats_for_schedule(schedule_id)
        flash("Расписание и связанные чаты удалены.", "success")
    except ValueError as exc:
        flash(str(exc), "error")

    return redirect(url_for("index"))


@app.post("/events")
def add_event_form():
    raw_id = request.form.get("schedule_id", "")

    try:
        schedule_id = int(raw_id)
        schedule = get_schedule(schedule_id)

        day_index = int(request.form.get("day_index", "0"))
        period = schedule.get("period", "Week")

        if period not in PERIOD_LENGTH:
            raise ValueError("Неизвестный формат расписания.")

        if not 0 <= day_index < PERIOD_LENGTH[period]:
            raise ValueError("Выбранный день не входит в расписание.")

        start_value = request.form.get("start_time", "").strip()
        end_value = request.form.get("end_time", "").strip()
        event_data: dict[str, Any] = {
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
            "day_index": day_index,
            "color": request.form.get("color", "#4df6d2").strip(),
        }

        start = to_minutes(start_value) if start_value else None
        end = to_minutes(end_value) if end_value else None
        if start is None or end is None:
            raise ValueError("Для события укажите время начала и окончания.")
        if end <= start:
            raise ValueError("Время окончания должно быть позже времени начала.")
        if start is not None:
            event_data["start_time_in_minutes"] = day_index * MINUTES_PER_DAY + start
        if end is not None:
            event_data["end_time_in_minutes"] = day_index * MINUTES_PER_DAY + end

        add_event(schedule_id, event_data)

        flash("Событие добавлено.", "success")
        return redirect(url_for(
            "index",
            schedule_id=schedule_id,
            period=period,
        ))

    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("index", schedule_id=raw_id or None))


@app.post("/deadlines")
def add_deadline_form():
    schedule_id = request.form.get("schedule_id", type=int)
    try:
        if not schedule_id:
            raise ValueError("Сначала выберите расписание.")
        get_schedule(schedule_id)
        title = request.form.get("title", "").strip()
        due_date = request.form.get("deadline", "").strip()
        description = request.form.get("description", "").strip()
        color = request.form.get("color", "#ff4fd8").strip()
        if not title:
            raise ValueError("У дедлайна должно быть название.")
        if not due_date:
            raise ValueError("Укажите дату дедлайна.")
        result = task_agent.run({
            "action": "create",
            "schedule_id": schedule_id,
            "title": title,
            "description": description,
            "deadline": due_date,
            "color": color,
        })
        if not result.get("success"):
            raise ValueError(result.get("message", "Не удалось создать дедлайн."))
        flash("Дедлайн добавлен.", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("index", schedule_id=schedule_id))


@app.post("/commands")
def command_form():
    command = request.form.get("command", "").strip()
    schedule_id = request.form.get("schedule_id", type=int)

    try:
        schedule = get_schedule(schedule_id) if schedule_id else None
    except ValueError:
        schedule = None

    if not schedule:
        flash("Сначала создайте или выберите расписание.", "error")
    elif not command:
        flash("Введите команду.", "error")
    else:
        ok, message = send_command(schedule, command)
        flash(message, "success" if ok else "error")
    return redirect(url_for("index", schedule_id=schedule_id, period=request.form.get("period", "Week")))


@app.get("/api/schedules")
def api_list_schedules():
    try:
        return jsonify(load_schedules())
    except RuntimeError as exc:
        app.logger.exception("Не удалось прочитать расписания")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/schedules")
def api_create_schedule():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Тело запроса должно быть JSON-объектом."}), 400
    try:
        schedule = create_schedule(str(data.get("name", "")), str(data.get("period", "Week")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(schedule), 201


@app.post("/api/command")
def api_command():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Тело запроса должно быть JSON-объектом."}), 400

    try:
        schedule_id = int(data.get("schedule_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "schedule_id должен быть целым числом."}), 400

    command = str(data.get("command", "")).strip()
    if not command:
        return jsonify({"error": "Команда не должна быть пустой."}), 400

    try:
        schedule = get_schedule(schedule_id)
    except ValueError:
        return jsonify({"error": "Расписание не найдено."}), 404

    if not schedule:
        return jsonify({"error": "Расписание не найдено."}), 404
    
    ok, message = send_command(schedule, command)
    return jsonify({"message": message, "ok": ok}), 200 if ok else 501


@app.post("/api/schedules/<int:schedule_id>/pin")
def api_toggle_schedule_pin(schedule_id: int):
    try:
        return jsonify(toggle_schedule_pin(schedule_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.delete("/api/schedules/<int:schedule_id>")
def api_delete_schedule(schedule_id: int):
    try:
        delete_schedule(schedule_id)
        delete_chats_for_schedule(schedule_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"success": True})


@app.get("/api/schedules/<int:schedule_id>")
def api_get_schedule(schedule_id: int):
    try:
        return jsonify(get_schedule(schedule_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.get("/api/schedules/<int:schedule_id>/events")
def api_list_events(schedule_id: int):
    try:
        return jsonify(get_events(schedule_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.patch("/api/schedules/<int:schedule_id>/items/<int:item_id>")
def api_update_timeline_item(schedule_id: int, item_id: int):
    """Сохраняет перетаскивание или изменения из контекстного окна таймлайна."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Тело запроса должно быть JSON-объектом."}), 400

    try:
        item = update_item(schedule_id, item_id, data)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "не найдено" in message.lower() else 400
        return jsonify({"error": message}), status_code
    except RuntimeError as exc:
        app.logger.exception("Не удалось обновить элемент таймлайна")
        return jsonify({"error": str(exc)}), 500

    return jsonify({"success": True, "item": item})


@app.delete("/api/schedules/<int:schedule_id>/items/<int:item_id>")
def api_delete_timeline_item(schedule_id: int, item_id: int):
    """Удаляет обычное событие или задачу по ID."""
    try:
        delete_item(schedule_id, item_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "не найдено" in message.lower() else 400
        return jsonify({"error": message}), status_code
    except RuntimeError as exc:
        app.logger.exception("Не удалось удалить элемент таймлайна")
        return jsonify({"error": str(exc)}), 500

    return jsonify({"success": True, "deleted_id": item_id})


@app.get("/api/tasks")
def api_get_tasks():
    raw_schedule_id = request.args.get("schedule_id")
    try:
        schedule_id = int(raw_schedule_id) if raw_schedule_id is not None else None
        tasks = get_all_tasks(schedule_id)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(tasks)


@app.post("/api/task-agent")
def api_task_agent():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "Тело запроса должно быть JSON-объектом."}), 400

    result = task_agent.run(data)
    return jsonify(result), 200 if result.get("success") else 400


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "secretary-flask"})


if __name__ == "__main__":
    ensure_data_file()
    app.run(host="127.0.0.1", port=5000, debug=True)
