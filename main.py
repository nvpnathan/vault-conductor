#!/usr/bin/env python3
"""
Agent Control Room Orchestrator

Watches the Obsidian kanban board and bridges it to cmux workspaces:
  - Card moves to "In Progress"  → spawn cmux workspace + Claude Code agent
  - Agent workspace closes       → move card to "Review", update note
  - cmux notifications           → update note status + move card to "Needs Attention"
"""

import json
import re
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent
VAULT = REPO_ROOT / "Agent Control Room"
KANBAN = VAULT / "Control Room.md"
PROJECTS = VAULT / "Projects"

# ── cmux helpers ──────────────────────────────────────────────────────────────

def _run(*args, capture=True):
    r = subprocess.run(
        ["cmux"] + [str(a) for a in args],
        capture_output=capture, text=True,
    )
    return r.stdout.strip(), r.returncode


def cmux(*args):
    out, rc = _run(*args)
    if rc != 0:
        label = " ".join(str(a) for a in args)
        print(f"[cmux] WARNING: '{label}' exited {rc}", file=sys.stderr)
    return out


def cmux_j(*args):
    out, rc = _run("--json", *args)
    if rc == 0 and out:
        try:
            return json.loads(out)
        except Exception as e:
            print(f"[cmux] json parse error: {e}")
    return {}


# ── Note / frontmatter helpers ─────────────────────────────────────────────────

