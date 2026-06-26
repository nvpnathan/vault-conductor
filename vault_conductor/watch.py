from __future__ import annotations

import json
import select
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from . import cmux
from .commands import mark_task, read_board, sample_session_transcript, start_task
from .config import Config
from .kanban import find_card
from .run_notes import append_run_repair_event, update_run_frontmatter
from .sessions import read_sessions, remove_session, upsert_session
from .tasks import append_task_log, now_iso, read_all_task_notes, update_task_frontmatter


WatchLog = Callable[[str], None]


@dataclass(frozen=True)
class CmuxSessionRepair:
    task_id: str
    run_id: str | None
    workspace_ref: str | None
    surface_ref: str | None
    reason: str
    action: str
    was_running: bool
    session_removed: bool
    task_status_before: str | None = None
    task_status_after: str | None = None

    @property
    def manual_action_required(self) -> bool:
        return self.action == "needs-human"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "workspace_ref": self.workspace_ref,
            "surface_ref": self.surface_ref,
            "reason": self.reason,
            "action": self.action,
            "was_running": self.was_running,
            "session_removed": self.session_removed,
            "manual_action_required": self.manual_action_required,
            "task_status_before": self.task_status_before,
            "task_status_after": self.task_status_after,
        }


def stderr_watch_log(message: str) -> None:
    print(f"{now_iso()} conductor-watch {message}", file=sys.stderr, flush=True)


def watch_once(config: Config, *, log: WatchLog | None = None, verbose: bool = False) -> None:
    board = read_board(config)
    sessions = read_sessions(config).get("sessions", {})
    tasks = read_all_task_notes(config)
    pending_starts = [
        task
        for task in tasks
        if (
            (located := find_card(board, task.frontmatter.id))
            and located.column_title == config.columns["running"]
            and task.frontmatter.status != "running"
            and task.frontmatter.id not in sessions
        )
    ]
    if log and verbose:
        log(f"poll tasks={len(tasks)} sessions={len(sessions)} pending_starts={len(pending_starts)}")

    for task in pending_starts:
        if log:
            log(
                "start requested "
                f"task={task.frontmatter.id} repo={task.frontmatter.repo} previous_status={task.frontmatter.status}"
            )
        try:
            result = start_task(config, task.frontmatter.id)
            if log:
                log(
                    "started "
                    f"task={task.frontmatter.id} run={result.get('run_id')} workspace={result.get('workspace_ref')}"
                )
        except Exception as error:
            message = str(error)
            if log:
                log(f"start failed task={task.frontmatter.id} error={message}")
            update_task_frontmatter(config, task.frontmatter.id, {"last_error": message})
            append_task_log(config, task.frontmatter.id, f"Failed to start task: {message}")
            mark_task(config, task.frontmatter.id, "needs-human")

    sessions = read_sessions(config).get("sessions", {})
    if log and sessions and verbose:
        log(f"sampling sessions count={len(sessions)}")
    for task_id, session in list(sessions.items()):
        before_hash = session.get("transcript_hash")
        workspace_ref = session.get("workspace_ref")
        surface_ref = session.get("surface_ref")
        if log and verbose:
            log(f"sample task={task_id} workspace={workspace_ref} surface={surface_ref}")
        try:
            detected = sample_session_transcript(config, task_id, session)
        except Exception as error:
            if log:
                log(f"sample failed task={task_id} workspace={workspace_ref} error={error}")
            continue
        current_session = read_sessions(config).get("sessions", {}).get(task_id, {})
        if detected:
            if log:
                log(f"detected agent status task={task_id} status={detected}")
        elif current_session.get("transcript_hash") != before_hash:
            if log and verbose:
                log(f"transcript changed task={task_id} workspace={workspace_ref}")
        elif log and verbose:
            log(f"transcript unchanged task={task_id} workspace={workspace_ref}")


