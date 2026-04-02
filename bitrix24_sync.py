"""
Bitrix24 Sync — работа с задачами и проектами через REST API.

Использование:
  python bitrix24_sync.py tasks list
  python bitrix24_sync.py tasks list --project-id 5
  python bitrix24_sync.py tasks create --title "Новая задача" --project-id 5
  python bitrix24_sync.py tasks update --task-id 123 --status done
  python bitrix24_sync.py projects list
"""

import os
import sys
import json
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()

# --- Статусы задач Bitrix24 ---
TASK_STATUSES = {
    "new":          2,   # Не начата
    "pending":      3,   # Ждёт выполнения
    "in_progress":  4,   # Выполняется
    "review":       5,   # Ждёт контроля
    "done":         5,   # Завершена (complete)
    "deferred":     6,   # Отложена
    "declined":     7,   # Отклонена
}

STATUS_LABELS = {
    2: "Не начата",
    3: "Ждёт выполнения",
    4: "Выполняется",
    5: "Ждёт контроля",
    6: "Отложена",
    7: "Отклонена",
}


class Bitrix24Client:
    """Клиент для Bitrix24 REST API через вебхук."""

    def __init__(self, webhook_url: str):
        """
        webhook_url: полный URL вебхука, например
            https://your-domain.bitrix24.ru/rest/1/abcdef123456/
        """
        self.webhook_url = webhook_url.rstrip("/")

    def _call(self, method: str, params: dict = None) -> dict:
        url = f"{self.webhook_url}/{method}.json"
        response = requests.post(url, json=params or {}, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"Bitrix24 API error: {data['error']} — {data.get('error_description', '')}")
        return data.get("result", data)

    # ------------------------------------------------------------------ #
    #  Задачи                                                              #
    # ------------------------------------------------------------------ #

    def get_tasks(self, project_id: int = None, responsible_id: int = None) -> list:
        """Получить список задач."""
        filter_params = {}
        if project_id:
            filter_params["GROUP_ID"] = project_id
        if responsible_id:
            filter_params["RESPONSIBLE_ID"] = responsible_id

        params = {
            "filter": filter_params,
            "select": ["ID", "TITLE", "STATUS", "DEADLINE",
                       "RESPONSIBLE_ID", "GROUP_ID", "DESCRIPTION",
                       "CREATED_BY", "DATE_START"],
            "order": {"CREATED_DATE": "DESC"},
        }

        all_tasks = []
        start = 0
        while True:
            params["start"] = start
            result = self._call("tasks.task.list", params)
            tasks = result.get("tasks", [])
            all_tasks.extend(tasks)
            if len(tasks) < 50:
                break
            start += 50

        return all_tasks

    def get_task(self, task_id: int) -> dict:
        """Получить одну задачу по ID."""
        result = self._call("tasks.task.get", {"taskId": task_id})
        return result.get("task", result)

    def create_task(
        self,
        title: str,
        description: str = "",
        project_id: int = None,
        responsible_id: int = None,
        deadline: str = None,
    ) -> dict:
        """
        Создать задачу.
        deadline: строка формата "2026-12-31T18:00:00+03:00"
        """
        fields = {"TITLE": title, "DESCRIPTION": description}
        if project_id:
            fields["GROUP_ID"] = project_id
        if responsible_id:
            fields["RESPONSIBLE_ID"] = responsible_id
        if deadline:
            fields["DEADLINE"] = deadline

        result = self._call("tasks.task.add", {"fields": fields})
        return result.get("task", result)

    def update_task(self, task_id: int, fields: dict) -> dict:
        """Обновить поля задачи."""
        result = self._call("tasks.task.update", {"taskId": task_id, "fields": fields})
        return result

    def complete_task(self, task_id: int) -> dict:
        """Завершить задачу."""
        return self._call("tasks.task.complete", {"taskId": task_id})

    def set_task_status(self, task_id: int, status: str) -> dict:
        """Установить статус задачи по текстовому названию."""
        if status == "done":
            return self.complete_task(task_id)
        status_code = TASK_STATUSES.get(status)
        if status_code is None:
            valid = ", ".join(TASK_STATUSES.keys())
            raise ValueError(f"Неизвестный статус '{status}'. Допустимые: {valid}")
        return self.update_task(task_id, {"STATUS": status_code})

    # ------------------------------------------------------------------ #
    #  Проекты (рабочие группы)                                           #
    # ------------------------------------------------------------------ #

    def get_projects(self) -> list:
        """Получить список рабочих групп/проектов."""
        all_groups = []
        start = 0
        while True:
            result = self._call("sonet_group.get", {
                "filter": {"IS_EXTRANET": "N"},
                "select": ["ID", "NAME", "DESCRIPTION", "DATE_CREATE",
                           "OWNER_ID", "CLOSED", "VISIBLE"],
                "order": {"DATE_CREATE": "DESC"},
                "start": start,
            })
            groups = result if isinstance(result, list) else result.get("groups", [])
            all_groups.extend(groups)
            if len(groups) < 50:
                break
            start += 50
        return all_groups


