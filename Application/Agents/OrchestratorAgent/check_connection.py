"""Диагностическая проверка Cloud.ru Foundation Models.

Показывает, на каком этапе возникла ошибка, HTTP-статус и тело ответа
сервера. API-ключ никуда не печатается.
"""

from __future__ import annotations


def main() -> int:
    stage = "загрузка настроек"
    try:
        from openai import OpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        from .llm_config import create_chat_model, load_cloud_settings

        api_key, base_url, model_name = load_cloud_settings()
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=0)

        stage = "получение списка моделей (GET /v1/models)"
        available_models = {model.id for model in client.models.list().data}

        if model_name not in available_models:
            qwen_models = sorted(name for name in available_models if "qwen" in name.lower())
            print(f"API доступен, но модель {model_name!r} не найдена в каталоге аккаунта.")
            print("Доступные модели Qwen:", qwen_models)
            print("Откройте AI Factory → Foundation Models → Модели и укажите точный ID модели в .env.")
            return 1

        stage = f"отправка тестового запроса модели {model_name!r} (POST /v1/chat/completions)"
        llm = create_chat_model()
        response = llm.invoke([
            SystemMessage(content="Отвечай кратко по-русски."),
            HumanMessage(content="Ответь одним словом: работает?"),
        ])

        print("Подключение к Cloud.ru успешно.")
        print("Модель:", model_name)
        print("Ответ модели:", response.content)
        return 0

    except Exception as exc:
        print(f"Проверка не пройдена на этапе: {stage}")
        print(f"Тип ошибки: {type(exc).__name__}")
        print(f"Описание: {exc}")

        status_code = getattr(exc, "status_code", None)
        response_obj = getattr(exc, "response", None)
        if response_obj is not None:
            status_code = getattr(response_obj, "status_code", status_code)

        if status_code is not None:
            print(f"HTTP status: {status_code}")

        body = getattr(exc, "body", None)
        if body:
            print("Тело ошибки:")
            print(str(body)[:4000])

        if response_obj is not None:
            try:
                response_text = response_obj.text
                if response_text:
                    print("Ответ сервера:")
                    print(response_text[:4000])
            except Exception:
                pass

            try:
                request_id = response_obj.headers.get("x-request-id") or response_obj.headers.get("request-id")
                if request_id:
                    print("Request ID:", request_id)
            except Exception:
                pass

        if status_code == 402:
            print("\nHTTP 402 означает Payment Required. Проверьте баланс/бонусы и статус договора в Cloud.ru.")
        elif status_code in (401, 403):
            print("\nПроверьте, что ключ активен, создан для Foundation Models и разрешён для нужного проекта.")

        print("\nAPI-ключ не выводился. Не публикуйте его и не присылайте в чат.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
