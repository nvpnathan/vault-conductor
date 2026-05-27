from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .kanban import parse_board
from .markdown import append_section_line, parse_markdown, replace_section as replace_body_section, stringify_markdown, write_file_atomic


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def filename_title(value: str) -> str:
    cleaned = re.sub(r"[\\/:\n\r\t]+", " ", value).strip()
    return re.sub(r"\s+", " ", cleaned) or "Untitled Task"


def slugify_text(value: str, max_length: int = 64) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return (slug or "task")[:max_length].strip("-") or "task"


@dataclass
class Task:
    id: str
    title: str
    status: str
    repo: str
    repo_path: str
    project: str
    agent: str
    priority: str
    risk: str
    base_branch: str
    branch: str
    worktree: str
    current_run: str | None
    run_count: int
    human_gate: str
    pr_url: str | None
    created: str
    updated: str
    completed: str | None
    tags: list[str]
    workspace_ref: str | None = None
    surface_ref: str | None = None
    cmux_command: str | None = None
    model: str | None = None
    assignment_source: str | None = None
    issue_url: str | None = None
    parent_task: str | None = None
    attempt_group: str | None = None
    reviewer_agent: str | None = None
    test_command: str | None = None
    lint_command: str | None = None
    typecheck_command: str | None = None
    setup_command: str | None = None
    max_runtime_minutes: int | None = None
    approval_policy: str | None = None
    sandbox_policy: str | None = None
    last_error: str | None = None
    last_exit_code: int | None = None
    last_test_status: str | None = None
    last_diff_stat: str | None = None
    current_activity: str | None = None
    current_activity_detail: str | None = None
    type: str = "agent-task"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Task":
        names = {field.name for field in fields(cls)}
        defaults = {
            "type": "agent-task",
            "project": "",
            "priority": "P2",
            "risk": "medium",
            "base_branch": "main",
            "current_run": None,
            "run_count": 0,
            "human_gate": "review-diff-before-pr",
            "pr_url": None,
            "completed": None,
            "tags": [],
        }
        merged = {**defaults, **data}
        return cls(**{key: merged.get(key) for key in names})

    def to_mapping(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = "agent-task"
        return data


@dataclass
class TaskNote:
    path: str
    abs_path: Path
    frontmatter: Task
    body: str


def state_path(config: Config) -> Path:
    return config.system_dir / "state.json"


def read_state(config: Config) -> dict[str, Any]:
    path = state_path(config)
    if not path.exists():
        return {"version": 1, "lastTaskId": 0, "activeRuns": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(config: Config, state: dict[str, Any]) -> None:
    write_file_atomic(state_path(config), json.dumps(state, indent=2) + "\n")


def allocate_task_id(config: Config) -> str:
    numbers = collect_task_numbers(config)
    state = read_state(config)
    next_id = max([0, int(state.get("lastTaskId", 0)), *numbers]) + 1
    state["lastTaskId"] = next_id
    write_state(config, state)
    return f"AGT-{next_id:04d}"


def collect_task_numbers(config: Config) -> list[int]:
    found: set[int] = set()
    if config.tasks_dir.exists():
        for path in config.tasks_dir.glob("*.md"):
            match = re.search(r"AGT-(\d{4,})", path.name)
            if match:
                found.add(int(match.group(1)))
            try:
                fm, _ = parse_markdown(path.read_text(encoding="utf-8"))
                match = re.search(r"AGT-(\d{4,})", str(fm.get("id", "")))
                if match:
                    found.add(int(match.group(1)))
            except Exception:
                pass
    if config.board_path.exists():
        for match in re.finditer(r"AGT-(\d{4,})", config.board_path.read_text(encoding="utf-8")):
            found.add(int(match.group(1)))
    return sorted(found)


def create_task_note(
    config: Config,
    *,
    title: str,
    repo: str,
    repo_path: str | Path,
    project: str = "",
    agent: str = "codex",
    priority: str = "P2",
    risk: str = "medium",
    status: str = "backlog",
    goal: str = "",
    acceptance: list[str] | None = None,
    context: str = "",
    base_branch: str = "main",
    test_command: str | None = None,
    task_id: str | None = None,
) -> TaskNote:
    task_id = task_id or allocate_task_id(config)
    title = filename_title(title)
    created = now_iso()
    slug = slugify_text(title, int(config.branching.get("slug_max_length", 64)))
    repo_path = str(Path(repo_path).expanduser().resolve())
    worktree = str(config.worktrees_root / repo / task_id)
    task = Task(
        id=task_id,
        title=title,
        status=status,
        repo=repo,
        repo_path=repo_path,
        project=project,
        agent=agent,
        priority=priority,
        risk=risk,
        base_branch=base_branch,
        branch=f"{config.branching.get('prefix', 'agent')}/{task_id}-{slug}",
        worktree=worktree,
        current_run=None,
        run_count=0,
        human_gate="review-diff-before-pr",
        pr_url=None,
        created=created,
        updated=created,
        completed=None,
        tags=["agent-task", f"repo/{repo}", f"agent/{agent}"],
        test_command=test_command,
    )
    body = task_body(created, goal, acceptance or ["Pending."], context or "None.")
    rel_path = f"20 Agent Tasks/{task_id} {title}.md"
    abs_path = config.vault_path / rel_path
    write_file_atomic(abs_path, stringify_markdown(task.to_mapping(), body))
    return TaskNote(rel_path, abs_path, task, body)


def task_body(created: str, goal: str, acceptance: list[str], context: str) -> str:
    return f"""# Goal

{goal or "Pending."}

# Acceptance criteria

{chr(10).join(f"- {item}" for item in acceptance)}

# Context

{context}

# Agent instructions

- Work only in the assigned worktree.
- Keep the change minimal and focused on the acceptance criteria.
- Do not merge, delete branches, delete worktrees, or mark this task Done.
- If blocked, add one specific question under `# Human question` and set status to `needs-human`.
- When implementation is complete, update `# Diff summary`, update `# Test output`, and set status to `review-diff`.

# Human question

None.

# Current status

Created. Not started.

# Log

- {created} - Task created.

# Diff summary

Pending.

# Test output

Pending.

# Decision

Pending.

# Runs

No runs yet.

# Links

- Board: [[00 Control Room/Agent Control Room]]
"""


def find_task_path(config: Config, task_id_or_path: str) -> Path:
    if re.fullmatch(r"AGT-\d{4,}", task_id_or_path):
        if not config.tasks_dir.exists():
            raise FileNotFoundError(f"Task note not found for {task_id_or_path}")
        for path in sorted(config.tasks_dir.glob("*.md")):
            if path.name.startswith(f"{task_id_or_path} "):
                return path
        for path in sorted(config.tasks_dir.glob("*.md")):
            fm, _ = parse_markdown(path.read_text(encoding="utf-8"))
            if fm.get("id") == task_id_or_path:
                return path
        raise FileNotFoundError(f"Task note not found for {task_id_or_path}")
    candidate = Path(task_id_or_path)
    if not candidate.is_absolute():
        candidate = config.vault_path / task_id_or_path
    if not candidate.exists():
        raise FileNotFoundError(f"Task note not found: {task_id_or_path}")
    return candidate


def read_task_note(config: Config, task_id_or_path: str) -> TaskNote:
    abs_path = find_task_path(config, task_id_or_path)
    fm, body = parse_markdown(abs_path.read_text(encoding="utf-8"))
    task = Task.from_mapping(fm)
    return TaskNote(relative_to_vault(config, abs_path), abs_path, task, body)


def read_all_task_notes(config: Config) -> list[TaskNote]:
    if not config.tasks_dir.exists():
        return []
    tasks: list[TaskNote] = []
    for path in sorted(config.tasks_dir.glob("*.md")):
        try:
            tasks.append(read_task_note(config, str(path)))
        except Exception:
            pass
    return tasks


def update_task_frontmatter(config: Config, task_id: str, patch: dict[str, Any]) -> None:
    note = read_task_note(config, task_id)
    data = note.frontmatter.to_mapping()
    data.update(patch)
    data["updated"] = patch.get("updated", now_iso())
    if data.get("status") == "done" and not data.get("completed"):
        data["completed"] = now_iso()
    write_file_atomic(note.abs_path, stringify_markdown(data, note.body))


def append_task_log(config: Config, task_id: str, message: str) -> None:
    note = read_task_note(config, task_id)
    body = append_section_line(note.body, "Log", f"{now_iso()} - {message}")
    write_file_atomic(note.abs_path, stringify_markdown(note.frontmatter.to_mapping(), body))


def replace_task_section(config: Config, task_id: str, heading: str, content: str) -> None:
    note = read_task_note(config, task_id)
    body = replace_body_section(note.body, heading, content)
    write_file_atomic(note.abs_path, stringify_markdown(note.frontmatter.to_mapping(), body))


def status_to_column(config: Config, status: str) -> str:
    return config.columns[status]


def status_from_column(config: Config, column: str) -> str | None:
    for status, title in config.columns.items():
        if title == column:
            return status
    return None


def relative_to_vault(config: Config, path: Path) -> str:
    return path.resolve().relative_to(config.vault_path.resolve()).as_posix()


def board_task_ids(config: Config) -> set[str]:
    if not config.board_path.exists():
        return set()
    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    return {card.task_id for column in board.columns for card in column.cards}
