from __future__ import annotations

import json
import subprocess
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
    data = cmux_json(*args)
    ref = (
        data.get("workspace", {}).get("ref")
        or data.get("workspace", {}).get("id")
        or data.get("ref")
        or data.get("id")
    )
    if ref:
        return str(ref)
    out, rc = run_cmux(*args)
    if rc != 0:
        raise RuntimeError(f"cmux new-workspace failed: {out}")
    for token in out.split():
        if token.startswith("workspace:"):
            return token
    raise RuntimeError(f"Unexpected cmux new-workspace response: {out!r}")


def markdown_open(path: str | Path, workspace_ref: str) -> None:
    run_cmux("markdown", "open", str(path), "--workspace", workspace_ref)


def set_status(workspace_ref: str, status: str, *, icon: str = "sparkle", color: str = "#4c71f2") -> None:
    run_cmux("set-status", "agent", status, "--icon", icon, "--color", color, "--workspace", workspace_ref)


def notify(title: str, body: str, workspace_ref: str | None = None) -> None:
    args = ["notify", "--title", title, "--body", body]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    run_cmux(*args)


def send(workspace_ref: str, message: str) -> None:
    run_cmux("send", "--workspace", workspace_ref, message)


def send_enter(workspace_ref: str) -> None:
    run_cmux("send-key", "--workspace", workspace_ref, "enter")


def close_workspace(workspace_ref: str) -> None:
    run_cmux("close-workspace", workspace_ref)


def list_workspaces() -> list[dict[str, Any]]:
    data = cmux_json("list-workspaces")
    return list(data.get("workspaces", []))


def read_screen(workspace_ref: str) -> str:
    data = cmux_json("read-screen", "--workspace", workspace_ref)
    if "text" in data:
        return str(data["text"])
    out, _ = run_cmux("read-screen", "--workspace", workspace_ref)
    return out