def repair_stale_sessions(config: Config, live_workspace_refs: Iterable[str] | None = None) -> list[CmuxSessionRepair]:
    verify_missing = live_workspace_refs is None
    if live_workspace_refs is None:
        live_workspace_refs = {
            str(workspace.get("ref") or workspace.get("id"))
            for workspace in cmux.list_workspaces()
            if workspace.get("ref") or workspace.get("id")
        }
    live = set(live_workspace_refs)
    repairs: list[CmuxSessionRepair] = []
    runtime = cmux.CmuxRuntimeState.load(config)
    for task_id, session in list(runtime.sessions.items()):
        workspace_ref = session.workspace_ref
        surface_ref = session.surface_ref
        workspace_listed = bool(workspace_ref and workspace_ref in live)
        workspace_exists = workspace_listed
        if not workspace_exists and verify_missing and workspace_ref and cmux.workspace_exists(workspace_ref):
            workspace_exists = True
        reason: str | None = None
        if not workspace_exists:
            reason = "workspace-missing"
        elif workspace_listed and surface_ref and not cmux.surface_exists(workspace_ref, surface_ref):
            reason = "surface-missing"
        if not reason:
            continue
        task_status = None
        try:
            task_status = read_task_status(config, task_id)
        except Exception:
            remove_session(config, task_id)
            continue
        session_status = session.status
        was_running = task_status == "running" or session_status == "running"
        action = "needs-human" if was_running else ("closed" if task_status in {"done", "failed", "parked", "pr-opened"} else "stale")
        session_removed = reason == "workspace-missing" or action != "needs-human"
        task_status_after = task_status
        if action == "needs-human":
            mark_task(config, task_id, "needs-human")
            task_status_after = "needs-human"
        _preserve_repair_evidence(
            config,
            task_id,
            run_id=session.run_id,
            action=action,
            reason=reason,
            workspace_ref=workspace_ref,
            surface_ref=surface_ref,
            session_removed=session_removed,
            task_status_before=task_status,
            task_status_after=task_status_after,
        )
        if action == "needs-human":
            append_task_log(
                config,
                task_id,
                _repair_log_message(reason=reason, workspace_ref=workspace_ref, surface_ref=surface_ref),
            )
        if reason == "surface-missing" and not session_removed:
            _clear_session_surface(config, task_id, session.raw)
            update_task_frontmatter(config, task_id, {"surface_ref": None})
            if session.run_id:
                _safe_update_run_frontmatter(config, session.run_id, {"status": task_status_after, "surface_ref": None})
        else:
            update_task_frontmatter(config, task_id, {"workspace_ref": None, "surface_ref": None})
            if session.run_id:
                _safe_update_run_frontmatter(
                    config,
                    session.run_id,
                    {"status": task_status_after or action, "workspace_ref": None, "surface_ref": None, "ended": now_iso()},
                )
            remove_session(config, task_id)
        repairs.append(
            CmuxSessionRepair(
                task_id=task_id,
                run_id=session.run_id,
                workspace_ref=workspace_ref,
                surface_ref=surface_ref,
                reason=reason,
                action=action,
                was_running=was_running,
                session_removed=session_removed,
                task_status_before=task_status,
                task_status_after=task_status_after,
            )
        )
    return repairs


def _clear_session_surface(config: Config, task_id: str, record: dict[str, Any]) -> None:
    repaired = dict(record)
    stale_surface_ref = repaired.get("surface_ref")
    repaired["status"] = "needs-human"
    repaired["surface_ref"] = None
    layout = repaired.get("cmux_layout")
    if isinstance(layout, dict):
        layout = dict(layout)
        surfaces = layout.get("surfaces")
        if isinstance(surfaces, dict):
            layout["surfaces"] = {
                key: value
                for key, value in surfaces.items()
                if key != "agent" and value != stale_surface_ref
            }
        target = layout.get("target")
        if isinstance(target, dict) and target.get("surface_ref") == stale_surface_ref:
            target = dict(target)
            target.pop("surface_ref", None)
            target.pop("surface_id", None)
            layout["target"] = target
        repaired["cmux_layout"] = layout
    upsert_session(config, task_id, repaired)


