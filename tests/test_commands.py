import json
import os
from pathlib import Path

import pytest
import yaml

from vault_conductor.commands import (
    activity_command,
    doctor_command,
    init_command,
    install_skill_command,
    mark_task,
    new_task_command,
    pr_command,
    repair_sessions_command,
    send_command,
    start_task,
    status_command,
    sync_command,
)
from vault_conductor.cli import main as cli_main
from vault_conductor.kanban import find_card, parse_board, render_board
from vault_conductor.sessions import read_sessions, write_sessions
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
    assert "--question" in (config.control_room_dir / "Needs Human.md").read_text(encoding="utf-8")
    assert "--question" in (config.control_room_dir / "Running Agents.md").read_text(encoding="utf-8")
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
    assert "AGENT_QUESTION: <one specific question?>" in prompt_text
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
    assert "AGENT_QUESTION" in " ".join(send_call)
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

    result = send_command(
        config,
        created["id"],
        "Please add the regression test.",
        status="needs-human",
        human_question="Should the regression test cover the CLI path?",
    )

    task_path = config.tasks_dir / "AGT-0001 Needs followup.md"
    run_path = config.runs_dir / "AGT-0001-RUN-001-codex.md"
    calls = cmux_calls(fake_cmux)
    task = read_task_note(config, created["id"])
    artifacts = list(Path(os.environ["VAULT_CONDUCTOR_ASSET_ROOT"]).rglob("*needs-human.html"))

    assert "Human instruction: Please add the regression test." in task_path.read_text(encoding="utf-8")
    assert "Should the regression test cover the CLI path?" in task_path.read_text(encoding="utf-8")
    assert "Please add the regression test." in run_path.read_text(encoding="utf-8")
    assert task.frontmatter.status == "needs-human"
    assert task.frontmatter.human_question == "Should the regression test cover the CLI path?"
    assert result["humanQuestion"] == "Should the regression test cover the CLI path?"
    assert result["handoffArtifact"]
    assert len(artifacts) == 1
    assert "Should the regression test cover the CLI path?" in artifacts[0].read_text(encoding="utf-8")
    assert any(
        call[:5] == ["send", "--workspace", "workspace:1", "--surface", "surface:1"]
        and "Please add the regression test." in " ".join(call)
        for call in calls
    )
    assert any(
        call[:5] == ["send-key", "--workspace", "workspace:1", "--surface", "surface:1"] and "enter" in call
        for call in calls
    )


def test_cli_send_reports_when_live_cmux_delivery_fails(config, fake_git_repo, fake_cmux, monkeypatch, capsys):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="CLI send failure", status="ready")
    start_task(config, created["id"])
    mark_task(config, created["id"], "needs-human", human_question="Should conductor keep the gate open?")
    monkeypatch.setenv("FAKE_CMUX_FAIL_SEND", "1")

    exit_code = cli_main(
        [
            "--vault",
            str(config.vault_path),
            "--repos",
            str(config.repos_root),
            "--runtime-root",
            str(config.state_root.parent),
            "send",
            created["id"],
            "Yes, keep it open.",
            "--status",
            "running",
        ]
    )

    output = capsys.readouterr().out
    task = read_task_note(config, created["id"]).frontmatter
    assert exit_code == 0
    assert "saved but not sent" in output
    assert "human question remains open" in output
    assert task.status == "needs-human"
    assert task.human_question_status == "open"


