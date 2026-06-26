from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import cmux
from .config import Config
from .constants import BOARD_COLUMNS, TASK_STATUSES
from .kanban import (
    build_card_line,
    empty_board_content,
    find_card,
    move_card,
    parse_board,
    render_board,
    update_card_line,
)
from .operational_log import append_operational_log
from .repos import sync_project_notes
from .run_notes import update_run_frontmatter
from .sessions import read_sessions, upsert_session
from .tasks import (
    append_task_log,
    read_all_task_notes,
    read_task_note,
    status_from_column,
    status_to_column,
    update_task_frontmatter,
)


@dataclass(frozen=True)
class StatusTransition:
    task_id: str
    before_status: str
    after_status: str
    actor: str
    source: str
    changed: bool


class ConductorEngine:
    """Lifecycle engine for task state changes backed by the Agent Control Room vault."""

    def __init__(self, config: Config):
        self.config = config

    def set_task_status(
        self,
        task_id: str,
        status: str,
        *,
        actor: str = "conductor",
        source: str = "engine",
        human: bool = False,
    ) -> StatusTransition:
        if status not in TASK_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        is_human = human or actor == "human"
        if status == "done" and not is_human:
            raise ValueError("Only a human may mark a task done. Rerun with --human after review/merge.")

        before = read_task_note(self.config, task_id)
        update_task_frontmatter(self.config, task_id, {"status": status})
        after = read_task_note(self.config, task_id)

        board = self.read_board()
        existing = find_card(board, task_id)
        line = update_card_line(
            existing.card.line if existing else build_card_line(before.frontmatter),
            task=after.frontmatter,
            checked=status == "done",
        )
        move_card(
            board,
            task_id,
            status_to_column(self.config, status),
            status=status,
            checked=status == "done",
            card_line=line,
        )
        self.write_board(board)

        self.record_status_change(
            task_id,
            before.frontmatter.status,
            after.frontmatter.status,
            actor=actor,
            source=source,
        )
        self.update_live_session_for_status(task_id, status)
        sync_project_notes(self.config)

        return StatusTransition(
            task_id=task_id,
            before_status=before.frontmatter.status,
            after_status=after.frontmatter.status,
            actor=actor,
            source=source,
            changed=before.frontmatter.status != after.frontmatter.status,
        )

    def sync_board(self, *, board_wins: bool = False) -> dict[str, int]:
        tasks = read_all_task_notes(self.config)
        board = self.read_board()
        for task in tasks:
            status = task.frontmatter.status
            if board_wins:
                located = find_card(board, task.frontmatter.id)
                board_status = status_from_column(self.config, located.column_title) if located else None
                if board_status and board_status != status:
                    before_status = status
                    update_task_frontmatter(self.config, task.frontmatter.id, {"status": board_status})
                    status = board_status
                    task = read_task_note(self.config, task.frontmatter.id)
                    self.record_status_change(
                        task.frontmatter.id,
                        before_status,
                        status,
                        actor="human",
                        source="sync-board-wins",
                    )
            located = find_card(board, task.frontmatter.id)
            line = update_card_line(
                located.card.line if located else build_card_line(task.frontmatter),
                task=task.frontmatter,
                checked=status == "done",
            )
            move_card(
                board,
                task.frontmatter.id,
                status_to_column(self.config, status),
                status=status,
                checked=status == "done",
                card_line=line,
            )
        self.write_board(board)
        sync_project_notes(self.config)
        return {"synced": len(tasks)}

    def read_board(self):
        if self.config.board_path.exists():
            return parse_board(self.config.board_path.read_text(encoding="utf-8"))
        return parse_board(empty_board_content(BOARD_COLUMNS))

    def write_board(self, board: Any) -> None:
        from .markdown import write_file_atomic

        write_file_atomic(self.config.board_path, render_board(board))

    def record_status_change(
        self,
        task_id: str,
        before_status: str,
        after_status: str,
        *,
        actor: str,
        source: str,
    ) -> None:
        if before_status == after_status:
            return
        task = read_task_note(self.config, task_id)
        append_task_log(self.config, task_id, f"Status changed: {before_status} -> {after_status}.")
        append_operational_log(
            self.config,
            "conductor-status",
            (
                f"status changed task={task_id} from={before_status} to={after_status} "
                f"repo={task.frontmatter.repo} actor={actor} source={source}"
            ),
        )

    def update_live_session_for_status(self, task_id: str, status: str) -> None:
        session = read_sessions(self.config).get("sessions", {}).get(task_id)
        if not session or not session.get("workspace_ref"):
            return
        session["status"] = status
        upsert_session(self.config, task_id, session)
        cmux.set_status(session["workspace_ref"], status)
        self.notify_status_change(task_id, status, session["workspace_ref"])
        if session.get("run_id") and status in {"review-diff", "failed", "parked", "pr-opened", "done"}:
            from .tasks import now_iso

            update_run_frontmatter(self.config, session["run_id"], {"status": status, "ended": now_iso()})

    def notify_status_change(self, task_id: str, status: str, workspace_ref: str) -> None:
        if status == "needs-human":
            cmux.notify(f"{task_id} needs human input", "The agent is waiting for a decision.", workspace_ref)
        elif status == "review-diff":
            cmux.notify(f"{task_id} ready for review", "Diff and test information are ready to inspect.", workspace_ref)
        elif status == "failed":
            cmux.notify(f"{task_id} failed", "The agent run failed and needs attention.", workspace_ref)
        elif status == "pr-opened":
            task = read_task_note(self.config, task_id)
            body = task.frontmatter.pr_url or "Pull request opened."
            cmux.notify(f"{task_id} PR opened", body, workspace_ref)
