from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import cmux
from .activity import record_activity
from .agents import detect_agent_activity, detect_agent_status
from .config import Config, config_to_yaml
from .constants import BOARD_COLUMNS, TASK_STATUSES
from .engine import ConductorEngine
from .git_ops import (
    get_branch_diff_stat,
    get_diff_name_only,
    get_diff_stat,
    get_full_diff,
    git_status_short,
    remove_worktree,
    run_git,
)
from .kanban import (
    add_card,
    build_card_line,
    empty_board_content,
    ensure_columns,
    find_card,
    move_card,
    parse_board,
    render_board,
    update_card_line,
)
from .markdown import write_file_atomic
from .repos import RepoEntry, find_repo, load_repo_registry, registry_path, scan_repos, sync_project_notes
from .run_notes import update_run_frontmatter
from .sessions import read_sessions, remove_session, transcript_hash, upsert_session
from .tasks import (
    append_task_log,
    create_task_note,
    now_iso,
    read_all_task_notes,
    read_task_note,
    replace_task_section,
    status_from_column,
    status_to_column,
    update_task_frontmatter,
)


DASHBOARD_NOTES = {
    "Needs Human.md": {
        "column": "Needs Human",
        "status": "needs-human",
        "commands": [
            "conductor status",
            "conductor log <TASK_ID> --tail 100",
            'conductor send <TASK_ID> "Answer or instruction" --status running',
            "conductor mark <TASK_ID> needs-revision",
        ],
    },
    "Review Queue.md": {
        "column": "Review Diff",
        "status": "review-diff",
        "commands": [
            "conductor diff <TASK_ID> --stat --save",
            "conductor test <TASK_ID>",
            "conductor pr <TASK_ID> --auto",
            'conductor send <TASK_ID> "Requested revision" --status needs-revision',
            "conductor mark <TASK_ID> done --human",
        ],
    },
    "Running Agents.md": {
        "column": "Running",
        "status": "running",
        "commands": [
            "conductor status",
            "conductor log <TASK_ID> --tail 100",
            'conductor send <TASK_ID> "Follow-up instruction"',
            "conductor mark <TASK_ID> review-diff",
            "conductor mark <TASK_ID> needs-human",
        ],
    },
    "Failed and Parked.md": {
        "column": "Failed / Parked",
        "status": "failed or parked",
        "commands": [
            "conductor status",
            "conductor log <TASK_ID> --tail 100",
            'conductor send <TASK_ID> "Recovery instruction" --status ready',
            "conductor start <TASK_ID>",
            "conductor cleanup <TASK_ID> --yes --dry-run",
        ],
    },
}


def dashboard_note_content(name: str, note: dict[str, Any]) -> str:
    commands = "\n".join(note["commands"])
    return f"""# {name.removesuffix(".md")}

Open the main board and inspect the `{note["column"]}` column.

Tasks in this view use status `{note["status"]}`.

Useful commands:

```bash
{commands}
```
"""


def should_refresh_dashboard_note(path: Path, *, force: bool) -> bool:
    if force or not path.exists():
        return True
    text = path.read_text(encoding="utf-8")
    return "agentctl" in text or "uv run conductor" in text or "TABLE status, repo, agent" in text


