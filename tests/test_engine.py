import pytest

from vault_conductor.commands import init_command, new_task_command, start_task
from vault_conductor.engine import ConductorEngine
from vault_conductor.kanban import find_card, parse_board
from vault_conductor.run_notes import read_run_note
from vault_conductor.sessions import read_sessions
from vault_conductor.tasks import read_task_note

from conftest import cmux_calls
from test_commands import write_registry


def test_engine_owns_task_status_transition_outputs(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Engine transition", status="ready")
    start_task(config, created["id"])

    engine = ConductorEngine(config)
    transition = engine.set_task_status(created["id"], "review-diff", actor="conductor", source="unit-test")

    task = read_task_note(config, created["id"])
    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    session = read_sessions(config)["sessions"][created["id"]]
    run = read_run_note(config, "AGT-0001-RUN-001")
    operational_log = (config.logs_root / "conductor-watch.log").read_text(encoding="utf-8")
    calls = cmux_calls(fake_cmux)

    assert transition.task_id == created["id"]
    assert transition.before_status == "running"
    assert transition.after_status == "review-diff"
    assert task.frontmatter.status == "review-diff"
    assert find_card(board, created["id"]).column_title == "Review Diff"
    assert session["status"] == "review-diff"
    assert run.frontmatter.status == "review-diff"
    assert run.frontmatter.ended is not None
    assert "Status changed: running -> review-diff." in task.body
    assert "source=unit-test" in operational_log
    assert any(call[:3] == ["set-status", "agent", "review-diff"] for call in calls)


def test_engine_preserves_human_only_done(config, fake_git_repo):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Human done", status="ready")

    engine = ConductorEngine(config)

    with pytest.raises(ValueError, match="Only a human"):
        engine.set_task_status(created["id"], "done", actor="conductor", source="unit-test")


def test_engine_starts_task_and_records_run_session_workspace(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Engine start", status="ready")

    engine = ConductorEngine(config)
    result = engine.start_task(created["id"])

    task = read_task_note(config, created["id"])
    sessions = read_sessions(config)
    calls = cmux_calls(fake_cmux)

    assert result.task_id == created["id"]
    assert result.run_id == "AGT-0001-RUN-001"
    assert result.workspace_ref == "workspace:1"
    assert result.status == "running"
    assert task.frontmatter.status == "running"
    assert task.frontmatter.current_run == result.run_id
    assert task.frontmatter.run_count == 1
    assert task.frontmatter.workspace_ref == "workspace:1"
    assert task.frontmatter.surface_ref == "surface:1"
    assert task.frontmatter.cmux_command == "cmux codex-teams"
    assert sessions["sessions"][created["id"]]["run_id"] == result.run_id
    assert sessions["sessions"][created["id"]]["activity_file"] == str(config.runs_dir / "AGT-0001-RUN-001-activity.md")
    assert sessions["sessions"][created["id"]]["cmux_layout"]["surfaces"] == {
        "agent": "surface:1",
        "run_note": "surface:2",
        "task_note": "surface:3",
    }
    assert (config.worktrees_root / "demo" / created["id"]).is_dir()
    assert (config.prompts_root / "AGT-0001-RUN-001.prompt.md").exists()
    assert any(call[:1] == ["new-workspace"] for call in calls)

    with pytest.raises(ValueError, match="already has a live session"):
        engine.start_task(created["id"])


def test_engine_stops_task_and_clears_live_session(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Engine stop", status="ready")

    engine = ConductorEngine(config)
    start_result = engine.start_task(created["id"])
    stop_result = engine.stop_task(created["id"], park=True)

    task = read_task_note(config, created["id"])
    run = read_run_note(config, start_result.run_id)
    calls = cmux_calls(fake_cmux)

    assert stop_result.task_id == created["id"]
    assert stop_result.run_id == start_result.run_id
    assert stop_result.workspace_ref == start_result.workspace_ref
    assert stop_result.status == "parked"
    assert task.frontmatter.status == "parked"
    assert task.frontmatter.workspace_ref is None
    assert task.frontmatter.surface_ref is None
    assert run.frontmatter.status == "parked"
    assert run.frontmatter.ended is not None
    assert run.frontmatter.exit_code == -15
    assert read_sessions(config)["sessions"] == {}
    assert any(call[:2] == ["close-workspace", start_result.workspace_ref] for call in calls)

    with pytest.raises(ValueError, match="No live session"):
        engine.stop_task(created["id"])


def test_engine_sends_followup_to_live_task(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Engine send", status="ready")

    engine = ConductorEngine(config)
    start_result = engine.start_task(created["id"])
    result = engine.send_to_task(
        created["id"],
        "Please add the focused regression test.",
        status="needs-human",
        human_question="Which regression path should be covered?",
    )

    task = read_task_note(config, created["id"])
    calls = cmux_calls(fake_cmux)
    run_text = (config.runs_dir / "AGT-0001-RUN-001-codex.md").read_text(encoding="utf-8")
    followups_text = (config.prompts_root / "AGT-0001-RUN-001.followups.md").read_text(encoding="utf-8")

    assert result.task_id == created["id"]
    assert result.message == "Please add the focused regression test."
    assert result.saved is True
    assert result.sent is True
    assert task.frontmatter.status == "needs-human"
    assert task.frontmatter.human_question == "Which regression path should be covered?"
    assert task.frontmatter.human_question_status == "open"
    assert "Which regression path should be covered?" in task.body
    assert "Human instruction: Please add the focused regression test." in task.body
    assert "Please add the focused regression test." in run_text
    assert "Please add the focused regression test." in followups_text
    assert any(call[:3] == ["set-status", "agent_activity", "Needs human"] for call in calls)
    assert any(call[:4] == ["new-pane", "--type", "browser", "--direction"] and "file://" in " ".join(call) for call in calls)
    assert any(
        call[:5] == ["send", "--workspace", start_result.workspace_ref, "--surface", "surface:1"]
        and "Please add the focused regression test." in " ".join(call)
        for call in calls
    )
    assert any(
        call[:5] == ["send-key", "--workspace", start_result.workspace_ref, "--surface", "surface:1"] and "enter" in call
        for call in calls
    )


def test_engine_requires_question_for_needs_human(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Question required", status="ready")
    start_task(config, created["id"])

    with pytest.raises(ValueError, match="needs-human requires one human question"):
        ConductorEngine(config).set_task_status(created["id"], "needs-human", actor="conductor", source="unit-test")


def test_engine_send_resumes_from_needs_human_and_clears_question(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Resume HITL", status="ready")
    engine = ConductorEngine(config)
    engine.start_task(created["id"])
    engine.set_task_status(
        created["id"],
        "needs-human",
        actor="conductor",
        source="unit-test",
        human_question="Should I update the generated fixture?",
    )

    result = engine.send_to_task(created["id"], "Yes, update the generated fixture.", status="running")

    task = read_task_note(config, created["id"])
    session = read_sessions(config)["sessions"][created["id"]]
    assert result.sent is True
    assert task.frontmatter.status == "running"
    assert task.frontmatter.human_question_status == "answered"
    assert task.frontmatter.human_question_answer == "Yes, update the generated fixture."
    assert task.frontmatter.human_question_answered is not None
    assert "# Human question\n\nNone." in task.abs_path.read_text(encoding="utf-8")
    assert session["status"] == "running"
    assert session.get("human_question") is None


def test_engine_saves_followup_without_live_session(config, fake_git_repo, fake_cmux):
    init_command(config, open_obsidian=False)
    write_registry(config, fake_git_repo)
    created = new_task_command(config, repo="demo", title="Engine saved send", status="ready")

    result = ConductorEngine(config).send_to_task(created["id"], "Queue this for later.")

    task = read_task_note(config, created["id"])
    calls = cmux_calls(fake_cmux)

    assert result.task_id == created["id"]
    assert result.saved is True
    assert result.sent is False
    assert result.message == "Queue this for later."
    assert task.frontmatter.status == "ready"
    assert "Human instruction: Queue this for later." in task.body
    assert not any(call[:1] == ["send"] for call in calls)