def read_note(path: Path):
    """Return (frontmatter_dict, body_text)."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            fm = yaml.safe_load(text[3:end]) or {}
            return fm, text[end + 3:]
    return {}, text


def write_note(path: Path, fm: dict, body: str):
    path.write_text(
        "---\n" + yaml.dump(fm, default_flow_style=False, allow_unicode=True) + "---" + body,
        encoding="utf-8",
    )


def patch_note(path: Path, **kw):
    fm, body = read_note(path)
    fm.update(kw)
    write_note(path, fm, body)


def append_note_log(path: Path, message: str):
    """Append a timestamped log line to the Notes section."""
    try:
        fm, body = read_note(path)
        ts = time.strftime("%Y-%m-%d %H:%M")
        body = body.rstrip() + f"\n- `{ts}` {message}\n"
        write_note(path, fm, body)
    except Exception as e:
        print(f"[notes] append_note_log failed for {path}: {e}")


# ── Kanban board helpers ───────────────────────────────────────────────────────

_H2 = re.compile(r"^## (.+)$")
_CARD = re.compile(r"^\s*- \[[ x]\] \[\[Projects/([^\]]+)\]\]")
_CARD_DONE = re.compile(r"^\s*- \[x\] \[\[Projects/([^\]]+)\]\]")


def parse_kanban(text: str) -> dict[str, list[str]]:
    """Parse the kanban board and return {column: [project_name, ...]}."""
    cols: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("%% kanban:settings"):
            break
        m = _H2.match(line)
        if m:
            cur = m.group(1)
            cols[cur] = []
            continue
        if cur:
            m = _CARD.match(line)
            if m:
                cols[cur].append(m.group(1))
    return cols


def move_kanban_card(project: str, from_col: str, to_col: str):
    """Move a [[Projects/X]] card between columns, editing the board file."""
    try:
        text = KANBAN.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        out = []
        in_from = in_to = False
        inserted = False

        for line in lines:
            stripped = line.strip()
            h2 = _H2.match(stripped)
            if h2:
                in_from = h2.group(1) == from_col
                in_to = h2.group(1) == to_col

            # Skip the card in its current column
            m = _CARD.match(stripped)
            if in_from and m and m.group(1) == project:
                continue

            out.append(line)

            # Insert card immediately after the target column heading
            if in_to and not inserted and h2 and h2.group(1) == to_col:
                marker = "x" if to_col == "Done" else " "
                out.append(f"- [{marker}] [[Projects/{project}]]\n")
                inserted = True

        KANBAN.write_text("".join(out), encoding="utf-8")
        if not inserted:
            print(f"[kanban] WARNING: target column '{to_col}' not found for {project!r}")
    except Exception as e:
        print(f"[kanban] move failed: {e}")


# ── Agent state ───────────────────────────────────────────────────────────────

_lock = threading.Lock()
_proj_to_ws: dict[str, str] = {}   # project_name → workspace_ref
_ws_to_proj: dict[str, str] = {}   # workspace_ref → project_name
_spawning: set[str] = set()        # projects currently being spawned (pre-track guard)


def _claim_spawn(project: str) -> bool:
    """Atomically claim the right to spawn this project. Returns False if already claimed."""
    with _lock:
        if project in _spawning or project in _proj_to_ws:
            return False
        _spawning.add(project)
        return True


def _track(project: str, ws_ref: str):
    with _lock:
        _spawning.discard(project)
        _proj_to_ws[project] = ws_ref
        _ws_to_proj[ws_ref] = project


def _abort_spawn(project: str):
    with _lock:
        _spawning.discard(project)


def _untrack_ws(ws_ref: str) -> str | None:
    with _lock:
        project = _ws_to_proj.pop(ws_ref, None)
        if project:
            _proj_to_ws.pop(project, None)
        return project


def _ws_for(project: str) -> str | None:
    with _lock:
        return _proj_to_ws.get(project)


def _project_for_ws(ws_ref: str) -> str | None:
    with _lock:
        return _ws_to_proj.get(ws_ref)


# ── Spawn ─────────────────────────────────────────────────────────────────────

def spawn(project: str):
    if not _claim_spawn(project):
        print(f"[spawn] '{project}' already spawning or running — skipped")
        return

    note = PROJECTS / f"{project}.md"
    if not note.exists():
        print(f"[spawn] note not found: {note}")
        _abort_spawn(project)
        return

    fm, _ = read_note(note)
    repo = str(Path(fm.get("repo") or "~").expanduser())
    goal = fm.get("goal") or project
    agent_cmd = fm.get("agent", "claude")

    print(f"[spawn] '{project}' → repo={repo!r}")

    # new-workspace returns plain text: "OK workspace:N"
    out, rc = _run(
        "new-workspace",
        "--name", project,
        "--description", goal[:120],
        "--cwd", repo,
        "--command", agent_cmd,
        "--focus", "false",
    )

    if rc != 0:
        print(f"[spawn] ERROR: 'cmux new-workspace' failed (rc={rc}): {out!r}", file=sys.stderr)
        _abort_spawn(project)
        return

    # Parse "OK workspace:N" from stdout
    ws_ref = None
    for token in out.split():
        if token.startswith("workspace:"):
            ws_ref = token
            break

    if not ws_ref:
        print(f"[spawn] ERROR: unexpected response {out!r}", file=sys.stderr)
        _abort_spawn(project)
        return

    _track(project, ws_ref)
    _ok = False
    try:
        # Open the project note as a rich markdown panel in the workspace
        cmux("markdown", "open",
             str(note),
             "--workspace", ws_ref)

        # Sidebar status pill
        cmux("set-status", "agent", "running",
             "--icon", "sparkle", "--color", "#4c71f2",
             "--workspace", ws_ref)

        # Notification
        cmux("notify",
             "--title", f"▶ {project}",
             "--body", goal,
             "--workspace", ws_ref)

        # Update note
        patch_note(note, workspace_ref=ws_ref, status="in_progress")
        append_note_log(note, f"Agent started in `{ws_ref}`")

        print(f"[spawn] '{project}' running in {ws_ref}")

        # Wait for Claude Code to finish starting up, then send the task.
        # Build the task as a single line with no embedded newlines — cmux send treats
        # newlines as Enter keypresses, which causes premature submission and splits the
        # task across multiple separate prompts. The full note is already open as a
        # markdown panel in the workspace; the agent reads it for complete context.
        time.sleep(5)
        goal_line = (fm.get("goal") or project).replace("\n", " ")
        task = f"{goal_line}  Context from project note: {note}"
        cmux("send", "--workspace", ws_ref, task)
        time.sleep(0.2)
        cmux("send-key", "--workspace", ws_ref, "enter")
        print(f"[spawn] task sent to '{project}'")
        _ok = True
    finally:
        if not _ok:
            exc = sys.exc_info()[1]
            print(f"[spawn] ERROR: exception spawning '{project}' in {ws_ref}: {exc}", file=sys.stderr)
            _untrack_ws(ws_ref)
            try:
                patch_note(note, workspace_ref=None, status="pending")
            except Exception as e2:
                print(f"[spawn] WARNING: could not reset note for '{project}': {e2}", file=sys.stderr)


# ── Workspace closed ──────────────────────────────────────────────────────────

def on_closed(ws_ref: str):
    project = _untrack_ws(ws_ref)
    if not project:
        return
    print(f"[events] '{project}' workspace closed")
    note = PROJECTS / f"{project}.md"
    from_col = "In Progress"
    if note.exists():
        fm, _ = read_note(note)
        if fm.get("status") == "needs_attention":
            from_col = "Needs Attention"
        patch_note(note, status="review", workspace_ref=None)
        append_note_log(note, "Agent finished — moved to Review")
    move_kanban_card(project, from_col, "Review")
    cmux("notify", "--title", f"✓ {project}", "--body", "Ready for review")


# ── cmux notification → Needs Attention ───────────────────────────────────────

def on_notification(evt_data: dict):
    ws_ref = evt_data.get("workspace_ref") or evt_data.get("workspace", {}).get("ref")
    if not ws_ref:
        return
    project = _project_for_ws(ws_ref)
    if not project:
        return

    title = evt_data.get("title", "")
    body = evt_data.get("body", "")
    print(f"[events] notification for '{project}': {title}")

    note = PROJECTS / f"{project}.md"
    if note.exists():
        fm, _ = read_note(note)
        if fm.get("status") == "in_progress":
            patch_note(note, status="needs_attention")
            move_kanban_card(project, "In Progress", "Needs Attention")
            append_note_log(note, f"Agent notification: {title} — {body}")
            cmux("set-status", "agent", "waiting",
                 "--icon", "exclamationmark.circle", "--color", "#ff9500",
                 "--workspace", ws_ref)


# ── Project note watcher ──────────────────────────────────────────────────────

class NoteWatcher:
    """Polls project notes for status changes written by the agent."""

    def __init__(self):
        self._mtimes: dict[str, float] = {}
        self._last_status: dict[str, str] = {}

    def poll(self):
        for note in PROJECTS.glob("*.md"):
            if note.name.startswith("."):
                continue
            try:
                mtime = note.stat().st_mtime
                if self._mtimes.get(note.stem) == mtime:
                    continue
                self._mtimes[note.stem] = mtime

                fm, _ = read_note(note)
                status = fm.get("status") or "backlog"
                prev = self._last_status.get(note.stem)
                self._last_status[note.stem] = status

                if prev == status or prev is None:
                    continue

                print(f"[notes] '{note.stem}' status: {prev} → {status}")

                if status == "review" and prev in ("in_progress", "needs_attention"):
                    move_kanban_card(note.stem, prev.replace("_", " ").title(), "Review")
                    ws = _ws_for(note.stem)
                    if ws:
                        cmux("set-status", "agent", "review",
                             "--icon", "checkmark", "--color", "#34c759",
                             "--workspace", ws)
                    cmux("notify",
                         "--title", f"✓ {note.stem}",
                         "--body", "Ready for review")

            except Exception as e:
                print(f"[notes] error reading {note.name}: {e}")


# ── Kanban watcher ────────────────────────────────────────────────────────────

class KanbanWatcher:
    def __init__(self):
        self._cols: dict[str, list[str]] = {}
        self._mtime: float = 0.0
        self._reload()

    def _reload(self):
        try:
            st = KANBAN.stat()
            if st.st_mtime != self._mtime:
                self._cols = parse_kanban(KANBAN.read_text(encoding="utf-8"))
                self._mtime = st.st_mtime
        except Exception as e:
            print(f"[kanban] reload failed: {e}")

    def poll(self):
        prev = dict(self._cols)
        self._reload()
        new = self._cols

        prev_active = set(prev.get("In Progress", []))
        curr_active = set(new.get("In Progress", []))

        for project in curr_active - prev_active:
            print(f"[kanban] '{project}' → In Progress")
            threading.Thread(target=spawn, args=(project,), daemon=True).start()

        # Card moved out of "Needs Attention" back to "In Progress" — clear the status
        prev_waiting = set(prev.get("Needs Attention", []))
        curr_waiting = set(new.get("Needs Attention", []))
        for project in prev_waiting - curr_waiting:
            ws = _ws_for(project)
            if ws and project in curr_active:
                note = PROJECTS / f"{project}.md"
                if note.exists():
                    patch_note(note, status="in_progress")
                cmux("set-status", "agent", "running",
                     "--icon", "sparkle", "--color", "#4c71f2",
                     "--workspace", ws)


# ── cmux event stream ─────────────────────────────────────────────────────────

_HEARTBEAT_TIMEOUT = 30  # seconds; reconnect if no frame received in this window


def _event_loop():
    while True:
        proc = None
        try:
            proc = subprocess.Popen(
                ["cmux", "events",
                 "--category", "workspace",
                 "--category", "notification"],
                stdout=subprocess.PIPE, text=True, bufsize=1,
            )
            while True:
                ready, _, _ = select.select([proc.stdout], [], [], _HEARTBEAT_TIMEOUT)
                if not ready:
                    print("[events] heartbeat timeout — reconnecting")
                    proc.kill()
                    break
                raw = proc.stdout.readline()
                if raw == "":
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") == "heartbeat":
                    continue
                if evt.get("type") != "event":
                    continue

                src = evt.get("source", "")
                data = evt.get("data", {})

                if "workspace.lifecycle" in src and data.get("action") == "close":
                    ws_ref = data.get("ref") or data.get("workspace_ref")
                    if ws_ref:
                        on_closed(ws_ref)

                elif "notification" in src:
                    on_notification(data)

            proc.wait()
        except Exception as e:
            print(f"[events] stream error: {e}")
        time.sleep(3)


# ── Startup reconciliation ────────────────────────────────────────────────────

def reconcile_on_startup():
    """Scan project notes for workspace_ref values and cross-check against live workspaces.

    Re-registers workspaces that are still open; calls on_closed() for any that
    disappeared while the orchestrator was down (so cards don't strand in "In Progress").
    """
    print("[startup] reconciling tracked workspaces...")

    ws_data = cmux_j("list-workspaces")
    if not ws_data:
        # cmux unavailable or returned nothing — skip to avoid false closures
        print("[startup] WARNING: could not fetch workspace list — skipping reconciliation")
        return
    live_refs = {ws["ref"] for ws in ws_data.get("workspaces", [])}

    reconciled = 0
    closed = 0

    for note in PROJECTS.glob("*.md"):
        if note.name.startswith("."):
            continue
        try:
            fm, _ = read_note(note)
            ws_ref = fm.get("workspace_ref")
            status = fm.get("status", "backlog")

            if not ws_ref or status not in ("in_progress", "needs_attention"):
                continue

            project = note.stem

            if ws_ref in live_refs:
                _track(project, ws_ref)
                print(f"[startup] re-registered '{project}' → {ws_ref} (still running)")
                reconciled += 1
            else:
                # Track temporarily so on_closed() can untrack and clean up correctly
                _track(project, ws_ref)
                print(f"[startup] '{project}' workspace {ws_ref} gone — cleaning up")
                on_closed(ws_ref)
                closed += 1

        except Exception as e:
            print(f"[startup] error processing {note.name}: {e}")

    print(f"[startup] reconciliation complete: {reconciled} re-registered, {closed} cleaned up")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[control-room] vault: {VAULT}")
    PROJECTS.mkdir(exist_ok=True)

    _, rc = _run("ping")
    if rc != 0:
        print("[control-room] WARNING: cmux socket not responding — workspace automation disabled")

    reconcile_on_startup()

    kanban = KanbanWatcher()
    notes = NoteWatcher()
    threading.Thread(target=_event_loop, daemon=True).start()

    print("[control-room] watching kanban + notes. Ctrl+C to stop.")
    try:
        while True:
            kanban.poll()
            notes.poll()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[control-room] stopped.")


if __name__ == "__main__":
    main()
