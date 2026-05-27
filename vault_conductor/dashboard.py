#!/usr/bin/env python3
"""
Control Room dashboard — runs in the cmux Dock sidebar.
Refreshes every 3 seconds showing project status and recent notifications.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pip install pyyaml", flush=True)
    sys.exit(1)

VAULT = Path(__file__).parent.parent / "Agent Control Room"
PROJECTS = VAULT / "Projects"

STATUS_ICON = {
    "backlog": "○",
    "ready": "◎",
    "in_progress": "▶",
    "needs_attention": "●",
    "review": "◈",
    "done": "✓",
}

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
    "in_progress": BLUE,
    "needs_attention": YELLOW,
    "review": MAGENTA,
    "done": GREEN,
}


def read_projects() -> list[dict]:
    projects = []
    for note in sorted(PROJECTS.glob("*.md")):
        if note.name.startswith("."):
            continue
        try:
            text = note.read_text(encoding="utf-8")
            fm: dict = {}
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    fm = yaml.safe_load(text[3:end]) or {}
            projects.append({
                "name": note.stem,
                "status": fm.get("status") or "backlog",
                "goal": fm.get("goal") or "",
                "workspace_ref": fm.get("workspace_ref"),
                "priority": fm.get("priority") or "medium",
            })
        except Exception as e:
            print(f"[dashboard] error reading {note.name}: {e}", file=sys.stderr)

    order = ["needs_attention", "in_progress", "review", "ready", "backlog", "done"]
    projects.sort(key=lambda p: (order.index(p["status"]) if p["status"] in order else 99, p["name"]))
    return projects


def read_notifications() -> list[dict]:
    try:
        r = subprocess.run(
            ["cmux", "--json", "list-notifications"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout).get("notifications", [])
    except Exception as e:
        print(f"[dashboard] error reading notifications: {e}", file=sys.stderr)
    return []


def truncate(s: str, n: int) -> str:
    return s[:n - 1] + "…" if len(s) > n else s


def render(projects: list[dict], notifications: list[dict]) -> str:
    w = 46
    lines = [
        f"{BOLD}Agent Control Room{RESET}  {DIM}{time.strftime('%H:%M:%S')}{RESET}",
        "─" * w,
    ]

    if not projects:
        lines.append(f"{DIM}  No projects yet — add a note to Projects/{RESET}")
    else:
        for p in projects:
            status = p["status"]
            icon = STATUS_ICON.get(status, "?")
            color = STATUS_COLOR.get(status, "")
            goal = truncate(p["goal"], 30) if p["goal"] else DIM + "(no goal)" + RESET
            ws = f" {DIM}[{p['workspace_ref']}]{RESET}" if p["workspace_ref"] else ""
            lines.append(f"  {color}{icon}{RESET} {BOLD}{p['name']}{RESET}{ws}")
            lines.append(f"    {DIM}{goal}{RESET}")
            lines.append("")

    if notifications:
        lines.append("─" * w)
        lines.append(f"{BOLD}Notifications{RESET}  {DIM}(Cmd+Shift+U to jump){RESET}")
        lines.append("")
        for n in notifications[:5]:
            title = n.get("title", "")
            body = truncate(n.get("body") or "", 36)
            read_marker = f"{DIM}·{RESET}" if n.get("read") else f"{YELLOW}•{RESET}"
            lines.append(f"  {read_marker} {title}")
            if body:
                lines.append(f"    {DIM}{body}{RESET}")
        lines.append("")

    lines.append(f"{DIM}uv run orchestrator to start watcher{RESET}")
    return "\n".join(lines)


def main():
    while True:
        try:
            projects = read_projects()
            notifications = read_notifications()
            output = render(projects, notifications)
            sys.stdout.write(CLEAR_SCREEN + output + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(f"Error: {e}\n")
            sys.stdout.flush()
        time.sleep(3)


if __name__ == "__main__":
    main()