def _preserve_repair_evidence(
    config: Config,
    task_id: str,
    *,
    run_id: str | None,
    action: str,
    reason: str,
    workspace_ref: str | None,
    surface_ref: str | None,
    session_removed: bool,
    task_status_before: str | None,
    task_status_after: str | None,
) -> None:
    message = (
        f"task={task_id} action={action} reason={reason} workspace={workspace_ref or 'none'} "
        f"surface={surface_ref or 'none'} session_removed={str(session_removed).lower()} "
        f"task_status={task_status_before or 'unknown'}->{task_status_after or 'unknown'}"
    )
    if run_id:
        try:
            append_run_repair_event(config, run_id, message)
        except Exception:
            pass


def _repair_log_message(*, reason: str, workspace_ref: str | None, surface_ref: str | None) -> str:
    if reason == "surface-missing":
        return (
            f"cmux surface {surface_ref} is missing while workspace {workspace_ref} is still open; "
            "needs human reconciliation."
        )
    return f"workspace {workspace_ref} closed while task was still running; needs human reconciliation."


def _safe_update_run_frontmatter(config: Config, run_id: str, patch: dict[str, Any]) -> None:
    try:
        update_run_frontmatter(config, run_id, patch)
    except Exception:
        pass


def reconcile_closed_workspaces(config: Config, live_workspace_refs: Iterable[str] | None = None) -> list[str]:
    return [repair.task_id for repair in repair_stale_sessions(config, live_workspace_refs=live_workspace_refs) if repair.was_running]


def read_task_status(config: Config, task_id: str) -> str:
    from .tasks import read_task_note

    return read_task_note(config, task_id).frontmatter.status


def handle_notification(config: Config, data: dict) -> str | None:
    workspace_ref = data.get("workspace_ref") or (data.get("workspace") or {}).get("ref")
    if not workspace_ref:
        return None
    sessions = read_sessions(config).get("sessions", {})
    task_id = next((tid for tid, session in sessions.items() if session.get("workspace_ref") == workspace_ref), None)
    if not task_id:
        return None
    if read_task_status(config, task_id) == "running":
        mark_task(config, task_id, "needs-human")
        append_task_log(config, task_id, f"cmux notification: {data.get('title', '')} {data.get('body', '')}".strip())
    return task_id


def watch_forever(
    config: Config,
    poll_interval: float = 1.0,
    *,
    log: WatchLog = stderr_watch_log,
    verbose: bool | None = None,
) -> None:
    verbose = bool(config.flags.get("verbose")) if verbose is None else verbose
    log(f"started vault={config.vault_path} runtime={config.state_root.parent} interval={poll_interval}s")
    while True:
        try:
            watch_once(config, log=log, verbose=verbose)
        except KeyboardInterrupt:
            log("stopped by keyboard interrupt")
            raise
        except Exception as error:
            log(f"poll failed error={error}")
            traceback.print_exc(file=sys.stderr)
        time.sleep(poll_interval)


def events_forever(config: Config, heartbeat_timeout: int = 30) -> None:
    while True:
        proc = None
        try:
            proc = subprocess.Popen(
                ["cmux", "events", "--category", "workspace", "--category", "notification"],
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            while True:
                ready, _, _ = select.select([proc.stdout], [], [], heartbeat_timeout)
                if not ready:
                    proc.kill()
                    break
                raw = proc.stdout.readline()
                if raw == "":
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "heartbeat":
                    continue
                if event.get("type") != "event":
                    continue
                source = event.get("source", "")
                data = event.get("data", {})
                if "workspace.lifecycle" in source and data.get("action") == "close":
                    ref = data.get("ref") or data.get("workspace_ref")
                    if ref:
                        reconcile_closed_workspaces(config, set())
                elif "notification" in source:
                    handle_notification(config, data)
            proc.wait()
        finally:
            if proc and proc.poll() is None:
                proc.kill()
        time.sleep(3)
