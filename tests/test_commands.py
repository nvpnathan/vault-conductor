import json
from pathlib import Path

import pytest
import yaml

from vault_conductor.commands import (
    activity_command,
    doctor_command,
    init_command,
    mark_task,
    new_task_command,
    pr_command,
    send_command,
    start_task,
    sync_command,
)
from vault_conductor.kanban import find_card, parse_board, render_board
from vault_conductor.sessions import read_sessions
from vault_conductor.tasks import read_task_note

from conftest import cmux_calls, git


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


def test_sync_migrates_project_notes_to_readable_task_tables(config, fake_git_repo):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    project = config.projects_dir / "demo.md"
    project.write_text(
        """---
type: project
repo: demo
repo_path: ~/repos/demo
default_branch: main
default_agent: codex
status: active
created: 2026-05-27T00:00:00Z
updated: 2026-05-27T00:00:00Z
---
# Repo

demo is registered at `~/repos/demo`.

# Common commands

- test: `custom test`

# Agent rules

- Keep changes small.

# Active tasks

old active list

# Completed tasks

old completed list
""",
        encoding="utf-8",
    )
    created = new_task_command(config, repo="demo", title="Active item", status="ready")

    sync_command(config)

    text = project.read_text(encoding="utf-8")
    assert "- test: `custom test`" in text
    assert "- Keep changes small." in text
    assert "old active list" not in text
    assert "old completed list" not in text
    assert "```dataview" not in text
    assert "| Task | Status | Priority | Agent | Updated |" in text
    assert f"[[20 Agent Tasks/{created['id']} Active item]]" in text
    assert "| No completed tasks. |  |  | |" in text

    mark_task(config, created["id"], "done", human=True)

    text = project.read_text(encoding="utf-8")
    assert "| No active tasks. |  |  |  |" in text
    assert "| Task | Completed | Agent | Tests |" in text
    assert f"[[20 Agent Tasks/{created['id']} Active item]]" in text


def test_status_changes_are_logged_to_task_note_and_operational_log(config, fake_git_repo):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Log status changes", status="ready")

    mark_task(config, created["id"], "review-diff")
    mark_task(config, created["id"], "done", human=True)

    task_text = (config.tasks_dir / "AGT-0001 Log status changes.md").read_text(encoding="utf-8")
    operational_log = (config.logs_root / "conductor-watch.log").read_text(encoding="utf-8")

    assert "Status changed: ready -> review-diff." in task_text
    assert "Status changed: review-diff -> done." in task_text
    assert "conductor-status status changed task=AGT-0001 from=ready to=review-diff repo=demo actor=conductor source=mark" in operational_log
    assert "conductor-status status changed task=AGT-0001 from=review-diff to=done repo=demo actor=human source=mark" in operational_log


def test_sync_board_wins_logs_status_changes(config, fake_git_repo):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Log board sync", status="ready")

    move_card_only_on_board(config, created["id"], "Running")
    sync_command(config, board_wins=True)

    operational_log = (config.logs_root / "conductor-watch.log").read_text(encoding="utf-8")
    assert "conductor-status status changed task=AGT-0001 from=ready to=running repo=demo actor=human source=sync-board-wins" in operational_log


def test_init_repairs_legacy_agentctl_dashboard_notes(config):
    init_command(config, open_obsidian=False)
    dashboard_names = [
        "Needs Human.md",
        "Review Queue.md",
        "Running Agents.md",
        "Failed and Parked.md",
    ]
    for name in dashboard_names:
        (config.control_room_dir / name).write_text(
            f"# {name.removesuffix('.md')}\n\n```bash\nagentctl status\nagentctl mark <TASK_ID> running\n```\n",
            encoding="utf-8",
        )

    init_command(config, open_obsidian=False)

    for name in dashboard_names:
        text = (config.control_room_dir / name).read_text(encoding="utf-8")
        assert "agentctl" not in text
        assert "conductor" in text
        assert "uv run conductor" not in text
    assert "needs-human" in (config.control_room_dir / "Needs Human.md").read_text(encoding="utf-8")
    assert "review-diff" in (config.control_room_dir / "Review Queue.md").read_text(encoding="utf-8")
    assert "running" in (config.control_room_dir / "Running Agents.md").read_text(encoding="utf-8")
    assert "failed or parked" in (config.control_room_dir / "Failed and Parked.md").read_text(encoding="utf-8")


