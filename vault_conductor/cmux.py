from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any


def run_cmux(*args: str, json_mode: bool = False, timeout: int | None = 30) -> tuple[str, int]:
    command = ["cmux"]
    if json_mode:
        command.append("--json")
    command.extend(str(arg) for arg in args)
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    return result.stdout.strip(), result.returncode


def cmux_json(*args: str) -> dict[str, Any]:
    out, rc = run_cmux(*args, json_mode=True)
    if rc != 0 or not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def ping() -> bool:
    _, rc = run_cmux("ping")
    return rc == 0


def new_workspace(*, name: str, description: str, cwd: str | Path, command: str, focus: bool = False) -> str:
    args = [
        "new-workspace",
        "--name",
        name,
        "--description",
        description[:120],
        "--cwd",
        str(cwd),
        "--command",
        command,
        "--focus",
        "true" if focus else "false",
    ]
    out, rc = run_cmux(*args)
    if rc != 0:
        raise RuntimeError(f"cmux new-workspace failed: {out}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {}
    ref = (
        data.get("workspace", {}).get("ref")
        or data.get("workspace", {}).get("id")
        or data.get("workspace_ref")
        or data.get("ref")
        or data.get("id")
    )
    if ref:
        return str(ref)
    for token in out.split():
        if token.startswith("workspace:"):
            return token
    raise RuntimeError(f"Unexpected cmux new-workspace response: {out!r}")


def markdown_open(
    path: str | Path,
    workspace_ref: str,
    *,
    surface_ref: str | None = None,
    direction: str | None = None,
    focus: bool = False,
) -> str | None:
    args = ["markdown", "open", str(path), "--workspace", workspace_ref]
    if surface_ref:
        args.extend(["--surface", surface_ref])
    if direction:
        args.extend(["--direction", direction])
    args.extend(["--focus", "true" if focus else "false"])
    out, rc = run_cmux(*args)
    if rc != 0:
        return None
    return surface_ref_from_output(out)


def surface_ref_from_output(out: str) -> str | None:
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {}
    ref = (
        data.get("surface", {}).get("ref")
        or data.get("surface", {}).get("id")
        or data.get("surface_ref")
        or data.get("ref")
        or data.get("id")
    )
    if ref:
        return str(ref)
    for token in out.replace(",", " ").split():
        if token.startswith("surface="):
            return token.split("=", 1)[1]
        if token.startswith("surface:"):
            return token
    return None


def set_status(workspace_ref: str, status: str, *, icon: str = "sparkle", color: str = "#4c71f2") -> None:
    run_cmux("set-status", "agent", status, "--icon", icon, "--color", color, "--workspace", workspace_ref)


def set_activity(workspace_ref: str, label: str, *, icon: str, color: str) -> None:
    run_cmux(
        "set-status",
        "agent_activity",
        label,
        "--icon",
        icon,
        "--color",
        color,
        "--priority",
        "80",
        "--workspace",
        workspace_ref,
    )


def log(message: str, *, workspace_ref: str | None = None, source: str = "conductor") -> None:
    args = ["log", "--source", source]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    args.append(message)
    run_cmux(*args)


def notify(title: str, body: str, workspace_ref: str | None = None) -> None:
    args = ["notify", "--title", title, "--body", body]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    run_cmux(*args)


def open_browser_split(url: str, workspace_ref: str) -> None:
    run_cmux("new-pane", "--type", "browser", "--direction", "right", "--workspace", workspace_ref, "--url", url, "--focus", "true")


def select_workspace(workspace_ref: str) -> None:
    run_cmux("select-workspace", "--workspace", workspace_ref)


def terminal_surface(workspace_ref: str) -> str | None:
    panes = cmux_json("list-panes", "--workspace", workspace_ref).get("panes", [])
    for pane in panes:
        pane_ref = pane.get("ref")
        if not pane_ref:
            continue
        surfaces = cmux_json("list-pane-surfaces", "--workspace", workspace_ref, "--pane", str(pane_ref)).get("surfaces", [])
        for surface in surfaces:
            if surface.get("type") == "terminal" and surface.get("ref"):
                return str(surface["ref"])
    return None


def wait_for_screen_text(
    workspace_ref: str,
    surface_ref: str | None,
    text: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.25,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text in read_screen(workspace_ref, surface_ref=surface_ref):
            return True
        time.sleep(interval)
    return False


def send(workspace_ref: str, message: str, *, surface_ref: str | None = None) -> None:
    args = ["send", "--workspace", workspace_ref]
    if surface_ref:
        args.extend(["--surface", surface_ref])
    args.append(message)
    run_cmux(*args)


def send_enter(workspace_ref: str, *, surface_ref: str | None = None) -> None:
    args = ["send-key", "--workspace", workspace_ref]
    if surface_ref:
        args.extend(["--surface", surface_ref])
    args.append("enter")
    run_cmux(*args)


def close_workspace(workspace_ref: str) -> None:
    run_cmux("close-workspace", workspace_ref)


def list_workspaces() -> list[dict[str, Any]]:
    data = cmux_json("list-workspaces")
    return list(data.get("workspaces", []))


def workspace_exists(workspace_ref: str) -> bool:
    _, rc = run_cmux("read-screen", "--workspace", workspace_ref, timeout=5)
    return rc == 0


def read_screen(workspace_ref: str, *, surface_ref: str | None = None) -> str:
    args = ["read-screen", "--workspace", workspace_ref]
    if surface_ref:
        args.extend(["--surface", surface_ref])
    data = cmux_json(*args)
    if "text" in data:
        return str(data["text"])
    out, _ = run_cmux(*args)
    return out
