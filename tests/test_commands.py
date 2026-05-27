import json
from pathlib import Path

import pytest
import yaml

from vault_conductor.commands import (
    doctor_command,
    init_command,
    mark_task,
    new_task_command,
    send_command,
    start_task,
    sync_command,
)
from vault_conductor.kanban import find_card, parse_board, render_board
from vault_conductor.sessions import read_sessions
from vault_conductor.tasks import read_task_note

from conftest import cmux_calls


def write_registry(config, repo):
    registry = {
        "version": 1,
        "repos": [
            {
                "name": "demo",
                "path": str(repo),
                "default_branch": "main",
                "default_agent": "codex",
                "status": "active",
                "last_scanned": "2026-05-27T00:00:00Z",
                "commands": {"test": "python -m pytest"},
            }
        ],
    }
    path = config.vault_path / "90 System" / "repo-registry.yml"
    path.write_text(yaml.safe_dump(registry), encoding="utf-8")


def test_new_mark_and_sync_treat_task_note_as_authoritative(config, fake_git_repo):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)

    created = new_task_command(
        config,
        repo="demo",
        title="Fix status drift",
        status="ready",
        goal="Keep note and board status aligned.",
        acceptance=["Board card points at the task note."],
    )

    task = read_task_note(config, created["id"])
    assert task.frontmatter.status == "ready"
    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    assert find_card(board, created["id"]).column_title == "Ready"

    with pytest.raises(ValueError, match="Only a human"):
        mark_task(config, created["id"], "done")

    mark_task(config, created["id"], "review-diff")
    task = read_task_note(config, created["id"])
    assert task.frontmatter.status == "review-diff"
    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    assert find_card(board, created["id"]).column_title == "Review Diff"

    move_card_only_on_board(config, created["id"], "Running")
    sync_command(config, board_wins=False)
    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    assert find_card(board, created["id"]).column_title == "Review Diff"
    assert read_task_note(config, created["id"]).frontmatter.status == "review-diff"

    move_card_only_on_board(config, created["id"], "Running")
    sync_command(config, board_wins=True)
    assert read_task_note(config, created["id"]).frontmatter.status == "running"


def test_start_creates_cmux_session_run_prompt_and_sends_prompt_file_instruction(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Start in cmux", status="ready")

    result = start_task(config, created["id"])

    task = read_task_note(config, created["id"])
    sessions = read_sessions(config)
    calls = cmux_calls(fake_cmux)
    new_workspace_call = next(call for call in calls if "new-workspace" in call)
    send_call = next(call for call in calls if call[:1] == ["send"])

    assert result["run_id"] == "AGT-0001-RUN-001"
    assert task.frontmatter.status == "running"
    assert task.frontmatter.workspace_ref == "workspace:1"
    assert task.frontmatter.cmux_command == "cmux codex-teams"
    assert (config.worktrees_root / "demo" / created["id"]).is_dir()
    assert (config.prompts_root / "AGT-0001-RUN-001.prompt.md").read_text(encoding="utf-8").startswith(
        "# Agent Control Room Task"
    )
    assert "codex-teams" in " ".join(new_workspace_call)
    assert "read the prompt file" in " ".join(send_call)
    assert str(config.prompts_root / "AGT-0001-RUN-001.prompt.md") in " ".join(send_call)
    assert sessions["sessions"][created["id"]]["workspace_ref"] == "workspace:1"

    with pytest.raises(ValueError, match="already has a live session"):
        start_task(config, created["id"])


def test_send_appends_notes_and_forwards_to_live_cmux_session(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Needs followup", status="ready")
    start_task(config, created["id"])

    send_command(config, created["id"], "Please add the regression test.", status="needs-human")

    task_path = config.tasks_dir / "AGT-0001 Needs followup.md"
    run_path = config.runs_dir / "AGT-0001-RUN-001-codex.md"
    calls = cmux_calls(fake_cmux)

    assert "Human instruction: Please add the regression test." in task_path.read_text(encoding="utf-8")
    assert "Please add the regression test." in run_path.read_text(encoding="utf-8")
    assert read_task_note(config, created["id"]).frontmatter.status == "needs-human"
    assert any(call[:1] == ["send"] and "Please add the regression test." in " ".join(call) for call in calls)
    assert any(call[:1] == ["send-key"] and "enter" in call for call in calls)


def test_doctor_json_reports_cmux_and_runtime_dirs(config, fake_cmux):
    init_command(config, open_obsidian=False)

    result = doctor_command(config, fix=True)
    checks = {check["name"]: check["status"] for check in result["checks"]}

    assert checks["vault"] == "OK"
    assert checks["board"] == "OK"
    assert checks["columns"] == "OK"
    assert checks["cmux"] == "OK"
    assert result["paths"]["stateRoot"] == str(config.state_root)
    json.dumps(result)


def move_card_only_on_board(config, task_id: str, column: str):
    from vault_conductor.kanban import move_card

    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    move_card(board, task_id, column)
    config.board_path.write_text(render_board(board), encoding="utf-8")
