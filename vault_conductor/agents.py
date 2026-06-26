from __future__ import annotations

import re

from .config import Config
from .run_notes import RunNote
from .tasks import TaskNote


def build_prompt(config: Config, task: TaskNote, run: RunNote) -> str:
    return f"""# Agent Control Room Task

You are working on a task managed by an Obsidian Kanban Agent Control Room.

## Non-negotiable rules

1. Work only in this assigned worktree: {task.frontmatter.worktree}
2. Do not edit the user's original repo checkout outside this worktree.
3. Do not merge, delete branches, delete worktrees, or mark the task Done.
4. Keep changes minimal and focused on the acceptance criteria.
5. If blocked, ask exactly one specific human question.
6. Report meaningful current activity with `conductor activity {task.frontmatter.id} <activity> --detail "<detail>"`.
7. Use only these activities: reading, planning, editing, testing, debugging, waiting, blocked, reviewing.
8. If blocked on a human, ask exactly one specific question and set status to `needs-human`; if you cannot edit the note, clearly print `AGENT_STATUS: needs-human`.
9. When implementation is ready for review, set status to `review-diff`; if you cannot edit the note, clearly print `AGENT_STATUS: review-diff`.
10. When a human asks you to commit, push, hand off, or open a PR after the task is ready for review, run `conductor pr {task.frontmatter.id} --auto`. Do not stop after a raw git push; PR handoff moves the task to `pr-opened`.
11. Include changed files, test results, and risks in your final summary.

## Task note

Path: {task.abs_path}

{task.body}

## Repo

Repo: {task.frontmatter.repo}
Repo path: {task.frontmatter.repo_path}
Worktree: {task.frontmatter.worktree}
Branch: {task.frontmatter.branch}
Base branch: {task.frontmatter.base_branch}

## Run

Run note: {run.abs_path}
Prompt file: {run.frontmatter.prompt_file}
Log file: {run.frontmatter.log_file}

## Expected output

At completion, provide:

- status: one of `needs-human`, `review-diff`, `pr-opened`, or `failed`
- summary
- changed files
- tests run
- risks
- next human action
"""


def provider_command(config: Config, agent: str, variables: dict[str, str]) -> tuple[str, dict[str, str]]:
    agent_config = config.agents.get(agent)
    if not agent_config or not agent_config.enabled:
        raise ValueError(f"Agent provider is not enabled: {agent}")
    command = " ".join(
        part
        for part in [apply_template(agent_config.command, variables), *[apply_template(arg, variables) for arg in agent_config.args]]
        if part
    )
    env = {key: apply_template(value, variables) for key, value in agent_config.env.items()}
    return command, env


def template_variables(config: Config, task: TaskNote, run: RunNote, prompt: str) -> dict[str, str]:
    return {
        "task_id": task.frontmatter.id,
        "run_id": run.frontmatter.id,
        "repo": task.frontmatter.repo,
        "repo_path": task.frontmatter.repo_path,
        "worktree": task.frontmatter.worktree,
        "branch": task.frontmatter.branch,
        "task_note": task.path,
        "task_note_abs_path": str(task.abs_path),
        "run_note": run.path,
        "run_note_abs_path": str(run.abs_path),
        "prompt": prompt,
        "prompt_file": run.frontmatter.prompt_file,
        "log_file": run.frontmatter.log_file,
        "vault": str(config.vault_path),
    }


def apply_template(value: str, variables: dict[str, str]) -> str:
    return re.sub(r"\{\{([a-zA-Z0-9_]+)\}\}", lambda match: variables.get(match.group(1), ""), value)


def detect_agent_status(text: str) -> str | None:
    for line in text.replace("\r\n", "\n").split("\n"):
        match = re.match(r"^AGENT_STATUS:\s*(needs-human|review-diff|failed)\s*$", line.strip())
        if match:
            return match.group(1)
    return None


def detect_agent_activity(text: str) -> tuple[str, str] | None:
    for line in text.replace("\r\n", "\n").split("\n"):
        match = re.match(r"^AGENT_ACTIVITY:\s*([a-zA-Z0-9_-]+)(?:\s*\|\s*(.*))?$", line.strip())
        if match:
            return match.group(1), (match.group(2) or "").strip()
    return None
