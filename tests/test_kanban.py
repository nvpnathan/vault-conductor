from vault_conductor.kanban import build_card_line, find_card, move_card, parse_board, render_board
from vault_conductor.tasks import Task


def test_kanban_render_preserves_frontmatter_cards_tags_and_settings():
    content = """---
kanban-plugin: board
---

# Agent Control Room

## Backlog

- [ ] [[20 Agent Tasks/AGT-0001 First task.md|AGT-0001 First task]] #repo/demo #agent/codex #state/backlog
plain note under backlog

## Done

- [x] [[20 Agent Tasks/AGT-0002 Done task.md|AGT-0002 Done task]] #repo/demo #agent/claude #state/done

%% kanban:settings
```
{"kanban-plugin":"board"}
```
%%
"""
    board = parse_board(content)

    assert board.frontmatter.strip() == "---\nkanban-plugin: board\n---"
    assert board.settings_block.startswith("%% kanban:settings")
    assert find_card(board, "AGT-0001").card.tags == ["#repo/demo", "#agent/codex", "#state/backlog"]
    assert find_card(board, "AGT-0002").card.checked is True

    move_card(board, "AGT-0001", "Done", status="done", checked=True)
    rendered = render_board(board)

    assert "plain note under backlog" in rendered
    assert "%% kanban:settings" in rendered
    assert "- [x] [[20 Agent Tasks/AGT-0001 First task.md|AGT-0001 First task]]" in rendered
    assert "#state/done" in rendered
    assert rendered.endswith("\n")


def test_build_card_line_uses_task_note_wikilink_and_status_tags():
    task = Task(
        id="AGT-0123",
        title="Ship useful feature",
        status="ready",
        repo="demo",
        repo_path="/tmp/demo",
        project="",
        agent="codex",
        priority="P1",
        risk="high",
        base_branch="main",
        branch="agent/AGT-0123-ship-useful-feature",
        worktree="/tmp/worktrees/demo/AGT-0123",
        current_run=None,
        run_count=0,
        human_gate="review-diff-before-pr",
        pr_url=None,
        created="2026-05-27T00:00:00Z",
        updated="2026-05-27T00:00:00Z",
        completed=None,
        tags=["agent-task"],
    )

    line = build_card_line(task)

    assert line == (
        "- [ ] [[20 Agent Tasks/AGT-0123 Ship useful feature.md|AGT-0123 Ship useful feature]] "
        "#repo/demo #agent/codex #priority/P1 #risk/high #state/ready"
    )
