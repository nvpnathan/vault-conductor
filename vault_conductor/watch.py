from __future__ import annotations

import json
import select
import subprocess
import time
from collections.abc import Iterable

from . import cmux
from .commands import mark_task, read_board, sample_session_transcript, start_task
from .config import Config
from .kanban import find_card
from .sessions import read_sessions, remove_session
from .tasks import append_task_log, read_all_task_notes, update_task_frontmatter


def watch_once(config: Config) -> None:
    board = read_board(config)
    sessions = read_sessions(config).get("sessions", {})

    for task in read_all_task_notes(config):
        located = find_card(board, task.frontmatter.id)
        if (
            located
            and located.column_title == config.columns["running"]
            and task.frontmatter.status != "running"
            and task.frontmatter.id not in sessions
        ):
            start_task(config, task.frontmatter.id)

    sessions = read_sessions(config).get("sessions", {})
    for task_id, session in list(sessions.items()):
        sample_session_transcript(config, task_id, session)

    live_refs = {workspace.get("ref") or workspace.get("id") for workspace in cmux.list_workspaces()}
    reconcile_closed_workspaces(config, {str(ref) for ref in live_refs if ref})


def reconcile_closed_workspaces(config: Config, live_workspace_refs: Iterable[str] | None = None) -> list[str]:
    if live_workspace_refs is None:
        live_workspace_refs = {
            str(workspace.get("ref") or workspace.get("id"))
            for workspace in cmux.list_workspaces()
            if workspace.get("ref") or workspace.get("id")
        }
    live = set(live_workspace_refs)
    changed: list[str] = []
    sessions = read_sessions(config).get("sessions", {})
    for task_id, session in list(sessions.items()):
        workspace_ref = session.get("workspace_ref")
        if workspace_ref and workspace_ref in live:
            continue
        task = None
        try:
            task = read_task_status(config, task_id)
        except Exception:
            remove_session(config, task_id)
            continue
        if task == "running":
            mark_task(config, task_id, "needs-human")
            append_task_log(config, task_id, f"workspace {workspace_ref} closed while task was still running; needs human reconciliation.")
            changed.append(task_id)
        update_task_frontmatter(config, task_id, {"workspace_ref": None, "surface_ref": None})
        remove_session(config, task_id)
    return changed


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


def watch_forever(config: Config, poll_interval: float = 1.0) -> None:
    while True:
        watch_once(config)
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
