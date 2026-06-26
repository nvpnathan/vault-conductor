from __future__ import annotations

import json
import select
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from . import cmux
from .commands import mark_task, read_board, sample_session_transcript, start_task
from .config import Config
from .kanban import find_card
from .sessions import read_sessions, remove_session
from .tasks import append_task_log, now_iso, read_all_task_notes, update_task_frontmatter


WatchLog = Callable[[str], None]


@dataclass(frozen=True)
class CmuxSessionRepair:
    task_id: str
    workspace_ref: str | None
    reason: str
    was_running: bool


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
        if workspace_ref and workspace_ref in live:
            continue
        if verify_missing and workspace_ref and cmux.workspace_exists(workspace_ref):
            continue
        task = None
        try:
            task = read_task_status(config, task_id)
        except Exception:
            remove_session(config, task_id)
            continue
        was_running = task == "running"
        if task == "running":
            mark_task(config, task_id, "needs-human")
            append_task_log(config, task_id, f"workspace {workspace_ref} closed while task was still running; needs human reconciliation.")
        repairs.append(
            CmuxSessionRepair(
                task_id=task_id,
                workspace_ref=workspace_ref,
                reason="workspace-missing",
                was_running=was_running,
            )
        )
        update_task_frontmatter(config, task_id, {"workspace_ref": None, "surface_ref": None})
        remove_session(config, task_id)
    return repairs


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
