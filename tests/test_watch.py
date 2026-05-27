import json

import pytest

from vault_conductor.commands import init_command, new_task_command
from vault_conductor.sessions import read_sessions, write_sessions
from vault_conductor.tasks import read_task_note, update_task_frontmatter
from vault_conductor.watch import reconcile_closed_workspaces, watch_forever, watch_once

from conftest import cmux_calls
from test_commands import move_card_only_on_board, write_registry


def test_watch_starts_tasks_dragged_to_running_and_detects_agent_status(config, fake_git_repo, fake_cmux, monkeypatch):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Watch me", status="ready")

    move_card_only_on_board(config, created["id"], "Running")
    watch_once(config)

    assert read_task_note(config, created["id"]).frontmatter.status == "running"
    assert any("new-workspace" in call for call in cmux_calls(fake_cmux))

    monkeypatch.setenv("FAKE_CMUX_SCREEN", "work complete\nAGENT_STATUS: review-diff\n")
    watch_once(config)

    assert read_task_note(config, created["id"]).frontmatter.status == "review-diff"


def test_watch_detects_agent_activity_fallback(config, fake_git_repo, fake_cmux, monkeypatch):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Watch activity", status="ready")

    move_card_only_on_board(config, created["id"], "Running")
    watch_once(config)

    monkeypatch.setenv("FAKE_CMUX_SCREEN", "AGENT_ACTIVITY: reading | Inspecting commands\n")
    watch_once(config)

    task = read_task_note(config, created["id"]).frontmatter
    assert task.current_activity == "reading"
    assert task.current_activity_detail == "Inspecting commands"
    assert "Reading - Inspecting commands" in (config.runs_dir / "AGT-0001-RUN-001-activity.md").read_text(
        encoding="utf-8"
    )


def test_watch_logs_activity_without_polling_noise_by_default(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Watch logs", status="ready")
    logs: list[str] = []

    move_card_only_on_board(config, created["id"], "Running")
    watch_once(config, log=logs.append)

    assert not any(line.startswith("poll ") for line in logs)
    assert not any(line.startswith("sampling sessions") for line in logs)
    assert not any(line.startswith("sample ") for line in logs)
    assert not any("transcript changed" in line for line in logs)
    assert not any("transcript unchanged" in line for line in logs)
    assert any(f"start requested task={created['id']}" in line for line in logs)
    assert any(f"started task={created['id']} run={created['id']}-RUN-001 workspace=workspace:1" in line for line in logs)


def test_watch_verbose_logs_polling_and_sampling_details(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Verbose watch logs", status="ready")
    logs: list[str] = []

    move_card_only_on_board(config, created["id"], "Running")
    watch_once(config, log=logs.append, verbose=True)

    assert "poll tasks=1 sessions=0 pending_starts=1" in logs
    assert "sampling sessions count=1" in logs
    assert any(f"sample task={created['id']} workspace=workspace:1 surface=surface:1" in line for line in logs)
    assert any(f"transcript changed task={created['id']} workspace=workspace:1" in line for line in logs)


def test_watch_forever_logs_startup_and_interrupt(config, monkeypatch):
    logs: list[str] = []

    def stop_after_start(config, *, log=None, verbose=False):
        raise KeyboardInterrupt

    monkeypatch.setattr("vault_conductor.watch.watch_once", stop_after_start)

    with pytest.raises(KeyboardInterrupt):
        watch_forever(config, poll_interval=0, log=logs.append)

    assert logs[0].startswith(f"started vault={config.vault_path}")
    assert logs[-1] == "stopped by keyboard interrupt"


def test_closed_workspace_while_task_still_running_moves_to_needs_human(config, fake_git_repo):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Workspace closed", status="ready")
    update_task_frontmatter(config, created["id"], {"status": "running", "workspace_ref": "workspace:9"})
    write_sessions(
        config,
        {
            "version": 1,
            "sessions": {
                created["id"]: {
                    "task_id": created["id"],
                    "run_id": "AGT-0001-RUN-001",
                    "workspace_ref": "workspace:9",
                    "surface_ref": None,
                    "agent": "codex",
                    "worktree": str(config.worktrees_root / "demo" / created["id"]),
                    "log_file": str(config.logs_root / "AGT-0001-RUN-001.log"),
                    "status": "running",
                    "transcript_hash": "",
                }
            },
        },
    )

    changed = reconcile_closed_workspaces(config, live_workspace_refs=set())

    assert changed == [created["id"]]
    task = read_task_note(config, created["id"]).frontmatter
    assert task.status == "needs-human"
    assert task.workspace_ref is None
    assert read_sessions(config)["sessions"] == {}
    assert "workspace workspace:9 closed while task was still running" in (
        config.tasks_dir / "AGT-0001 Workspace closed.md"
    ).read_text(encoding="utf-8")


def test_reconcile_keeps_session_when_workspace_list_temporarily_misses_live_workspace(config, fake_git_repo, monkeypatch):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="List miss", status="ready")
    update_task_frontmatter(config, created["id"], {"status": "running", "workspace_ref": "workspace:9", "surface_ref": "surface:10"})
    write_sessions(
        config,
        {
            "version": 1,
            "sessions": {
                created["id"]: {
                    "task_id": created["id"],
                    "run_id": "AGT-0001-RUN-001",
                    "workspace_ref": "workspace:9",
                    "surface_ref": "surface:10",
                    "agent": "codex",
                    "worktree": str(config.worktrees_root / "demo" / created["id"]),
                    "log_file": str(config.logs_root / "AGT-0001-RUN-001.log"),
                    "status": "running",
                    "transcript_hash": "",
                }
            },
        },
    )
    monkeypatch.setattr("vault_conductor.cmux.list_workspaces", lambda: [])
    monkeypatch.setattr("vault_conductor.cmux.workspace_exists", lambda workspace_ref: workspace_ref == "workspace:9")

    changed = reconcile_closed_workspaces(config)

    assert changed == []
    assert read_task_note(config, created["id"]).frontmatter.status == "running"
    assert created["id"] in read_sessions(config)["sessions"]
