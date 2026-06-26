import json

import pytest

from vault_conductor.commands import init_command, new_task_command, start_task
from vault_conductor.run_notes import read_run_note
from vault_conductor.sessions import read_sessions, write_sessions
from vault_conductor.tasks import read_task_note, update_task_frontmatter
from vault_conductor.watch import reconcile_closed_workspaces, repair_stale_sessions, watch_forever, watch_once

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


def test_repair_stale_sessions_returns_repair_details_and_removes_layout(config, fake_git_repo):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Repair stale layout", status="ready")
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
                    "cmux_layout": {
                        "workspace_ref": "workspace:9",
                        "surfaces": {
                            "agent": "surface:10",
                            "run_note": "surface:11",
                        },
                    },
                }
            },
        },
    )

    repairs = repair_stale_sessions(config, live_workspace_refs=set())

    assert len(repairs) == 1
    assert repairs[0].task_id == created["id"]
    assert repairs[0].workspace_ref == "workspace:9"
    assert repairs[0].reason == "workspace-missing"
    assert repairs[0].action == "needs-human"
    assert repairs[0].was_running is True
    assert repairs[0].session_removed is True
    task = read_task_note(config, created["id"]).frontmatter
    assert task.status == "needs-human"
    assert task.workspace_ref is None
    assert task.surface_ref is None
    assert read_sessions(config)["sessions"] == {}


def test_repair_done_task_with_missing_workspace_closes_stale_session_and_preserves_run_evidence(
    config, fake_git_repo, fake_cmux
):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Done stale session", status="ready")
    result = start_task(config, created["id"])
    update_task_frontmatter(
        config,
        created["id"],
        {"status": "done", "workspace_ref": "workspace:999", "surface_ref": "surface:999"},
    )
    sessions = read_sessions(config)
    session = sessions["sessions"][created["id"]]
    session.update({"status": "done", "workspace_ref": "workspace:999", "surface_ref": "surface:999"})
    write_sessions(config, sessions)

    repairs = repair_stale_sessions(config, live_workspace_refs={"workspace:1"})

    assert len(repairs) == 1
    assert repairs[0].task_id == created["id"]
    assert repairs[0].reason == "workspace-missing"
    assert repairs[0].action == "closed"
    assert repairs[0].was_running is False
    assert repairs[0].session_removed is True
    task = read_task_note(config, created["id"]).frontmatter
    assert task.status == "done"
    assert task.workspace_ref is None
    assert task.surface_ref is None
    assert read_sessions(config)["sessions"] == {}
    run = read_run_note(config, result["run_id"])
    assert "Session repair" in run.body
    assert "workspace-missing" in run.body
    assert "workspace:999" in run.body


def test_repair_missing_surface_in_live_workspace_keeps_session_and_needs_human(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Missing surface", status="ready")
    result = start_task(config, created["id"])
    update_task_frontmatter(config, created["id"], {"surface_ref": "surface:999"})
    sessions = read_sessions(config)
    session = sessions["sessions"][created["id"]]
    session.update(
        {
            "status": "running",
            "surface_ref": "surface:999",
            "cmux_layout": {
                **session["cmux_layout"],
                "surfaces": {**session["cmux_layout"]["surfaces"], "agent": "surface:999"},
            },
        }
    )
    write_sessions(config, sessions)

    repairs = repair_stale_sessions(config)

    assert len(repairs) == 1
    assert repairs[0].task_id == created["id"]
    assert repairs[0].workspace_ref == "workspace:1"
    assert repairs[0].surface_ref == "surface:999"
    assert repairs[0].reason == "surface-missing"
    assert repairs[0].action == "needs-human"
    assert repairs[0].session_removed is False
    task = read_task_note(config, created["id"]).frontmatter
    assert task.status == "needs-human"
    assert task.workspace_ref == "workspace:1"
    assert task.surface_ref is None
    repaired_session = read_sessions(config)["sessions"][created["id"]]
    assert repaired_session["status"] == "needs-human"
    assert repaired_session["workspace_ref"] == "workspace:1"
    assert repaired_session["surface_ref"] is None
    assert "agent" not in repaired_session["cmux_layout"]["surfaces"]
    run = read_run_note(config, result["run_id"])
    assert "Session repair" in run.body
    assert "surface-missing" in run.body
    assert "surface:999" in run.body


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
