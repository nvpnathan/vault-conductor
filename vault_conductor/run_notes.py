from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .markdown import append_section_line, parse_markdown, replace_section, stringify_markdown, write_file_atomic
from .tasks import TaskNote, now_iso, relative_to_vault


@dataclass
class Run:
    id: str
    task_id: str
    task_note: str
    status: str
    agent: str
    repo: str
    repo_path: str
    worktree: str
    branch: str
    pid: int | None
    exit_code: int | None
    started: str
    ended: str | None
    log_file: str
    prompt_file: str
    activity_file: str | None = None
    current_activity: str | None = None
    current_activity_detail: str | None = None
    workspace_ref: str | None = None
    surface_ref: str | None = None
    cmux_command: str | None = None
    type: str = "agent-run"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Run":
        return cls(
            id=str(data.get("id")),
            task_id=str(data.get("task_id")),
            task_note=str(data.get("task_note")),
            status=str(data.get("status", "running")),
            agent=str(data.get("agent", "codex")),
            repo=str(data.get("repo", "")),
            repo_path=str(data.get("repo_path", "")),
            worktree=str(data.get("worktree", "")),
            branch=str(data.get("branch", "")),
            pid=data.get("pid"),
            exit_code=data.get("exit_code"),
            started=str(data.get("started", "")),
            ended=data.get("ended"),
            log_file=str(data.get("log_file", "")),
            prompt_file=str(data.get("prompt_file", "")),
            activity_file=data.get("activity_file"),
            current_activity=data.get("current_activity"),
            current_activity_detail=data.get("current_activity_detail"),
            workspace_ref=data.get("workspace_ref"),
            surface_ref=data.get("surface_ref"),
            cmux_command=data.get("cmux_command"),
            type=str(data.get("type", "agent-run")),
        )

    def to_mapping(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = "agent-run"
        return data


@dataclass
class RunNote:
    path: str
    abs_path: Path
    frontmatter: Run
    body: str


def create_run_note(config: Config, task: TaskNote) -> RunNote:
    run_id = allocate_run_id(config, task.frontmatter.id)
    prompt_file = config.prompts_root / f"{run_id}.prompt.md"
    log_file = config.logs_root / f"{run_id}.log"
    activity_file = config.runs_dir / f"{run_id}-activity.md"
    run = Run(
        id=run_id,
        task_id=task.frontmatter.id,
        task_note=task.path,
        status="running",
        agent=task.frontmatter.agent,
        repo=task.frontmatter.repo,
        repo_path=task.frontmatter.repo_path,
        worktree=task.frontmatter.worktree,
        branch=task.frontmatter.branch,
        pid=None,
        exit_code=None,
        started=now_iso(),
        ended=None,
        log_file=str(log_file),
        prompt_file=str(prompt_file),
        activity_file=str(activity_file),
    )
    rel_path = f"30 Agent Runs/{run_id}-{task.frontmatter.agent}.md"
    abs_path = config.vault_path / rel_path
    body = run_body(run)
    write_file_atomic(abs_path, stringify_markdown(run.to_mapping(), body))
    return RunNote(rel_path, abs_path, run, body)


def allocate_run_id(config: Config, task_id: str) -> str:
    numbers: list[int] = []
    if config.runs_dir.exists():
        pattern = re.compile(rf"^{re.escape(task_id)}-RUN-(\d{{3,}})")
        for path in config.runs_dir.glob("*.md"):
            match = pattern.match(path.name)
            if match:
                numbers.append(int(match.group(1)))
    return f"{task_id}-RUN-{max(numbers or [0]) + 1:03d}"


def find_run_path(config: Config, run_id_or_path: str) -> Path:
    if re.search(r"AGT-\d{4,}-RUN-\d{3,}", run_id_or_path):
        if not config.runs_dir.exists():
            raise FileNotFoundError(f"Run note not found for {run_id_or_path}")
        for path in sorted(config.runs_dir.glob("*.md")):
            if not path.name.startswith(run_id_or_path):
                continue
            fm, _ = parse_markdown(path.read_text(encoding="utf-8"))
            if fm.get("type", "agent-run") == "agent-run":
                return path
        for path in sorted(config.runs_dir.glob("*.md")):
            fm, _ = parse_markdown(path.read_text(encoding="utf-8"))
            if fm.get("id") == run_id_or_path:
                return path
    candidate = Path(run_id_or_path)
    if not candidate.is_absolute():
        candidate = config.vault_path / run_id_or_path
    if not candidate.exists():
        raise FileNotFoundError(f"Run note not found: {run_id_or_path}")
    return candidate


def read_run_note(config: Config, run_id_or_path: str) -> RunNote:
    abs_path = find_run_path(config, run_id_or_path)
    fm, body = parse_markdown(abs_path.read_text(encoding="utf-8"))
    return RunNote(relative_to_vault(config, abs_path), abs_path, Run.from_mapping(fm), body)


def update_run_frontmatter(config: Config, run_id: str, patch: dict[str, Any]) -> None:
    note = read_run_note(config, run_id)
    data = note.frontmatter.to_mapping()
    data.update(patch)
    write_file_atomic(note.abs_path, stringify_markdown(data, note.body))


def append_run_followup(config: Config, run_id: str, message: str) -> None:
    note = read_run_note(config, run_id)
    body = append_section_line(note.body, "Follow-up instructions", f"{now_iso()} - {message}")
    write_file_atomic(note.abs_path, stringify_markdown(note.frontmatter.to_mapping(), body))


def replace_run_section(config: Config, run_id: str, heading: str, content: str) -> None:
    note = read_run_note(config, run_id)
    body = replace_section(note.body, heading, content)
    write_file_atomic(note.abs_path, stringify_markdown(note.frontmatter.to_mapping(), body))


def run_body(run: Run) -> str:
    activity = run.activity_file or "Pending."
    return f"""# Summary

Run {run.id} created.

# Prompt

See prompt file: {run.prompt_file}

# Live log pointer

{run.log_file}

# Activity timeline

{activity}

# Agent output summary

Pending.

# Files changed

Pending.

# Commands run

Pending.

# Test result

Pending.

# Blockers

None.

# Follow-up instructions

None.
"""
