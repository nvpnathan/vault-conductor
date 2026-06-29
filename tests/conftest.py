import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from vault_conductor.config import load_config


def git(cmd, cwd):
    return subprocess.run(["git", *cmd], cwd=cwd, check=True, text=True, capture_output=True)


@pytest.fixture
def fake_git_repo(tmp_path):
    repos = tmp_path / "repos"
    repo = repos / "demo"
    repo.mkdir(parents=True)
    git(["init", "-b", "main"], repo)
    git(["config", "user.email", "test.invalid"], repo)
    git(["config", "user.name", "Test User"], repo)
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    git(["add", "README.md"], repo)
    git(["commit", "-m", "initial"], repo)
    return repo


@pytest.fixture
def fake_cmux(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_file = tmp_path / "cmux-calls.jsonl"
    workspaces_file = tmp_path / "cmux-workspaces.json"
    workspaces_file.write_text(json.dumps({"workspaces": [], "read_screen_count": 0}), encoding="utf-8")
    script = bin_dir / "cmux"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
calls = Path(os.environ["FAKE_CMUX_CALLS"])
state_file = Path(os.environ["FAKE_CMUX_WORKSPACES"])
calls.open("a", encoding="utf-8").write(json.dumps(args) + "\\n")

json_mode = False
socket_path = None
id_format = None
while args:
    if args[0] == "--json":
        json_mode = True
        args = args[1:]
    elif args[0] == "--socket" and len(args) > 1:
        socket_path = args[1]
        args = args[2:]
    elif args[0] == "--password" and len(args) > 1:
        args = args[2:]
    elif args[0] == "--id-format" and len(args) > 1:
        id_format = args[1]
        args = args[2:]
    else:
        break

cmd = args[0] if args else ""

def read_state():
    return json.loads(state_file.read_text(encoding="utf-8"))

def write_state(data):
    state_file.write_text(json.dumps(data), encoding="utf-8")

def emit(data, text="OK"):
    if json_mode:
        print(json.dumps(data))
    else:
        print(text)

def emit_text(text):
    print(text)

def next_surface_ref(workspace):
    count = sum(len(pane.get("surfaces", [])) for pane in workspace.get("panes", []))
    return f"surface:{count + 1}"

def add_surface(workspace, pane, surface_type, url=None):
    surface_ref = next_surface_ref(workspace)
    surface = {
        "ref": surface_ref,
        "type": surface_type,
        "selected": True,
    }
    if url:
        surface["url"] = url
    pane.setdefault("surface_refs", []).append(surface_ref)
    pane.setdefault("surfaces", []).append(surface)
    return surface_ref

if cmd == "ping":
    emit({"ok": True}, "OK")
elif cmd == "identify":
    emit(
        {
            "workspace_ref": "workspace:1",
            "workspace_id": "workspace-id-1" if id_format == "both" else None,
            "surface_ref": "surface:1",
            "surface_id": "surface-id-1" if id_format == "both" else None,
            "socket_path": socket_path or os.environ.get("CMUX_SOCKET_PATH") or "/tmp/fake-cmux.sock",
        },
        "OK",
    )
elif cmd == "capabilities":
    emit({"commands": ["identify", "capabilities", "new-workspace", "new-pane", "new-surface", "browser", "markdown", "send", "notify", "set-progress"]}, "OK")
elif cmd == "new-workspace":
    data = read_state()
    ref = f"workspace:{len(data['workspaces']) + 1}"
    pane_ref = f"pane:{len(data['workspaces']) + 1}"
    surface_ref = f"surface:{len(data['workspaces']) + 1}"
    workspace = {
        "ref": ref,
        "id": ref,
        "name": "workspace",
        "panes": [
            {
                "ref": pane_ref,
                "surface_refs": [surface_ref],
                "surfaces": [
                    {
                        "ref": surface_ref,
                        "type": "terminal",
                        "selected": True,
                    }
                ],
            }
        ],
    }
    data["workspaces"].append(workspace)
    write_state(data)
    emit_text(f"OK {ref}")
elif cmd == "list-workspaces":
    emit(read_state())
elif cmd == "list-panes":
    data = read_state()
    workspace_ref = args[args.index("--workspace") + 1] if "--workspace" in args else "workspace:1"
    workspace = next((item for item in data["workspaces"] if item["ref"] == workspace_ref), None)
    emit({"workspace_ref": workspace_ref, "panes": (workspace or {}).get("panes", [])})
elif cmd == "list-pane-surfaces":
    data = read_state()
    workspace_ref = args[args.index("--workspace") + 1] if "--workspace" in args else "workspace:1"
    pane_ref = args[args.index("--pane") + 1] if "--pane" in args else None
    workspace = next((item for item in data["workspaces"] if item["ref"] == workspace_ref), None)
    pane = next((item for item in (workspace or {}).get("panes", []) if item["ref"] == pane_ref), None)
    emit({"workspace_ref": workspace_ref, "pane_ref": pane_ref, "surfaces": (pane or {}).get("surfaces", [])})
elif cmd == "new-pane":
    data = read_state()
    workspace_ref = args[args.index("--workspace") + 1] if "--workspace" in args else "workspace:1"
    surface_type = args[args.index("--type") + 1] if "--type" in args else "terminal"
    url = args[args.index("--url") + 1] if "--url" in args else None
    workspace = next((item for item in data["workspaces"] if item["ref"] == workspace_ref), None)
    if workspace is None:
        workspace = {"ref": workspace_ref, "id": workspace_ref, "name": "workspace", "panes": []}
        data["workspaces"].append(workspace)
    pane_ref = f"pane:{len(workspace.get('panes', [])) + 1}"
    pane = {
        "ref": pane_ref,
        "surface_refs": [],
        "surfaces": [],
    }
    surface_ref = add_surface(workspace, pane, surface_type, url=url)
    workspace.setdefault("panes", []).append(pane)
    write_state(data)
    emit({"surface_ref": surface_ref, "pane_ref": pane_ref, "url": url}, f"OK surface={surface_ref} pane={pane_ref}")
elif cmd == "new-surface":
    data = read_state()
    workspace_ref = args[args.index("--workspace") + 1] if "--workspace" in args else "workspace:1"
    pane_ref = args[args.index("--pane") + 1] if "--pane" in args else None
    surface_type = args[args.index("--type") + 1] if "--type" in args else "terminal"
    url = args[args.index("--url") + 1] if "--url" in args else None
    workspace = next((item for item in data["workspaces"] if item["ref"] == workspace_ref), None)
    pane = next((item for item in (workspace or {}).get("panes", []) if item["ref"] == pane_ref), None)
    if workspace is None or pane is None:
        emit({"ok": False}, "ERROR pane not found")
        sys.exit(1)
    surface_ref = add_surface(workspace, pane, surface_type, url=url)
    write_state(data)
    emit({"surface_ref": surface_ref, "pane_ref": pane_ref, "url": url}, f"OK surface={surface_ref} pane={pane_ref}")
elif cmd == "read-screen":
    workspace_ref = args[args.index("--workspace") + 1] if "--workspace" in args else None
    if workspace_ref:
        data = read_state()
        if not any(item["ref"] == workspace_ref for item in data["workspaces"]):
            emit({"ok": False}, "ERROR workspace not found")
            sys.exit(1)
    sequence = os.environ.get("FAKE_CMUX_SCREEN_SEQUENCE")
    if sequence is not None:
        data = read_state()
        parts = sequence.split("\\f")
        index = min(data.get("read_screen_count", 0), len(parts) - 1)
        text = parts[index]
        data["read_screen_count"] = data.get("read_screen_count", 0) + 1
        write_state(data)
    else:
        text = os.environ.get("FAKE_CMUX_SCREEN", "OpenAI Codex\\nFind and fix a bug in @filename")
    emit({"text": text}, text)
elif cmd == "events":
    sys.exit(0)
elif cmd == "browser":
    surface_ref = args[1] if len(args) > 1 else "surface:1"
    action = args[2] if len(args) > 2 else ""
    if action == "snapshot":
        emit(
            {
                "surface_ref": surface_ref,
                "title": "Fake PR",
                "url": "https://github.test/demo/pull/1",
                "text": "Fake pull request snapshot",
            },
            "Fake pull request snapshot",
        )
    elif action == "screenshot":
        out_path = args[args.index("--out") + 1] if "--out" in args else ""
        if out_path:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text("fake screenshot", encoding="utf-8")
        emit({"surface_ref": surface_ref, "out": out_path}, "OK")
    elif action == "wait":
        emit({"surface_ref": surface_ref, "ok": True}, "OK")
    elif action == "get":
        prop = args[3] if len(args) > 3 else "url"
        value = "Fake PR" if prop == "title" else "https://github.test/demo/pull/1"
        emit({"surface_ref": surface_ref, prop: value, "value": value}, value)
    else:
        emit({"surface_ref": surface_ref, "ok": True}, "OK")
elif cmd == "markdown":
    data = read_state()
    workspace_ref = args[args.index("--workspace") + 1] if "--workspace" in args else "workspace:1"
    workspace = next((item for item in data["workspaces"] if item["ref"] == workspace_ref), None)
    if workspace is None:
        emit({"ok": False}, "ERROR workspace not found")
        sys.exit(1)
    surface_number = sum(len(pane.get("surfaces", [])) for pane in workspace.get("panes", [])) + 1
    pane_number = len(workspace.get("panes", [])) + 1
    surface_ref = f"surface:{surface_number}"
    pane_ref = f"pane:{pane_number}"
    workspace.setdefault("panes", []).append(
        {
            "ref": pane_ref,
            "surface_refs": [surface_ref],
            "surfaces": [
                {
                    "ref": surface_ref,
                    "type": "markdown",
                    "selected": True,
                }
            ],
        }
    )
    write_state(data)
    path = args[2] if len(args) > 2 else ""
    emit({"surface_ref": surface_ref, "pane_ref": pane_ref}, f"OK surface={surface_ref} pane={pane_ref} path={path}")
elif cmd == "send":
    if os.environ.get("FAKE_CMUX_FAIL_SEND") == "1":
        emit({"ok": False}, "ERROR send failed")
        sys.exit(1)
    emit({"ok": True}, "OK")
elif cmd == "send-key":
    if os.environ.get("FAKE_CMUX_FAIL_SEND_KEY") == "1":
        emit({"ok": False}, "ERROR send-key failed")
        sys.exit(1)
    emit({"ok": True}, "OK")
elif cmd == "close-workspace":
    if os.environ.get("FAKE_CMUX_FAIL_CLOSE") == "1":
        emit({"ok": False}, "ERROR close failed")
        sys.exit(1)
    if "--workspace" not in args:
        emit({"ok": False}, "ERROR missing --workspace")
        sys.exit(1)
    data = read_state()
    workspace_ref = args[args.index("--workspace") + 1]
    data["workspaces"] = [item for item in data["workspaces"] if item["ref"] != workspace_ref]
    write_state(data)
    emit({"ok": True}, "OK")
elif cmd in {"set-status", "set-progress", "clear-progress", "notify"}:
    emit({"ok": True}, "OK")
else:
    emit({"ok": True}, "OK")
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CMUX_CALLS", str(calls_file))
    monkeypatch.setenv("FAKE_CMUX_WORKSPACES", str(workspaces_file))
    monkeypatch.delenv("CMUX_SOCKET_PATH", raising=False)
    return calls_file


@pytest.fixture
def config(tmp_path, monkeypatch):
    vault = tmp_path / "Agent Control Room"
    repos = tmp_path / "repos"
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("VAULT_CONDUCTOR_ASSET_ROOT", str(tmp_path / "cmux-assets"))
    return load_config(vault=vault, repos=repos, runtime_root=runtime)


def cmux_calls(calls_file: Path):
    if not calls_file.exists():
        return []
    return [json.loads(line) for line in calls_file.read_text(encoding="utf-8").splitlines() if line]