# ------------------------------------------------------------------ #
#  CLI                                                                #
# ------------------------------------------------------------------ #

def print_tasks(tasks: list):
    if not tasks:
        print("Задач не найдено.")
        return
    print(f"\n{'ID':<8} {'Статус':<20} {'Дедлайн':<22} {'Название'}")
    print("-" * 90)
    for t in tasks:
        status_code = int(t.get("status", 0))
        status = STATUS_LABELS.get(status_code, str(status_code))
        deadline = t.get("deadline", "—") or "—"
        if deadline and len(deadline) > 19:
            deadline = deadline[:19]
        print(f"{t['id']:<8} {status:<20} {deadline:<22} {t.get('title', '')}")


def print_projects(projects: list):
    if not projects:
        print("Проектов не найдено.")
        return
    print(f"\n{'ID':<8} {'Статус':<12} {'Название'}")
    print("-" * 70)
    for g in projects:
        closed = "Закрыт" if g.get("CLOSED") == "Y" else "Активен"
        print(f"{g.get('ID', ''):<8} {closed:<12} {g.get('NAME', '')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bitrix24 Sync — управление задачами и проектами"
    )
    sub = parser.add_subparsers(dest="entity", required=True)

    # --- tasks ---
    tasks_p = sub.add_parser("tasks", help="Работа с задачами")
    tasks_sub = tasks_p.add_subparsers(dest="action", required=True)

    t_list = tasks_sub.add_parser("list", help="Список задач")
    t_list.add_argument("--project-id", type=int, help="Фильтр по ID проекта")
    t_list.add_argument("--responsible-id", type=int, help="Фильтр по ответственному")

    t_get = tasks_sub.add_parser("get", help="Одна задача по ID")
    t_get.add_argument("--task-id", type=int, required=True)

    t_create = tasks_sub.add_parser("create", help="Создать задачу")
    t_create.add_argument("--title", required=True)
    t_create.add_argument("--description", default="")
    t_create.add_argument("--project-id", type=int)
    t_create.add_argument("--responsible-id", type=int)
    t_create.add_argument("--deadline", help="Формат: 2026-12-31T18:00:00+03:00")

    t_update = tasks_sub.add_parser("update", help="Обновить статус задачи")
    t_update.add_argument("--task-id", type=int, required=True)
    t_update.add_argument(
        "--status",
        required=True,
        choices=list(TASK_STATUSES.keys()),
        help="Новый статус",
    )

    # --- projects ---
    projects_p = sub.add_parser("projects", help="Работа с проектами")
    proj_sub = projects_p.add_subparsers(dest="action", required=True)
    proj_sub.add_parser("list", help="Список проектов")

    return parser


def get_client() -> Bitrix24Client:
    webhook = os.getenv("BITRIX24_WEBHOOK_URL")
    if not webhook:
        print(
            "Ошибка: переменная окружения BITRIX24_WEBHOOK_URL не задана.\n"
            "Скопируйте .env.example → .env и укажите ваш webhook URL.",
            file=sys.stderr,
        )
        sys.exit(1)
    return Bitrix24Client(webhook)


def main():
    parser = build_parser()
    args = parser.parse_args()
    client = get_client()

    if args.entity == "tasks":
        if args.action == "list":
            tasks = client.get_tasks(
                project_id=getattr(args, "project_id", None),
                responsible_id=getattr(args, "responsible_id", None),
            )
            print_tasks(tasks)

        elif args.action == "get":
            task = client.get_task(args.task_id)
            print(json.dumps(task, ensure_ascii=False, indent=2))

        elif args.action == "create":
            task = client.create_task(
                title=args.title,
                description=args.description,
                project_id=getattr(args, "project_id", None),
                responsible_id=getattr(args, "responsible_id", None),
                deadline=getattr(args, "deadline", None),
            )
            print(f"Задача создана: ID={task.get('id', task)}")

        elif args.action == "update":
            client.set_task_status(args.task_id, args.status)
            print(f"Статус задачи #{args.task_id} обновлён → {args.status}")

    elif args.entity == "projects":
        if args.action == "list":
            projects = client.get_projects()
            print_projects(projects)


if __name__ == "__main__":
    main()
