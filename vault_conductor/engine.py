from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import cmux
from .activity import create_activity_timeline
from .agents import build_prompt, provider_command, template_variables
from .config import Config
from .constants import BOARD_COLUMNS, TASK_STATUSES
from .git_ops import ensure_worktree
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
from .run_notes import append_run_followup, create_run_note, update_run_frontmatter
from .sessions import read_sessions, remove_session, upsert_session
from .tasks import (
    append_task_log,
    clear_task_human_question,
    human_question_from_body,
    now_iso,
    read_all_task_notes,
    read_task_note,
    set_task_human_question,
    status_from_column,
    status_to_column,
    update_task_frontmatter,
    validate_human_question,
)


@dataclass(frozen=True)
class StatusTransition:
    task_id: str
    before_status: str
    after_status: str
    actor: str
    source: str
    changed: bool


@dataclass(frozen=True)
class RunStartResult:
    task_id: str
    run_id: str
    log_file: str
    prompt_file: str
    workspace_ref: str
    status: str

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        data.pop("task_id", None)
        return data


@dataclass(frozen=True)
class StopTaskResult:
    task_id: str
    run_id: str | None
    workspace_ref: str | None
    status: str


@dataclass(frozen=True)
class SendTaskResult:
    task_id: str
    saved: bool
    sent: bool
    message: str
    human_question: str | None = None
    handoff_artifact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "saved": self.saved,
            "sent": self.sent,
            "message": self.message,
            "humanQuestion": self.human_question,
            "handoffArtifact": self.handoff_artifact,
        }


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
        human_question: str | None = None,
        human_response: str | None = None,
    ) -> StatusTransition:
        if status not in TASK_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        is_human = human or actor == "human"
        if status == "done" and not is_human:
            raise ValueError("Only a human may mark a task done. Rerun with --human after review/merge.")

        before = read_task_note(self.config, task_id)
        timestamp = now_iso()
        question = None
        if status == "needs-human":
            question = validate_human_question(human_question or human_question_from_body(before.body))
            set_task_human_question(
                self.config,
                task_id,
                question,
                patch={"status": status},
                opened_at=timestamp,
            )
        elif before.frontmatter.human_question_status == "open":
            clear_task_human_question(
                self.config,
                task_id,
                answer=human_response,
                patch={"status": status},
                answered_at=timestamp,
            )
        else:
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
        self.update_live_session_for_status(task_id, status, human_question=question)
        if status == "needs-human" and question:
            artifact = self.capture_human_handoff_artifact(task_id, question)
            if artifact:
                update_task_frontmatter(self.config, task_id, {"human_handoff_artifact": str(artifact)})
                append_task_log(self.config, task_id, f"Needs-human handoff artifact captured: {artifact}")
        sync_project_notes(self.config)

        return StatusTransition(
            task_id=task_id,
            before_status=before.frontmatter.status,
            after_status=after.frontmatter.status,
            actor=actor,
            source=source,
            changed=before.frontmatter.status != after.frontmatter.status,
        )

    def start_task(self, task_id: str) -> RunStartResult:
        sessions = read_sessions(self.config)
        if task_id in sessions.get("sessions", {}):
            raise ValueError(f"Task {task_id} already has a live session")
        task = read_task_note(self.config, task_id)
        ensure_worktree(self.config, task)
        run = create_run_note(self.config, task)
        activity_path = create_activity_timeline(self.config, task, run)
        prompt = build_prompt(self.config, task, run)
        prompt_path = Path(run.frontmatter.prompt_file)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        Path(run.frontmatter.log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(run.frontmatter.log_file).write_text("", encoding="utf-8")

        variables = template_variables(self.config, task, run, prompt)
        cmux_command, _env = provider_command(self.config, task.frontmatter.agent, variables)
        focus_policy = cmux.CmuxHITLPolicy.non_disruptive()
        workspace_layout = cmux.create_task_workspace(
            task_id=task.frontmatter.id,
            title=task.frontmatter.title,
            cwd=task.frontmatter.worktree,
            command=cmux_command,
            policy=focus_policy,
        )
        workspace_layout = cmux.open_task_context(
            workspace_layout,
            task_note=task.abs_path,
            run_note=run.abs_path,
            policy=focus_policy,
        )
        if not workspace_layout.workspace_ref:
            raise RuntimeError("cmux did not return a workspace reference")
        workspace_ref = workspace_layout.workspace_ref
        surface_ref = workspace_layout.agent_surface_ref
        cmux.surface_status(workspace_layout, status="running")

        update_task_frontmatter(
            self.config,
            task_id,
            {
                "current_run": run.frontmatter.id,
                "run_count": task.frontmatter.run_count + 1,
                "workspace_ref": workspace_ref,
                "surface_ref": surface_ref,
                "cmux_command": cmux_command,
            },
        )
        update_run_frontmatter(
            self.config,
            run.frontmatter.id,
            {"workspace_ref": workspace_ref, "surface_ref": surface_ref, "cmux_command": cmux_command},
        )
        session_record = {
            "task_id": task_id,
            "run_id": run.frontmatter.id,
            "workspace_ref": workspace_ref,
            "surface_ref": surface_ref,
            "agent": task.frontmatter.agent,
            "worktree": task.frontmatter.worktree,
            "log_file": run.frontmatter.log_file,
            "activity_file": str(activity_path),
            "status": "running",
            "cmux_command": cmux_command,
            "transcript_hash": "",
        }
        session_record.update(workspace_layout.to_session_patch())
        upsert_session(self.config, task_id, session_record)
        self.set_task_status(task_id, "running", actor="conductor", source="start")
        append_task_log(self.config, task_id, f"Agent started in `{workspace_ref}` with `{cmux_command}`.")
        instruction = (
            f"Please read the prompt file at {prompt_path} and follow it. "
            "Update the task status to review-diff, needs-human, or failed; if you cannot edit the note, print AGENT_STATUS."
        )
        if "codex" in cmux_command.lower() and not cmux.wait_for_screen_text(workspace_ref, surface_ref, "OpenAI Codex"):
            append_task_log(self.config, task_id, "Timed out waiting for Codex; sending prompt instruction anyway.")
        cmux.send_to_agent(workspace_layout, instruction)
        return RunStartResult(
            task_id=task_id,
            run_id=run.frontmatter.id,
            log_file=run.frontmatter.log_file,
            prompt_file=run.frontmatter.prompt_file,
            workspace_ref=workspace_ref,
            status="running",
        )

    def stop_task(self, task_id: str, *, park: bool = False, kill: bool = False) -> StopTaskResult:
        session = read_sessions(self.config).get("sessions", {}).get(task_id)
        if not session:
            raise ValueError(f"No live session found for {task_id}")
        workspace_ref = session.get("workspace_ref")
        if workspace_ref:
            cmux.close_workspace(workspace_ref)
        status = "parked" if park else "failed"
        run_id = session.get("run_id")
        if run_id:
            update_run_frontmatter(self.config, run_id, {"status": status, "ended": now_iso(), "exit_code": -15})
        self.set_task_status(task_id, status, actor="conductor", source="stop")
        update_task_frontmatter(self.config, task_id, {"workspace_ref": None, "surface_ref": None})
        remove_session(self.config, task_id)
        return StopTaskResult(
            task_id=task_id,
            run_id=run_id,
            workspace_ref=workspace_ref,
            status=status,
        )

    def send_to_task(
        self,
        task_id: str,
        message: str,
        *,
        status: str | None = None,
        human_question: str | None = None,
    ) -> SendTaskResult:
        task = read_task_note(self.config, task_id)
        if task.frontmatter.current_run:
            append_run_followup(self.config, task.frontmatter.current_run, message)
            followup_file = self.config.prompts_root / f"{task.frontmatter.current_run}.followups.md"
            followup_file.parent.mkdir(parents=True, exist_ok=True)
            with followup_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{now_iso()} {message}\n")
        append_task_log(self.config, task_id, f"Human instruction: {message}")
        session = read_sessions(self.config).get("sessions", {}).get(task_id)
        sent = False
        if session and session.get("workspace_ref"):
            send_result = cmux.send(session["workspace_ref"], message, surface_ref=session.get("surface_ref"))
            enter_result = cmux.send_enter(session["workspace_ref"], surface_ref=session.get("surface_ref"))
            sent = bool(send_result.ok and enter_result.ok)
            if not sent:
                append_task_log(
                    self.config,
                    task_id,
                    f"Human instruction could not be sent to cmux workspace {session.get('workspace_ref')}; saved only.",
                )
        if status:
            self.set_task_status(
                task_id,
                status,
                human_question=human_question,
                human_response=message if status != "needs-human" else None,
            )
        updated = read_task_note(self.config, task_id)
        return SendTaskResult(
            task_id=task_id,
            saved=True,
            sent=sent,
            message=message,
            human_question=updated.frontmatter.human_question if updated.frontmatter.human_question_status == "open" else None,
            handoff_artifact=updated.frontmatter.human_handoff_artifact,
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

    def update_live_session_for_status(self, task_id: str, status: str, *, human_question: str | None = None) -> None:
        session = read_sessions(self.config).get("sessions", {}).get(task_id)
        if not session or not session.get("workspace_ref"):
            return
        session["status"] = status
        if status == "needs-human":
            session["human_question"] = human_question
        else:
            session.pop("human_question", None)
        upsert_session(self.config, task_id, session)
        cmux.set_status(session["workspace_ref"], status)
        if status == "needs-human" and human_question:
            cmux.set_activity(session["workspace_ref"], "Needs human", icon="circle-alert", color="#dc2626")
            cmux.set_progress(session["workspace_ref"], 0.50, label="Needs human")
            cmux.log(f"Needs human: {human_question}", workspace_ref=session["workspace_ref"], source="conductor-hitl")
        self.notify_status_change(task_id, status, session["workspace_ref"])
        if session.get("run_id") and status == "needs-human":
            self.safe_update_run_frontmatter(session["run_id"], {"status": status})
        if session.get("run_id") and status in {"review-diff", "failed", "parked", "pr-opened", "done"}:
            self.safe_update_run_frontmatter(session["run_id"], {"status": status, "ended": now_iso()})

    def notify_status_change(self, task_id: str, status: str, workspace_ref: str) -> None:
        if status == "needs-human":
            task = read_task_note(self.config, task_id)
            body = task.frontmatter.human_question or "The agent is waiting for a decision."
            cmux.notify(f"{task_id} needs human input", body, workspace_ref)
        elif status == "review-diff":
            cmux.notify(f"{task_id} ready for review", "Diff and test information are ready to inspect.", workspace_ref)
        elif status == "failed":
            cmux.notify(f"{task_id} failed", "The agent run failed and needs attention.", workspace_ref)
        elif status == "pr-opened":
            task = read_task_note(self.config, task_id)
            body = task.frontmatter.pr_url or "Pull request opened."
            cmux.notify(f"{task_id} PR opened", body, workspace_ref)

    def safe_update_run_frontmatter(self, run_id: str, patch: dict[str, Any]) -> None:
        try:
            update_run_frontmatter(self.config, run_id, patch)
        except FileNotFoundError:
            pass

    def capture_human_handoff_artifact(self, task_id: str, question: str) -> Path | None:
        task = read_task_note(self.config, task_id)
        sessions = read_sessions(self.config).get("sessions", {})
        session = sessions.get(task_id)
        layout = cmux.CmuxWorkspaceLayout.from_session(session)
        layout_for_open = None
        if layout.workspace_ref:
            try:
                if cmux.workspace_exists(layout.workspace_ref):
                    layout_for_open = layout
            except Exception:
                layout_for_open = None
        artifact_path = cmux.durable_asset_root(
            task.frontmatter.branch or task_id,
            task_id,
            fallback_root=task.frontmatter.repo_path or self.config.state_root.parent,
        ) / f"{task_id}-needs-human.html"
        evidence = {
            "task": task_id,
            "title": task.frontmatter.title,
            "status": task.frontmatter.status,
            "question": question,
            "current_activity": task.frontmatter.current_activity,
            "current_activity_detail": task.frontmatter.current_activity_detail,
            "run": task.frontmatter.current_run,
            "workspace_ref": task.frontmatter.workspace_ref,
            "surface_ref": task.frontmatter.surface_ref,
            "last_diff_stat": task.frontmatter.last_diff_stat,
            "last_test_status": task.frontmatter.last_test_status,
            "last_error": task.frontmatter.last_error,
            "task_note": str(task.abs_path),
            "next_actions": [
                f"conductor send {task_id} \"<answer>\" --status running",
                f"conductor mark {task_id} needs-revision",
                f"conductor log {task_id} --tail 100",
            ],
        }
        return cmux.capture_review_artifact(
            artifact_path,
            title=f"{task_id} needs human input",
            evidence=evidence,
            layout=layout_for_open,
            policy=cmux.CmuxHITLPolicy.non_disruptive(),
        )
