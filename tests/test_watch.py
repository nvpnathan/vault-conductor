import json

from vault_conductor.commands import init_command, new_task_command
from vault_conductor.sessions import read_sessions, write_sessions
from vault_conductor.tasks import read_task_note, update_task_frontmatter
from vault_conductor.watch import reconcile_closed_workspaces, watch_once

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
