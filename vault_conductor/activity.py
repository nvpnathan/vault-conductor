from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cmux
from .config import Config
from .markdown import parse_markdown, stringify_markdown, write_file_atomic
from .run_notes import RunNote, update_run_frontmatter
from .sessions import read_sessions, upsert_session
from .tasks import TaskNote, now_iso, read_task_note, update_task_frontmatter


@dataclass(frozen=True)
class ActivityDefinition:
    value: str
    label: str
    icon: str
    color: str
    progress: float


ACTIVITY_DEFINITIONS: dict[str, ActivityDefinition] = {
    "reading": ActivityDefinition("reading", "Reading", "search", "#4c71f2", 0.10),
    "planning": ActivityDefinition("planning", "Planning", "route", "#7c3aed", 0.20),
    "editing": ActivityDefinition("editing", "Editing", "pencil", "#f59e0b", 0.45),
    "testing": ActivityDefinition("testing", "Testing", "flask", "#14b8a6", 0.65),
    "debugging": ActivityDefinition("debugging", "Debugging", "bug", "#f97316", 0.55),
    "waiting": ActivityDefinition("waiting", "Waiting", "clock", "#6b7280", 0.50),
    "blocked": ActivityDefinition("blocked", "Blocked", "circle-alert", "#dc2626", 0.50),
    "reviewing": ActivityDefinition("reviewing", "Reviewing", "git-pull-request", "#16a34a", 0.85),
}


def validate_activity(value: str) -> ActivityDefinition:
    normalized = value.strip().lower()
    try:
        return ACTIVITY_DEFINITIONS[normalized]
    except KeyError:
        valid = ", ".join(sorted(ACTIVITY_DEFINITIONS))
        raise ValueError(f"Unknown activity: {value}. Valid activities: {valid}") from None


def create_activity_timeline(config: Config, task: TaskNote, run: RunNote) -> Path:
    path = Path(run.frontmatter.activity_file or config.runs_dir / f"{run.frontmatter.id}-activity.md")
    frontmatter = {
        "type": "agent-run-activity",
        "run_id": run.frontmatter.id,
        "task_id": task.frontmatter.id,
        "task_note": task.path,
        "agent": task.frontmatter.agent,
        "repo": task.frontmatter.repo,
        "created": now_iso(),
    }
    body = f"""# Activity Timeline

Meaningful activity changes for [[{run.path.removesuffix(".md")}]].
"""
    if not path.exists():
        write_file_atomic(path, stringify_markdown(frontmatter, body))
    return path


def record_activity(config: Config, task_id: str, activity: str, *, detail: str = "") -> dict[str, Any]:
    definition = validate_activity(activity)
    task = read_task_note(config, task_id)
    if not task.frontmatter.current_run:
        raise ValueError(f"No current run found for {task_id}.")

    timestamp = now_iso()
    detail = detail.strip()
    timeline_path = activity_timeline_path(config, task)
    changed = append_activity_entry(
        timeline_path,
        timestamp=timestamp,
        definition=definition,
        detail=detail,
    )

    patch = {
        "current_activity": definition.value,
        "current_activity_detail": detail or None,
    }
    update_task_frontmatter(config, task_id, patch)
    update_run_frontmatter(
        config,
        task.frontmatter.current_run,
        {
            "current_activity": definition.value,
            "current_activity_detail": detail or None,
            "activity_file": str(timeline_path),
        },
    )

    session = read_sessions(config).get("sessions", {}).get(task_id)
    if session:
        session["current_activity"] = definition.value
        session["current_activity_detail"] = detail
        session["last_activity_at"] = timestamp
        upsert_session(config, task_id, session)
        workspace_ref = session.get("workspace_ref")
        if workspace_ref:
            render_activity(workspace_ref, definition, detail=detail, changed=changed)

    return {
        "task_id": task_id,
        "run_id": task.frontmatter.current_run,
        "activity": definition.value,
        "detail": detail,
        "timeline": str(timeline_path),
        "recorded": changed,
    }


def activity_timeline_path(config: Config, task: TaskNote) -> Path:
    run_id = task.frontmatter.current_run
    if not run_id:
        raise ValueError(f"No current run found for {task.frontmatter.id}.")
    from .run_notes import read_run_note

    run = read_run_note(config, run_id)
    if run.frontmatter.activity_file:
        return Path(run.frontmatter.activity_file)
    path = config.runs_dir / f"{run_id}-activity.md"
    update_run_frontmatter(config, run_id, {"activity_file": str(path)})
    if not path.exists():
        write_file_atomic(
            path,
            stringify_markdown(
                {
                    "type": "agent-run-activity",
                    "run_id": run_id,
                    "task_id": task.frontmatter.id,
                    "task_note": task.path,
                    "agent": task.frontmatter.agent,
                    "repo": task.frontmatter.repo,
                    "created": now_iso(),
                },
                "# Activity Timeline\n\n",
            ),
        )
    return path


def append_activity_entry(
    path: Path,
    *,
    timestamp: str,
    definition: ActivityDefinition,
    detail: str = "",
) -> bool:
    if path.exists():
        frontmatter, body = parse_markdown(path.read_text(encoding="utf-8"))
    else:
        frontmatter, body = {"type": "agent-run-activity", "created": timestamp}, "# Activity Timeline\n\n"
    line = activity_line(timestamp, definition, detail)
    previous = last_activity_line(body)
    if previous and previous.endswith(activity_line("", definition, detail).lstrip()):
        return False
    write_file_atomic(path, stringify_markdown(frontmatter, f"{body.rstrip()}\n{line}\n"))
    return True


def activity_line(timestamp: str, definition: ActivityDefinition, detail: str = "") -> str:
    suffix = f" - {detail}" if detail else ""
    if timestamp:
        return f"- {timestamp} - {definition.label}{suffix}"
    return f"- {definition.label}{suffix}"


def last_activity_line(body: str) -> str:
    for line in reversed(body.splitlines()):
        if line.startswith("- "):
            return line
    return ""


def render_activity(
    workspace_ref: str,
    definition: ActivityDefinition,
    *,
    detail: str = "",
    changed: bool = True,
) -> None:
    cmux.set_activity(workspace_ref, definition.label, icon=definition.icon, color=definition.color)
    cmux.set_progress(workspace_ref, definition.progress, label=definition.label)
    if changed:
        message = f"{definition.label}: {detail}" if detail else definition.label
        cmux.log(message, workspace_ref=workspace_ref, source="conductor-activity")
