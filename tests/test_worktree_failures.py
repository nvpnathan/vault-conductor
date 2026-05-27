import subprocess

from vault_conductor.commands import init_command, new_task_command
from vault_conductor.sessions import read_sessions
from vault_conductor.kanban import find_card, parse_board
from vault_conductor.tasks import read_task_note
from vault_conductor.watch import watch_once

from test_commands import move_card_only_on_board, write_registry


def test_watch_moves_startup_failure_to_needs_human_when_repo_has_no_commits(config, tmp_path, fake_cmux):
    repo = tmp_path / "repos" / "unborn"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, text=True, capture_output=True)
    (repo / "README.md").write_text("# unborn\n", encoding="utf-8")

    init_command(config, open_obsidian=False)
    write_registry(config, repo)
    registry = config.system_dir / "repo-registry.yml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("demo", "unborn"), encoding="utf-8")
    created = new_task_command(config, repo="unborn", title="Start unborn repo", status="ready")
    move_card_only_on_board(config, created["id"], "Running")

    watch_once(config)

    task = read_task_note(config, created["id"]).frontmatter
    board = parse_board(config.board_path.read_text(encoding="utf-8"))
    task_text = (config.tasks_dir / "AGT-0001 Start unborn repo.md").read_text(encoding="utf-8")
    assert task.status == "needs-human"
    assert find_card(board, created["id"]).column_title == "Needs Human"
    assert "has no commits" in task.last_error
    assert "Failed to start task" in task_text
    assert read_sessions(config)["sessions"] == {}
