from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from . import cmux
from .agents import build_prompt, detect_agent_status, provider_command, template_variables
from .config import Config, config_to_yaml
from .constants import BOARD_COLUMNS, TASK_STATUSES
from .git_ops import (
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
from .repos import RepoEntry, find_repo, load_repo_registry, registry_path, scan_repos
from .run_notes import append_run_followup, create_run_note, update_run_frontmatter
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
from .git_ops import ensure_worktree


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

    for name, status_title in {
        "Needs Human.md": "Needs Human",
        "Review Queue.md": "Review Diff",
        "Running Agents.md": "Running",
        "Failed and Parked.md": "Failed / Parked",
    }.items():
        path = config.control_room_dir / name
        if not path.exists():
            write_file_atomic(path, f"# {name.removesuffix('.md')}\n\n```dataview\nTABLE status, repo, agent FROM \"20 Agent Tasks\" WHERE status = \"{status_title}\"\n```\n")

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


def mark_task(config: Config, task_id: str, status: str, *, human: bool = False) -> None:
    if status not in TASK_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    if status == "done" and not human:
        raise ValueError("Only a human may mark a task done. Rerun with --human after review/merge.")
    before = read_task_note(config, task_id)
    update_task_frontmatter(config, task_id, {"status": status})
    after = read_task_note(config, task_id)
    board = read_board(config)
    existing = find_card(board, task_id)
    line = update_card_line(
        existing.card.line if existing else build_card_line(before.frontmatter),
        task=after.frontmatter,
        checked=status == "done",
    )
    move_card(board, task_id, status_to_column(config, status), status=status, checked=status == "done", card_line=line)
    write_board(config, board)


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
    tasks = read_all_task_notes(config)
    board = read_board(config)
    for task in tasks:
        status = task.frontmatter.status
        if board_wins:
            located = find_card(board, task.frontmatter.id)
            board_status = status_from_column(config, located.column_title) if located else None
            if board_status and board_status != status:
                update_task_frontmatter(config, task.frontmatter.id, {"status": board_status})
                status = board_status
                task = read_task_note(config, task.frontmatter.id)
        located = find_card(board, task.frontmatter.id)
        line = update_card_line(
            located.card.line if located else build_card_line(task.frontmatter),
            task=task.frontmatter,
            checked=status == "done",
        )
        move_card(board, task.frontmatter.id, status_to_column(config, status), status=status, checked=status == "done", card_line=line)
    write_board(config, board)
    return {"synced": len(tasks)}


def start_task(config: Config, task_id: str) -> dict[str, str]:
    sessions = read_sessions(config)
    if task_id in sessions.get("sessions", {}):
        raise ValueError(f"Task {task_id} already has a live session")
    task = read_task_note(config, task_id)
    ensure_worktree(config, task)
    run = create_run_note(config, task)
    prompt = build_prompt(config, task, run)
    prompt_path = Path(run.frontmatter.prompt_file)
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    Path(run.frontmatter.log_file).parent.mkdir(parents=True, exist_ok=True)
    Path(run.frontmatter.log_file).write_text("", encoding="utf-8")

    variables = template_variables(config, task, run, prompt)
    cmux_command, _env = provider_command(config, task.frontmatter.agent, variables)
    workspace_ref = cmux.new_workspace(
        name=task.frontmatter.id,
        description=task.frontmatter.title,
        cwd=task.frontmatter.worktree,
        command=cmux_command,
        focus=False,
    )
    cmux.markdown_open(task.abs_path, workspace_ref)
    cmux.markdown_open(run.abs_path, workspace_ref)
    cmux.set_status(workspace_ref, "running")

    update_task_frontmatter(
        config,
        task_id,
        {
            "status": "running",
            "current_run": run.frontmatter.id,
            "run_count": task.frontmatter.run_count + 1,
            "workspace_ref": workspace_ref,
            "surface_ref": None,
            "cmux_command": cmux_command,
        },
    )
    update_run_frontmatter(
        config,
        run.frontmatter.id,
        {"workspace_ref": workspace_ref, "surface_ref": None, "cmux_command": cmux_command},
    )
    mark_task(config, task_id, "running")
    append_task_log(config, task_id, f"Agent started in `{workspace_ref}` with `{cmux_command}`.")
    instruction = (
        f"Please read the prompt file at {prompt_path} and follow it. "
        "Update the task status to review-diff, needs-human, or failed; if you cannot edit the note, print AGENT_STATUS."
    )
    cmux.send(workspace_ref, instruction)
    cmux.send_enter(workspace_ref)

    upsert_session(
        config,
        task_id,
        {
            "task_id": task_id,
            "run_id": run.frontmatter.id,
            "workspace_ref": workspace_ref,
            "surface_ref": None,
            "agent": task.frontmatter.agent,
            "worktree": task.frontmatter.worktree,
            "log_file": run.frontmatter.log_file,
            "status": "running",
            "cmux_command": cmux_command,
            "transcript_hash": "",
        },
    )
    return {
        "run_id": run.frontmatter.id,
        "log_file": run.frontmatter.log_file,
        "prompt_file": run.frontmatter.prompt_file,
        "workspace_ref": workspace_ref,
        "status": "running",
    }


def send_command(config: Config, task_id: str, message: str, *, status: str | None = None) -> dict[str, Any]:
    task = read_task_note(config, task_id)
    if task.frontmatter.current_run:
        append_run_followup(config, task.frontmatter.current_run, message)
        followup_file = config.prompts_root / f"{task.frontmatter.current_run}.followups.md"
        followup_file.parent.mkdir(parents=True, exist_ok=True)
        with followup_file.open("a", encoding="utf-8") as handle:
            handle.write(f"{now_iso()} {message}\n")
    append_task_log(config, task_id, f"Human instruction: {message}")
    session = read_sessions(config).get("sessions", {}).get(task_id)
    if session and session.get("workspace_ref"):
        cmux.send(session["workspace_ref"], message)
        cmux.send_enter(session["workspace_ref"])
    if status:
        mark_task(config, task_id, status)
    return {"saved": True, "sent": bool(session), "message": message}


def stop_task(config: Config, task_id: str, *, park: bool = False, kill: bool = False) -> str:
    session = read_sessions(config).get("sessions", {}).get(task_id)
    if not session:
        raise ValueError(f"No live session found for {task_id}")
    workspace_ref = session.get("workspace_ref")
    if workspace_ref:
        cmux.close_workspace(workspace_ref)
    status = "parked" if park else "failed"
    run_id = session.get("run_id")
    if run_id:
        update_run_frontmatter(config, run_id, {"status": status, "ended": now_iso(), "exit_code": -15})
    mark_task(config, task_id, status)
    update_task_frontmatter(config, task_id, {"workspace_ref": None, "surface_ref": None})
    remove_session(config, task_id)
    return status


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
    registry = load_repo_registry(config)
    repo = next((entry for entry in registry.get("repos", []) if entry.get("name") == task.frontmatter.repo), {})
    command = task.frontmatter.test_command or (repo.get("commands") or {}).get("test") or config.commands.get("default_test")
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


def pr_command(
    config: Config,
    task_id: str,
    *,
    commit: bool = False,
    yes: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> str:
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


def pr_body(task) -> str:
    return f"""## Agent Control Room Task

Task: [[{task.path}]]
ID: {task.frontmatter.id}
Repo: {task.frontmatter.repo}
Agent: {task.frontmatter.agent}
Run: {task.frontmatter.current_run or ""}

## Summary

See task note diff summary.

## Tests

See task note test output.

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
    return {
        "checks": checks,
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
    text = cmux.read_screen(workspace_ref)
    digest = transcript_hash(text)
    if digest != session.get("transcript_hash"):
        log_file = Path(session["log_file"])
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"\n--- transcript snapshot {now_iso()} ---\n{text}\n")
        session["transcript_hash"] = digest
        upsert_session(config, task_id, session)
    detected = detect_agent_status(text)
    if detected:
        mark_task(config, task_id, detected)
        if session.get("run_id"):
            update_run_frontmatter(config, session["run_id"], {"status": detected, "ended": now_iso()})
        append_task_log(config, task_id, f"Detected AGENT_STATUS: {detected}.")
    return detected