def init_command(config: Config, *, force: bool = False, open_obsidian: bool | None = None) -> dict[str, str]:
    for directory in [
        config.control_room_dir,
        config.projects_dir,
        config.tasks_dir,
        config.runs_dir,
        config.vault_path / "40 Decisions",
        config.templates_dir,
        config.system_dir,
        config.worktrees_root,
        config.logs_root,
        config.prompts_root,
        config.state_root,
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    if force or not config.board_path.exists():
        write_file_atomic(config.board_path, empty_board_content(BOARD_COLUMNS))
    else:
        board = ensure_columns(parse_board(config.board_path.read_text(encoding="utf-8")), BOARD_COLUMNS)
        write_file_atomic(config.board_path, render_board(board))

    templates = {
        "Agent Task Template.md": "# Goal\n\nPending.\n",
        "Agent Run Template.md": "# Summary\n\nPending.\n",
        "Agent Prompt Template.md": "# Agent Control Room Task\n\nPending.\n",
        "Review Prompt Template.md": "# Review\n\nPending.\n",
    }
    for filename, content in templates.items():
        path = config.templates_dir / filename
        if not path.exists():
            write_file_atomic(path, content)

    config_path = config.system_dir / "control-room.config.yml"
    if not config_path.exists():
        write_file_atomic(config_path, config_to_yaml(config))
    repo_registry = registry_path(config)
    if not repo_registry.exists():
        write_file_atomic(repo_registry, "version: 1\nrepos: []\n")
    state_file = config.system_dir / "state.json"
    if not state_file.exists():
        write_file_atomic(state_file, json.dumps({"version": 1, "lastTaskId": 0, "activeRuns": {}}, indent=2) + "\n")
    sessions_file = config.state_root / "sessions.json"
    if not sessions_file.exists():
        write_file_atomic(sessions_file, json.dumps({"version": 1, "sessions": {}}, indent=2) + "\n")

    for name, note in DASHBOARD_NOTES.items():
        path = config.control_room_dir / name
        if should_refresh_dashboard_note(path, force=force):
            write_file_atomic(path, dashboard_note_content(name, note))

    if open_obsidian if open_obsidian is not None else False:
        open_board(config)
    return {"vaultPath": str(config.vault_path), "boardFile": config.board_file}


def open_board(config: Config) -> None:
    obsidian = str(config.obsidian.get("cli_command", "obsidian"))
    if shutil.which(obsidian):
        subprocess.run([obsidian, str(config.board_path)], check=False)


def scan_command(config: Config) -> dict[str, Any]:
    return scan_repos(config)


def new_task_command(
    config: Config,
    *,
    repo: str,
    title: str,
    agent: str | None = None,
    priority: str = "P2",
    risk: str = "medium",
    status: str = "backlog",
    goal: str = "",
    acceptance: list[str] | None = None,
    context: str = "",
    test_command: str | None = None,
) -> dict[str, str]:
    if status not in {"backlog", "ready"}:
        raise ValueError(f"conductor new status must be backlog or ready, got: {status}")
    repo_entry = resolve_repo(config, repo)
    task = create_task_note(
        config,
        title=title,
        repo=repo_entry.name,
        repo_path=repo_entry.path,
        project="",
        agent=agent or repo_entry.default_agent or "codex",
        priority=priority,
        risk=risk,
        status=status,
        goal=goal,
        acceptance=acceptance,
        context=context,
        base_branch=repo_entry.default_branch,
        test_command=test_command or repo_entry.commands.get("test"),
    )
    board = read_board(config)
    add_card(board, status_to_column(config, task.frontmatter.status), build_card_line(task.frontmatter))
    write_board(config, board)
    sync_project_notes(config)
    return {"id": task.frontmatter.id, "path": task.path}


def resolve_repo(config: Config, repo_name: str) -> RepoEntry:
    repo = find_repo(config, repo_name)
    if repo:
        return repo
    fallback = config.repos_root / repo_name
    if fallback.exists():
        return RepoEntry(
            name=repo_name,
            path=str(fallback.resolve()),
            default_branch="main",
            default_agent="codex",
            status="active",
            last_scanned=now_iso(),
            commands={},
        )
    raise ValueError(f"Unknown repo: {repo_name}. Run conductor scan or create {fallback}.")


def read_board(config: Config):
    if config.board_path.exists():
        return parse_board(config.board_path.read_text(encoding="utf-8"))
    return parse_board(empty_board_content(BOARD_COLUMNS))


def write_board(config: Config, board) -> None:
    write_file_atomic(config.board_path, render_board(board))


def record_status_change(config: Config, task_id: str, before_status: str, after_status: str, *, actor: str, source: str) -> None:
    ConductorEngine(config).record_status_change(task_id, before_status, after_status, actor=actor, source=source)


def mark_task(config: Config, task_id: str, status: str, *, human: bool = False) -> None:
    ConductorEngine(config).set_task_status(
        task_id,
        status,
        actor="human" if human else "conductor",
        source="mark",
        human=human,
    )


def notify_status_change(config: Config, task_id: str, status: str, workspace_ref: str) -> None:
    ConductorEngine(config).notify_status_change(task_id, status, workspace_ref)


def move_command(config: Config, task_id: str, column_or_status: str, *, human: bool = False) -> None:
    if column_or_status in TASK_STATUSES:
        mark_task(config, task_id, column_or_status, human=human)
        return
    status = status_from_column(config, column_or_status)
    if status:
        mark_task(config, task_id, status, human=human)
        return
    task = read_task_note(config, task_id)
    board = read_board(config)
    located = find_card(board, task_id)
    if not located:
        raise ValueError(f"No card found for {task_id}")
    move_card(board, task_id, column_or_status, card_line=located.card.line, checked=task.frontmatter.status == "done")
    update_task_frontmatter(config, task_id, {})
    write_board(config, board)


def sync_command(config: Config, *, board_wins: bool = False) -> dict[str, int]:
    return ConductorEngine(config).sync_board(board_wins=board_wins)


def start_task(config: Config, task_id: str) -> dict[str, str]:
    return ConductorEngine(config).start_task(task_id).to_dict()


def send_command(config: Config, task_id: str, message: str, *, status: str | None = None) -> dict[str, Any]:
    return ConductorEngine(config).send_to_task(task_id, message, status=status).to_dict()


def activity_command(config: Config, task_id: str, activity: str, *, detail: str = "") -> dict[str, Any]:
    return record_activity(config, task_id, activity, detail=detail)


def stop_task(config: Config, task_id: str, *, park: bool = False, kill: bool = False) -> str:
    return ConductorEngine(config).stop_task(task_id, park=park, kill=kill).status


def log_command(config: Config, task_id: str, *, tail: int | None = None) -> str:
    session = read_sessions(config).get("sessions", {}).get(task_id)
    task = read_task_note(config, task_id)
    log_file = session.get("log_file") if session else None
    if not log_file and task.frontmatter.current_run:
        log_file = str(config.logs_root / f"{task.frontmatter.current_run}.log")
    if not log_file:
        raise ValueError(f"No current run log for {task_id}")
    path = Path(log_file)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if tail:
        return "\n".join(text.splitlines()[-tail:])
    return text


def diff_command(
    config: Config,
    task_id: str,
    *,
    stat: bool = False,
    name_only: bool = False,
    full: bool = False,
    save: bool = False,
) -> str:
    task = read_task_note(config, task_id)
    if name_only:
        output = "\n".join(get_diff_name_only(task.frontmatter.worktree))
    elif full:
        output = get_full_diff(task.frontmatter.worktree)
    else:
        output = get_diff_stat(task.frontmatter.worktree)
    if save:
        replace_task_section(config, task_id, "Diff summary", output or "No diff.")
        update_task_frontmatter(config, task_id, {"last_diff_stat": output or "No diff."})
    return output or "No diff."


def test_command(config: Config, task_id: str) -> dict[str, Any]:
    task = read_task_note(config, task_id)
    command = configured_test_command(config, task)
    if not command:
        raise ValueError(f"No test command configured for {task_id}.")
    log_file = config.logs_root / f"{task_id}-test.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, cwd=task.frontmatter.worktree, shell=True, text=True, capture_output=True)
    log_file.write_text(f"COMMAND: {command}\nCWD: {task.frontmatter.worktree}\n{result.stdout}{result.stderr}", encoding="utf-8")
    summary = f"Command: {command}\nExit code: {result.returncode}\nLog: {log_file}"
    replace_task_section(config, task_id, "Test output", summary)
    update_task_frontmatter(config, task_id, {"last_test_status": "passed" if result.returncode == 0 else "failed"})
    return {"exitCode": result.returncode, "command": command}


def configured_test_command(config: Config, task) -> str:
    registry = load_repo_registry(config)
    repo = next((entry for entry in registry.get("repos", []) if entry.get("name") == task.frontmatter.repo), {})
    return task.frontmatter.test_command or (repo.get("commands") or {}).get("test") or config.commands.get("default_test", "")


def pr_command(
    config: Config,
    task_id: str,
    *,
    commit: bool = False,
    yes: bool = False,
    force: bool = False,
    auto: bool = False,
    dry_run: bool = False,
) -> str:
    if auto:
        return pr_handoff_command(config, task_id, force=force, dry_run=dry_run)
    task = read_task_note(config, task_id)
    if task.frontmatter.status not in {"review-diff", "needs-revision"} and not force:
        raise ValueError(f"Task {task_id} must be in review-diff before PR creation, or pass --force.")
    diff = get_diff_stat(task.frontmatter.worktree)
    if not yes:
        raise ValueError("PR creation requires --yes after reviewing the diff.")
    dirty = git_status_short(task.frontmatter.worktree)
    if dirty and not commit:
        raise ValueError("Worktree has uncommitted changes. Pass --commit or commit manually.")
    if dry_run:
        return "dry-run"
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI `gh` is not available.")
    if commit:
        run_git(["-C", task.frontmatter.worktree, "add", "-A"], check=True)
        run_git(["-C", task.frontmatter.worktree, "commit", "-m", f"{task_id}: {task.frontmatter.title}"], check=False)
    run_git(["-C", task.frontmatter.worktree, "push", "-u", "origin", task.frontmatter.branch], check=True)
    body_file = config.prompts_root / f"{task_id}-pr-body.md"
    write_file_atomic(body_file, pr_body(task))
    result = subprocess.run(
        ["gh", "pr", "create", "--title", f"{task_id}: {task.frontmatter.title}", "--body-file", str(body_file)],
        cwd=task.frontmatter.worktree,
        text=True,
        capture_output=True,
        check=True,
    )
    pr_url = result.stdout.strip()
    update_task_frontmatter(config, task_id, {"pr_url": pr_url})
    replace_task_section(config, task_id, "Decision", f"PR opened: {pr_url}")
    mark_task(config, task_id, "pr-opened")
    return pr_url


def pr_handoff_command(
    config: Config,
    task_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    task = read_task_note(config, task_id)
    if task.frontmatter.status not in {"review-diff", "needs-revision"} and not force:
        raise ValueError(f"Task {task_id} must be in review-diff before PR handoff, or pass --force.")
    diff = get_diff_stat(task.frontmatter.worktree)
    branch_diff = get_branch_diff_stat(task.frontmatter.worktree, task.frontmatter.base_branch or "main")
    handoff_diff = diff or branch_diff
    if not handoff_diff.strip():
        raise ValueError(f"Task {task_id} has no diff to hand off.")
    replace_task_section(config, task_id, "Diff summary", handoff_diff)
    update_task_frontmatter(config, task_id, {"last_diff_stat": handoff_diff})

    test_summary = "No test command configured. PR handoff is untested."
    test_result = "untested"
    test_command_text = configured_test_command(config, task)
    if test_command_text:
        result = test_command(config, task_id)
        test_summary = f"Command: {result['command']}\nExit code: {result['exitCode']}"
        if result["exitCode"] != 0:
            capture_pr_failure_artifact(config, task_id, test_summary=test_summary)
            notify_task(config, task_id, "Tests failed", f"{task_id} test command exited {result['exitCode']}.")
            raise ValueError(f"Tests failed for {task_id}; PR was not created.")
        test_result = "passed"
    else:
        replace_task_section(config, task_id, "Test output", test_summary)
        update_task_frontmatter(config, task_id, {"last_test_status": test_result})

    if dry_run:
        return "dry-run"
    if shutil.which("gh") is None:
        raise ValueError("GitHub CLI `gh` is not available.")

    if diff.strip():
        run_git(["-C", task.frontmatter.worktree, "add", "-A"], check=True)
        commit_result = run_git(["-C", task.frontmatter.worktree, "commit", "-m", f"{task_id}: {task.frontmatter.title}"])
        if commit_result.returncode != 0:
            raise RuntimeError(commit_result.stderr or commit_result.stdout or "git commit failed")
    run_git(["-C", task.frontmatter.worktree, "push", "-u", "origin", task.frontmatter.branch], check=True)

    task = read_task_note(config, task_id)
    body_file = config.prompts_root / f"{task_id}-pr-body.md"
    write_file_atomic(body_file, pr_body(task, test_summary=test_summary, test_result=test_result))
    result = subprocess.run(
        ["gh", "pr", "create", "--title", f"{task_id}: {task.frontmatter.title}", "--body-file", str(body_file)],
        cwd=task.frontmatter.worktree,
        text=True,
        capture_output=True,
        check=True,
    )
    pr_url = result.stdout.strip()
    update_task_frontmatter(config, task_id, {"pr_url": pr_url, "last_test_status": test_result})
    replace_task_section(config, task_id, "Decision", f"PR opened: {pr_url}")
    open_pr_in_workspace(config, task_id, pr_url)
    mark_task(config, task_id, "pr-opened")
    return pr_url


def open_pr_in_workspace(config: Config, task_id: str, pr_url: str) -> None:
    session = read_sessions(config).get("sessions", {}).get(task_id)
    workspace_ref = session.get("workspace_ref") if session else None
    if not workspace_ref:
        return
    layout = cmux.CmuxWorkspaceLayout.from_session(session)
    focus_policy = cmux.CmuxHITLPolicy(
        focus_new_surfaces=True,
        browser_focus=True,
        allow_select_workspace=True,
        notify=False,
        open_browser=True,
    )
    cmux.present_handoff(layout, pr_url=pr_url, focus_policy=focus_policy)


def capture_pr_failure_artifact(config: Config, task_id: str, *, test_summary: str) -> Path:
    task = read_task_note(config, task_id)
    sessions = read_sessions(config).get("sessions", {})
    layout = cmux.CmuxWorkspaceLayout.from_session(sessions.get(task_id))
    artifact_path = review_artifact_path(config, task_id, task.frontmatter.branch, "pr-failure")
    evidence = {
        "task": task_id,
        "title": task.frontmatter.title,
        "status": "Tests failed",
        "test": test_summary,
        "test_log": str(config.logs_root / f"{task_id}-test.log"),
        "worktree": task.frontmatter.worktree,
        "branch": task.frontmatter.branch,
    }
    path = cmux.capture_review_artifact(
        artifact_path,
        title=f"{task_id} Tests failed",
        evidence=evidence,
        layout=layout if layout.workspace_ref else None,
        policy=cmux.CmuxHITLPolicy.non_disruptive(),
    )
    append_task_log(config, task_id, f"Review artifact captured: {path}")
    return path


def review_artifact_path(config: Config, task_id: str, branch: str | None, suffix: str) -> Path:
    return config.state_root.parent / "cmux-assets" / safe_path_slug(branch or task_id) / f"{task_id}-{suffix}.html"


def safe_path_slug(value: str) -> str:
    slug = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    return slug.strip("-") or "task"


def notify_task(config: Config, task_id: str, title: str, body: str) -> None:
    session = read_sessions(config).get("sessions", {}).get(task_id)
    workspace_ref = session.get("workspace_ref") if session else None
    cmux.notify(title, body, workspace_ref)


def pr_body(task, *, test_summary: str | None = None, test_result: str | None = None) -> str:
    test_text = test_summary or "See task note test output."
    if test_result == "untested":
        test_text = f"WARNING: {test_text}"
    return f"""## Agent Control Room Task

Task: [[{task.path}]]
ID: {task.frontmatter.id}
Repo: {task.frontmatter.repo}
Agent: {task.frontmatter.agent}
Run: {task.frontmatter.current_run or ""}

## Summary

See task note diff summary.

## Tests

{test_text}

## Human review checklist

- [ ] Diff is scoped to the task.
- [ ] Tests are adequate.
- [ ] No secrets or unrelated files included.
- [ ] Risk is acceptable.
"""


def cleanup_command(
    config: Config,
    task_id: str,
    *,
    yes: bool = False,
    force: bool = False,
    branch: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not yes:
        raise ValueError("Cleanup requires --yes. Review the worktree before deleting it.")
    task = read_task_note(config, task_id)
    dirty = git_status_short(task.frontmatter.worktree)
    if dirty and not force:
        raise ValueError(f"Refusing to cleanup dirty worktree for {task_id}.")
    if dry_run:
        return {"removed": task.frontmatter.worktree, "dryRun": True}
    remove_worktree(config, task, force=force)
    if branch:
        run_git(["-C", task.frontmatter.repo_path, "branch", "-D", task.frontmatter.branch])
    return {"removed": task.frontmatter.worktree}


def status_command(config: Config) -> dict[str, Any]:
    return {
        "tasks": [
            {
                "id": task.frontmatter.id,
                "title": task.frontmatter.title,
                "status": task.frontmatter.status,
                "repo": task.frontmatter.repo,
                "agent": task.frontmatter.agent,
                "current_run": task.frontmatter.current_run,
                "workspace_ref": task.frontmatter.workspace_ref,
                "current_activity": task.frontmatter.current_activity,
                "current_activity_detail": task.frontmatter.current_activity_detail,
            }
            for task in read_all_task_notes(config)
        ],
        "sessions": read_sessions(config).get("sessions", {}),
    }


def doctor_command(config: Config, *, fix: bool = False) -> dict[str, Any]:
    if fix:
        for directory in [config.worktrees_root, config.logs_root, config.prompts_root, config.state_root]:
            directory.mkdir(parents=True, exist_ok=True)
        if config.board_path.exists():
            write_board(config, ensure_columns(read_board(config), BOARD_COLUMNS))
    checks: list[dict[str, str]] = []
    checks.append(exists_check("vault", config.vault_path, "Vault exists"))
    checks.append(exists_check("board", config.board_path, "Board exists"))
    if config.board_path.exists():
        board = read_board(config)
        checks.append(
            {
                "name": "kanban-frontmatter",
                "status": "OK" if "kanban-plugin: board" in board.frontmatter else "FAIL",
                "message": "Board has kanban-plugin frontmatter",
            }
        )
        missing = [column for column in BOARD_COLUMNS if not any(existing.title == column for existing in board.columns)]
        checks.append(
            {
                "name": "columns",
                "status": "FAIL" if missing else "OK",
                "message": f"Missing columns: {', '.join(missing)}" if missing else "Required columns exist",
            }
        )
    checks.append(exists_check("repos", config.repos_root, "Repos root exists"))
    for name, directory in {
        "worktrees": config.worktrees_root,
        "logs": config.logs_root,
        "prompts": config.prompts_root,
        "state": config.state_root,
    }.items():
        checks.append(
            {
                "name": name,
                "status": "OK" if directory.exists() and directory.is_dir() else "WARN",
                "message": f"{name} dir writable: {directory}",
            }
        )
    checks.extend(
        [
            command_check("git", "git", fail=True),
            command_check("cmux", "cmux"),
            command_check("obsidian", str(config.obsidian.get("cli_command", "obsidian"))),
            command_check("gh", "gh"),
            command_check("codex", "codex"),
            command_check("claude", "claude"),
        ]
    )
    provenance = cli_provenance()
    checks.append(
        {
            "name": "conductor-cli",
            "status": "OK" if provenance["executable"] else "WARN",
            "message": (
                f"conductor executable: {provenance['executable']}"
                if provenance["executable"]
                else "conductor executable not found on PATH"
            ),
        }
    )
    if provenance.get("directUrlEditable") is False:
        checks.append(
            {
                "name": "conductor-editable",
                "status": "WARN",
                "message": "conductor package does not appear to be an editable install",
            }
        )
    cmux_details = inspect_cmux_runtime(config)
    checks.extend(cmux_details.pop("checks"))
    return {
        "checks": checks,
        "cli": provenance,
        "cmux": cmux_details,
        "paths": {
            "vaultPath": str(config.vault_path),
            "reposRoot": str(config.repos_root),
            "boardFile": str(config.board_path),
            "worktreesRoot": str(config.worktrees_root),
            "logsRoot": str(config.logs_root),
            "promptsRoot": str(config.prompts_root),
            "stateRoot": str(config.state_root),
        },
    }


def inspect_cmux_runtime(config: Config) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    details: dict[str, Any] = {
        "capabilities": {},
        "identify": {},
        "socketPath": None,
        "sessions": [],
        "checks": checks,
    }
    adapter = cmux.CmuxAdapter()
    try:
        capabilities = adapter.capabilities()
        details["capabilities"] = capabilities.raw
        checks.append(
            {
                "name": "cmux-capabilities",
                "status": "OK" if capabilities.raw else "WARN",
                "message": (
                    f"Discovered {len(capabilities.commands)} cmux commands"
                    if capabilities.raw
                    else "cmux capabilities returned no JSON"
                ),
            }
        )
    except Exception as error:
        checks.append({"name": "cmux-capabilities", "status": "WARN", "message": str(error)})
    try:
        target = adapter.identify()
        identify = {
            key: value
            for key, value in {
                "workspace_ref": target.workspace_ref,
                "workspace_id": target.workspace_id,
                "pane_ref": target.pane_ref,
                "pane_id": target.pane_id,
                "surface_ref": target.surface_ref,
                "surface_id": target.surface_id,
                "socket_path": target.socket_path,
            }.items()
            if value
        }
        details["identify"] = identify
        details["socketPath"] = target.socket_path
        checks.append(
            {
                "name": "cmux-identify",
                "status": "OK" if identify else "WARN",
                "message": f"cmux socket: {target.socket_path}" if target.socket_path else "cmux identify returned no target",
            }
        )
    except Exception as error:
        checks.append({"name": "cmux-identify", "status": "WARN", "message": str(error)})
    runtime = cmux.CmuxRuntimeState.load(config)
    live_workspace_refs: set[str] | None = None
    try:
        live_workspace_refs = {
            str(workspace.get("ref") or workspace.get("id"))
            for workspace in adapter.list_workspaces()
            if workspace.get("ref") or workspace.get("id")
        }
        details["liveWorkspaces"] = sorted(live_workspace_refs)
    except Exception as error:
        details["liveWorkspaces"] = []
        checks.append({"name": "cmux-session-refs", "status": "WARN", "message": str(error)})
    details["sessions"] = [
        {
            "task_id": session.task_id,
            "run_id": session.run_id,
            "status": session.status,
            "workspace_ref": session.workspace_ref,
            "surface_ref": session.surface_ref,
            "surfaces": dict(session.layout.surfaces),
            "workspace_exists": (
                session.workspace_ref in live_workspace_refs
                if live_workspace_refs is not None and session.workspace_ref
                else None
            ),
        }
        for session in runtime.sessions.values()
    ]
    if not any(check["name"] == "cmux-session-refs" for check in checks):
        stale_count = sum(1 for session in details["sessions"] if session["workspace_exists"] is False)
        checks.append(
            {
                "name": "cmux-session-refs",
                "status": "WARN" if stale_count else "OK",
                "message": (
                    f"{stale_count} tracked sessions point at missing cmux workspaces"
                    if stale_count
                    else f"{len(runtime.sessions)} tracked sessions have live cmux workspace refs"
                ),
            }
        )
    checks.append({"name": "cmux-sessions", "status": "OK", "message": f"{len(runtime.sessions)} live conductor sessions tracked"})
    return details


def cli_provenance() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parents[1]
    executable = shutil.which("conductor")
    version = "unknown"
    direct_url: dict[str, Any] = {}
    try:
        version = metadata.version("vault-conductor")
        dist = metadata.distribution("vault-conductor")
        raw_direct_url = dist.read_text("direct_url.json")
        if raw_direct_url:
            direct_url = json.loads(raw_direct_url)
    except Exception:
        pass
    source_path = direct_url_source_path(direct_url)
    return {
        "executable": executable,
        "packageVersion": version,
        "pythonExecutable": sys.executable,
        "modulePath": str(Path(__file__).resolve()),
        "sourceCheckout": str(package_root),
        "directUrlSource": str(source_path) if source_path else None,
        "directUrlEditable": direct_url.get("dir_info", {}).get("editable") if direct_url else None,
        "matchesCheckout": bool(source_path and source_path.resolve() == package_root.resolve()),
    }


def direct_url_source_path(direct_url: dict[str, Any]) -> Path | None:
    url = direct_url.get("url")
    if not isinstance(url, str) or not url.startswith("file:"):
        return None
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        return None
    return Path(unquote(parsed.path)).expanduser()


def exists_check(name: str, path: Path, message: str) -> dict[str, str]:
    return {
        "name": name,
        "status": "OK" if path.exists() else "FAIL",
        "message": f"{message}: {path}" if path.exists() else f"Missing: {path}",
    }


def command_check(name: str, command: str, *, fail: bool = False) -> dict[str, str]:
    available = shutil.which(command) is not None
    return {
        "name": name,
        "status": "OK" if available else ("FAIL" if fail else "WARN"),
        "message": f"{command} available" if available else f"{command} not found",
    }


def sample_session_transcript(config: Config, task_id: str, session: dict[str, Any]) -> str | None:
    workspace_ref = session.get("workspace_ref")
    if not workspace_ref:
        return None
    text = cmux.read_screen(workspace_ref, surface_ref=session.get("surface_ref"))
    digest = transcript_hash(text)
    if digest != session.get("transcript_hash"):
        log_file = Path(session["log_file"])
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"\n--- transcript snapshot {now_iso()} ---\n{text}\n")
        session["transcript_hash"] = digest
        upsert_session(config, task_id, session)
    detected_activity = detect_agent_activity(text)
    if detected_activity:
        activity, detail = detected_activity
        try:
            record_activity(config, task_id, activity, detail=detail)
        except ValueError:
            pass
    detected = detect_agent_status(text)
    if detected:
        mark_task(config, task_id, detected)
        if session.get("run_id"):
            update_run_frontmatter(config, session["run_id"], {"status": detected, "ended": now_iso()})
        append_task_log(config, task_id, f"Detected AGENT_STATUS: {detected}.")
    return detected
