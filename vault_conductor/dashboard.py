from __future__ import annotations

import json
import subprocess
import sys
import time

from .config import load_config
from .tasks import read_all_task_notes

STATUS_ICON = {
    "backlog": "○",
    "ready": "◎",
    "running": "▶",
    "needs-human": "●",
    "review-diff": "◈",
    "needs-revision": "↻",
    "pr-opened": "PR",
    "done": "✓",
    "failed": "!",
    "parked": "P",
}

ORDER = ["needs-human", "running", "review-diff", "needs-revision", "pr-opened", "ready", "backlog", "failed", "parked", "done"]

DIM = "\033[90m"
CYAN = "\033[36m"
BLUE = "\033[34m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"
CLEAR_SCREEN = "\033[H\033[2J\033[3J"

STATUS_COLOR = {
    "backlog": DIM,
    "ready": CYAN,
    "running": BLUE,
    "needs-human": YELLOW,
    "review-diff": MAGENTA,
    "needs-revision": YELLOW,
    "pr-opened": MAGENTA,
    "done": GREEN,
    "failed": YELLOW,
    "parked": DIM,
}


def read_tasks():
    config = load_config()
    tasks = []
    for note in read_all_task_notes(config):
        task = note.frontmatter
        tasks.append(
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "repo": task.repo,
                "agent": task.agent,
                "workspace_ref": task.workspace_ref,
                "current_run": task.current_run,
            }
        )
    tasks.sort(key=lambda item: (ORDER.index(item["status"]) if item["status"] in ORDER else 99, item["id"]))
    return tasks


def read_notifications() -> list[dict]:
    try:
        result = subprocess.run(["cmux", "--json", "list-notifications"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout).get("notifications", [])
    except Exception:
        return []
    return []


def truncate(value: str, length: int) -> str:
    return f"{value[: length - 1]}..." if len(value) > length else value


def render(tasks: list[dict], notifications: list[dict]) -> str:
    width = 54
    lines = [
        f"{BOLD}Agent Control Room{RESET}  {DIM}{time.strftime('%H:%M:%S')}{RESET}",
        "-" * width,
    ]
    if not tasks:
        lines.append(f"{DIM}  No tasks yet. Use conductor new.{RESET}")
    else:
        for task in tasks:
            status = task["status"]
            icon = STATUS_ICON.get(status, "?")
            color = STATUS_COLOR.get(status, "")
            ws = f" {DIM}[{task['workspace_ref']}]{RESET}" if task.get("workspace_ref") else ""
            title = truncate(task["title"], 32)
            lines.append(f"  {color}{icon}{RESET} {BOLD}{task['id']}{RESET} {title}{ws}")
            lines.append(f"    {DIM}{task['repo']} · {task['agent']} · {status}{RESET}")
            lines.append("")
    if notifications:
        lines.append("-" * width)
        lines.append(f"{BOLD}Notifications{RESET}")
        for notification in notifications[:5]:
            title = truncate(notification.get("title", ""), 44)
            body = truncate(notification.get("body") or "", 44)
            lines.append(f"  {YELLOW if not notification.get('read') else DIM}•{RESET} {title}")
            if body:
                lines.append(f"    {DIM}{body}{RESET}")
    lines.append(f"{DIM}conductor watch to start watcher{RESET}")
    return "\n".join(lines)


def main():
    while True:
        try:
            output = render(read_tasks(), read_notifications())
            sys.stdout.write(CLEAR_SCREEN + output + "\n")
            sys.stdout.flush()
        except KeyboardInterrupt:
            raise
        except Exception as error:
            sys.stdout.write(f"Error: {error}\n")
            sys.stdout.flush()
        time.sleep(3)


if __name__ == "__main__":
    main()