def test_start_creates_cmux_session_run_prompt_and_sends_prompt_file_instruction(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Start in cmux", status="ready")

    result = start_task(config, created["id"])

    task = read_task_note(config, created["id"])
    sessions = read_sessions(config)
    calls = cmux_calls(fake_cmux)
    new_workspace_calls = [call for call in calls if "new-workspace" in call]
    new_workspace_call = new_workspace_calls[0]
    send_call = next(call for call in calls if call[:1] == ["send"])

    assert result["run_id"] == "AGT-0001-RUN-001"
    assert len(new_workspace_calls) == 1
    assert task.frontmatter.status == "running"
    assert task.frontmatter.workspace_ref == "workspace:1"
    assert task.frontmatter.surface_ref == "surface:1"
    assert task.frontmatter.cmux_command == "cmux codex-teams"
    assert (config.worktrees_root / "demo" / created["id"]).is_dir()
    prompt_text = (config.prompts_root / "AGT-0001-RUN-001.prompt.md").read_text(encoding="utf-8")
    assert prompt_text.startswith("# Agent Control Room Task")
    assert "conductor pr AGT-0001 --auto" in prompt_text
    assert (config.runs_dir / "AGT-0001-RUN-001-activity.md").exists()
    assert "codex-teams" in " ".join(new_workspace_call)
    markdown_calls = [call for call in calls if call[:2] == ["markdown", "open"]]
    assert markdown_calls[:2] == [
        [
            "markdown",
            "open",
            str(config.runs_dir / "AGT-0001-RUN-001-codex.md"),
            "--workspace",
            "workspace:1",
            "--surface",
            "surface:1",
            "--direction",
            "right",
            "--focus",
            "false",
        ],
        [
            "markdown",
            "open",
            str(config.tasks_dir / "AGT-0001 Start in cmux.md"),
            "--workspace",
            "workspace:1",
            "--surface",
            "surface:2",
            "--direction",
            "down",
            "--focus",
            "false",
        ],
    ]
    assert "read the prompt file" in " ".join(send_call)
    assert send_call[:5] == ["send", "--workspace", "workspace:1", "--surface", "surface:1"]
    assert str(config.prompts_root / "AGT-0001-RUN-001.prompt.md") in " ".join(send_call)
    assert sessions["sessions"][created["id"]]["workspace_ref"] == "workspace:1"
    assert sessions["sessions"][created["id"]]["surface_ref"] == "surface:1"
    assert sessions["sessions"][created["id"]]["activity_file"] == str(config.runs_dir / "AGT-0001-RUN-001-activity.md")

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
    assert any(
        call[:5] == ["send", "--workspace", "workspace:1", "--surface", "surface:1"]
        and "Please add the regression test." in " ".join(call)
        for call in calls
    )
    assert any(
        call[:5] == ["send-key", "--workspace", "workspace:1", "--surface", "surface:1"] and "enter" in call
        for call in calls
    )


def test_activity_updates_task_timeline_and_cmux_status(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Report activity", status="ready")
    start_task(config, created["id"])

    result = activity_command(config, created["id"], "testing", detail="Running pytest")

    task = read_task_note(config, created["id"])
    calls = cmux_calls(fake_cmux)
    timeline = config.runs_dir / "AGT-0001-RUN-001-activity.md"

    assert result["activity"] == "testing"
    assert task.frontmatter.current_activity == "testing"
    assert task.frontmatter.current_activity_detail == "Running pytest"
    assert "Testing - Running pytest" in timeline.read_text(encoding="utf-8")
    assert any(call[:3] == ["set-status", "agent_activity", "Testing"] for call in calls)
    assert any(call[:2] == ["log", "--source"] and "Testing: Running pytest" in call for call in calls)

    with pytest.raises(ValueError, match="Unknown activity"):
        activity_command(config, created["id"], "vibing")


def test_auto_pr_handoff_runs_tests_creates_pr_and_opens_cmux_browser(
    config,
    fake_git_repo,
    fake_cmux,
    tmp_path,
):
    remote = tmp_path / "remote.git"
    git(["init", "--bare", str(remote)], tmp_path)
    git(["remote", "add", "origin", str(remote)], fake_git_repo)
    git(["push", "-u", "origin", "main"], fake_git_repo)
    gh = fake_cmux.parent / "bin" / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s\\n' 'https://github.test/demo/pull/1'\n", encoding="utf-8")
    gh.chmod(0o755)

    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(
        config,
        repo="demo",
        title="Open PR",
        status="ready",
        test_command="python -c 'print(\"ok\")'",
    )
    start_task(config, created["id"])
    task = read_task_note(config, created["id"])
    Path(task.frontmatter.worktree, "change.txt").write_text("changed\n", encoding="utf-8")
    mark_task(config, created["id"], "review-diff")

    url = pr_command(config, created["id"], auto=True)

    task = read_task_note(config, created["id"])
    calls = cmux_calls(fake_cmux)
    assert url == "https://github.test/demo/pull/1"
    assert task.frontmatter.status == "pr-opened"
    assert task.frontmatter.pr_url == url
    assert task.frontmatter.last_test_status == "passed"
    assert "Exit code: 0" in (config.prompts_root / "AGT-0001-pr-body.md").read_text(encoding="utf-8")
    assert any(call[:4] == ["new-pane", "--type", "browser", "--direction"] and url in call for call in calls)
    assert any(call[:2] == ["select-workspace", "--workspace"] and "workspace:1" in call for call in calls)


def test_auto_pr_handoff_creates_pr_from_already_committed_branch(config, fake_git_repo, fake_cmux, tmp_path):
    remote = tmp_path / "remote.git"
    git(["init", "--bare", str(remote)], tmp_path)
    git(["remote", "add", "origin", str(remote)], fake_git_repo)
    git(["push", "-u", "origin", "main"], fake_git_repo)
    gh = fake_cmux.parent / "bin" / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s\n' 'https://github.test/demo/pull/2'\n", encoding="utf-8")
    gh.chmod(0o755)

    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(
        config,
        repo="demo",
        title="Open PR from pushed branch",
        status="ready",
        test_command="python -c 'print(\"ok\")'",
    )
    start_task(config, created["id"])
    task = read_task_note(config, created["id"])
    Path(task.frontmatter.worktree, "change.txt").write_text("changed\n", encoding="utf-8")
    git(["add", "change.txt"], task.frontmatter.worktree)
    git(["commit", "-m", "Manual agent commit"], task.frontmatter.worktree)
    git(["push", "-u", "origin", task.frontmatter.branch], task.frontmatter.worktree)
    mark_task(config, created["id"], "review-diff")

    url = pr_command(config, created["id"], auto=True)

    task = read_task_note(config, created["id"])
    assert url == "https://github.test/demo/pull/2"
    assert task.frontmatter.status == "pr-opened"
    assert task.frontmatter.pr_url == url


def test_auto_pr_handoff_stops_when_tests_fail(config, fake_git_repo, fake_cmux, tmp_path):
    remote = tmp_path / "remote.git"
    git(["init", "--bare", str(remote)], tmp_path)
    git(["remote", "add", "origin", str(remote)], fake_git_repo)
    git(["push", "-u", "origin", "main"], fake_git_repo)

    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(
        config,
        repo="demo",
        title="Fail tests",
        status="ready",
        test_command="python -c 'import sys; sys.exit(3)'",
    )
    start_task(config, created["id"])
    task = read_task_note(config, created["id"])
    Path(task.frontmatter.worktree, "change.txt").write_text("changed\n", encoding="utf-8")
    mark_task(config, created["id"], "review-diff")

    with pytest.raises(ValueError, match="Tests failed"):
        pr_command(config, created["id"], auto=True)

    task = read_task_note(config, created["id"])
    calls = cmux_calls(fake_cmux)
    assert task.frontmatter.status == "review-diff"
    assert task.frontmatter.pr_url is None
    assert task.frontmatter.last_test_status == "failed"
    assert any(call[:2] == ["notify", "--title"] and "Tests failed" in call for call in calls)


def test_start_waits_for_codex_before_sending_prompt_instruction(config, fake_git_repo, fake_cmux, monkeypatch):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Wait for Codex", status="ready")
    monkeypatch.setenv(
        "FAKE_CMUX_SCREEN_SEQUENCE",
        "Last login\n$ cmux codex-teams\fOpenAI Codex\nFind and fix a bug in @filename",
    )

    start_task(config, created["id"])

    calls = cmux_calls(fake_cmux)
    read_index = next(index for index, call in enumerate(calls) if "read-screen" in call)
    send_index = next(index for index, call in enumerate(calls) if call[:1] == ["send"])
    assert read_index < send_index
    assert calls[send_index][:5] == ["send", "--workspace", "workspace:1", "--surface", "surface:1"]


def test_mark_updates_live_session_and_cmux_status(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Ready for review", status="ready")
    start_task(config, created["id"])

    mark_task(config, created["id"], "review-diff")

    session = read_sessions(config)["sessions"][created["id"]]
    calls = cmux_calls(fake_cmux)
    assert session["status"] == "review-diff"
    assert any(
        call[:3] == ["set-status", "agent", "review-diff"] and "--workspace" in call and "workspace:1" in call
        for call in calls
    )


def test_doctor_json_reports_cmux_and_runtime_dirs(config, fake_cmux):
    init_command(config, open_obsidian=False)

    result = doctor_command(config, fix=True)
    checks = {check["name"]: check["status"] for check in result["checks"]}

    assert checks["vault"] == "OK"
    assert checks["board"] == "OK"
    assert checks["columns"] == "OK"
    assert checks["cmux"] == "OK"
    assert "conductor-cli" in checks
    assert "packageVersion" in result["cli"]
    assert "modulePath" in result["cli"]
    assert result["paths"]["stateRoot"] == str(config.state_root)
    json.dumps(result)


def move_card_only_on_board(config, task_id: str, column: str):
    from vault_conductor.kanban import move_card

    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    move_card(board, task_id, column)
    config.board_path.write_text(render_board(board), encoding="utf-8")