def test_status_reports_human_question(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Question status", status="ready")
    start_task(config, created["id"])

    mark_task(config, created["id"], "needs-human", human_question="Which API shape should be preserved?")

    result = status_command(config)
    task = next(item for item in result["tasks"] if item["id"] == created["id"])
    assert task["human_question"] == "Which API shape should be preserved?"
    assert task["human_question_status"] == "open"
    assert task["human_handoff_artifact"]


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
    assert any(call[:2] == ["set-progress", "0.65"] and "--label" in call and "Testing" in call for call in calls)
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
    session = read_sessions(config)["sessions"][created["id"]]
    artifacts = list(Path(os.environ["VAULT_CONDUCTOR_ASSET_ROOT"]).rglob("*pr-review.html"))
    assert url == "https://github.test/demo/pull/1"
    assert task.frontmatter.status == "pr-opened"
    assert task.frontmatter.pr_url == url
    assert task.frontmatter.last_test_status == "passed"
    assert "Exit code: 0" in (config.prompts_root / "AGT-0001-pr-body.md").read_text(encoding="utf-8")
    assert len(artifacts) == 1
    artifact_html = artifacts[0].read_text(encoding="utf-8")
    assert "PR review" in artifact_html
    assert url in artifact_html
    assert "Fake PR" in artifact_html
    assert session["cmux_layout"]["panes"]["helper"] == "pane:4"
    assert session["cmux_layout"]["surfaces"]["review_browser"] == "surface:4"
    assert any(call[:4] == ["new-pane", "--type", "browser", "--direction"] and url in call and call[-2:] == ["--focus", "false"] for call in calls)
    assert any(call[:3] == ["--json", "browser", "surface:4"] and "snapshot" in call for call in calls)
    assert not any(call[:2] == ["select-workspace", "--workspace"] for call in calls)


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
    artifacts = list(Path(os.environ["VAULT_CONDUCTOR_ASSET_ROOT"]).rglob("*pr-failure.html"))
    assert task.frontmatter.status == "review-diff"
    assert task.frontmatter.pr_url is None
    assert task.frontmatter.last_test_status == "failed"
    assert len(artifacts) == 1
    artifact_html = artifacts[0].read_text(encoding="utf-8")
    assert "Tests failed" in artifact_html
    assert "AGT-0001" in artifact_html
    assert "Exit code: 3" in artifact_html
    assert any(call[:2] == ["notify", "--title"] and "Tests failed" in call for call in calls)
    assert any(
        call[:4] == ["new-pane", "--type", "browser", "--direction"]
        and "file://" in " ".join(call)
        and call[-2:] == ["--focus", "false"]
        for call in calls
    )


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
    assert checks["cmux-capabilities"] == "OK"
    assert checks["cmux-identify"] == "OK"
    assert "conductor-cli" in checks
    assert checks["agent-control-room-skill"] == "WARN"
    assert result["skill"]["status"] == "missing"
    assert "packageVersion" in result["cli"]
    assert "modulePath" in result["cli"]
    assert "new-workspace" in result["cmux"]["capabilities"]["commands"]
    assert result["cmux"]["identify"]["workspace_ref"] == "workspace:1"
    assert result["cmux"]["socketPath"] == "/tmp/fake-cmux.sock"
    assert result["paths"]["stateRoot"] == str(config.state_root)
    json.dumps(result)


def test_install_skill_command_syncs_packaged_skill_and_doctor_reports_ok(config, fake_cmux):
    init_command(config, open_obsidian=False)
    stale_skill = Path.home() / ".codex" / "skills" / "agent-control-room" / "SKILL.md"
    stale_skill.parent.mkdir(parents=True)
    stale_skill.write_text("old skill\n", encoding="utf-8")

    before = doctor_command(config, fix=False)
    before_checks = {check["name"]: check["status"] for check in before["checks"]}
    assert before_checks["agent-control-room-skill"] == "WARN"
    assert before["skill"]["status"] == "stale"

    result = install_skill_command()
    after = doctor_command(config, fix=False)
    after_checks = {check["name"]: check["status"] for check in after["checks"]}

    assert result["status"] == "installed"
    assert result["name"] == "agent-control-room"
    assert Path(result["destination"]).joinpath("SKILL.md").exists()
    assert after_checks["agent-control-room-skill"] == "OK"
    assert after["skill"]["status"] == "installed"
    assert "AGENT_QUESTION: <one specific question?>" in stale_skill.read_text(encoding="utf-8")


def test_doctor_reports_stale_cmux_session_refs(config, fake_cmux):
    init_command(config, open_obsidian=False)
    write_sessions(
        config,
        {
            "version": 1,
            "sessions": {
                "AGT-9999": {
                    "task_id": "AGT-9999",
                    "run_id": "AGT-9999-RUN-001",
                    "workspace_ref": "workspace:999",
                    "surface_ref": "surface:999",
                    "status": "running",
                }
            },
        },
    )

    result = doctor_command(config, fix=False)
    checks = {check["name"]: check["status"] for check in result["checks"]}

    assert checks["cmux-session-refs"] == "WARN"
    assert result["cmux"]["sessions"] == [
        {
            "task_id": "AGT-9999",
            "run_id": "AGT-9999-RUN-001",
            "status": "running",
            "workspace_ref": "workspace:999",
            "surface_ref": "surface:999",
            "surfaces": {"agent": "surface:999"},
            "workspace_exists": False,
            "surface_exists": None,
        }
    ]


def test_doctor_keeps_session_live_when_workspace_list_temporarily_misses_live_workspace(config, monkeypatch):
    init_command(config, open_obsidian=False)
    write_sessions(
        config,
        {
            "version": 1,
            "sessions": {
                "AGT-9999": {
                    "task_id": "AGT-9999",
                    "run_id": "AGT-9999-RUN-001",
                    "workspace_ref": "workspace:9",
                    "surface_ref": "surface:10",
                    "status": "running",
                }
            },
        },
    )
    monkeypatch.setattr("vault_conductor.cmux.CmuxAdapter.list_workspaces", lambda self: [])
    monkeypatch.setattr("vault_conductor.cmux.CmuxAdapter.workspace_exists", lambda self, workspace_ref: workspace_ref == "workspace:9")

    result = doctor_command(config, fix=False)
    checks = {check["name"]: check["status"] for check in result["checks"]}

    assert checks["cmux-session-refs"] == "OK"
    assert result["cmux"]["sessions"][0]["workspace_exists"] is True
    assert result["cmux"]["sessions"][0]["surface_exists"] is None


def test_repair_sessions_command_reports_repair_details(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Repair command", status="ready")
    start_task(config, created["id"])
    update = read_sessions(config)
    update["sessions"][created["id"]].update(
        {
            "status": "done",
            "workspace_ref": "workspace:999",
            "surface_ref": "surface:999",
        }
    )
    write_sessions(config, update)
    from vault_conductor.tasks import update_task_frontmatter

    update_task_frontmatter(
        config,
        created["id"],
        {"status": "done", "workspace_ref": "workspace:999", "surface_ref": "surface:999"},
    )

    result = repair_sessions_command(config)

    assert result["count"] == 1
    assert result["repairs"][0]["task_id"] == created["id"]
    assert result["repairs"][0]["reason"] == "workspace-missing"
    assert result["repairs"][0]["action"] == "closed"
    assert result["repairs"][0]["session_removed"] is True
    assert read_sessions(config)["sessions"] == {}


def test_doctor_fix_repairs_safe_stale_sessions_and_reports_actions(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Doctor repair", status="ready")
    start_task(config, created["id"])
    sessions = read_sessions(config)
    sessions["sessions"][created["id"]].update(
        {
            "status": "done",
            "workspace_ref": "workspace:999",
            "surface_ref": "surface:999",
        }
    )
    write_sessions(config, sessions)
    from vault_conductor.tasks import update_task_frontmatter

    update_task_frontmatter(
        config,
        created["id"],
        {"status": "done", "workspace_ref": "workspace:999", "surface_ref": "surface:999"},
    )

    result = doctor_command(config, fix=True)
    checks = {check["name"]: check for check in result["checks"]}

    assert checks["cmux-session-repair"]["status"] == "OK"
    assert "Repaired 1 cmux session" in checks["cmux-session-repair"]["message"]
    assert result["cmux"]["repairs"][0]["action"] == "closed"
    assert read_sessions(config)["sessions"] == {}


def move_card_only_on_board(config, task_id: str, column: str):
    from vault_conductor.kanban import move_card

    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    move_card(board, task_id, column)
    config.board_path.write_text(render_board(board), encoding="utf-8")
