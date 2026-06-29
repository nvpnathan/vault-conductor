from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any


_DEFAULT_TIMEOUT = object()


class CmuxCommandError(RuntimeError):
    """Raised when a cmux command fails and the caller asked to enforce success."""

    def __init__(self, result: CmuxCommandResult, message: str | None = None):
        self.result = result
        detail = message or result.stderr or result.stdout or "cmux command failed"
        super().__init__(f"{detail} (exit {result.returncode}): {' '.join(result.command)}")


@dataclass(frozen=True)
class CmuxCommandResult:
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    parsed_json: Any | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def require_ok(self, message: str | None = None) -> CmuxCommandResult:
        if not self.ok:
            raise CmuxCommandError(self, message=message)
        return self

    def json_dict(self) -> dict[str, Any]:
        return self.parsed_json if isinstance(self.parsed_json, dict) else {}


@dataclass(frozen=True)
class CmuxTarget:
    workspace_ref: str | None = None
    workspace_id: str | None = None
    pane_ref: str | None = None
    pane_id: str | None = None
    surface_ref: str | None = None
    surface_id: str | None = None
    socket_path: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> CmuxTarget:
        env = environ or os.environ
        return cls(
            workspace_ref=env.get("CMUX_WORKSPACE_REF"),
            workspace_id=env.get("CMUX_WORKSPACE_ID"),
            pane_ref=env.get("CMUX_PANE_REF"),
            pane_id=env.get("CMUX_PANE_ID"),
            surface_ref=env.get("CMUX_SURFACE_REF"),
            surface_id=env.get("CMUX_SURFACE_ID"),
            socket_path=env.get("CMUX_SOCKET_PATH"),
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> CmuxTarget:
        data = data or {}
        workspace = _mapping(data.get("workspace"))
        pane = _mapping(data.get("pane"))
        surface = _mapping(data.get("surface"))
        return cls(
            workspace_ref=_first_str(data.get("workspace_ref"), workspace.get("ref")),
            workspace_id=_first_str(data.get("workspace_id"), workspace.get("id")),
            pane_ref=_first_str(data.get("pane_ref"), pane.get("ref")),
            pane_id=_first_str(data.get("pane_id"), pane.get("id")),
            surface_ref=_first_str(data.get("surface_ref"), surface.get("ref")),
            surface_id=_first_str(data.get("surface_id"), surface.get("id")),
            socket_path=_first_str(data.get("socket_path"), data.get("socket")),
        )


@dataclass(frozen=True)
class CmuxCapabilities:
    raw: dict[str, Any] = field(default_factory=dict)
    commands: frozenset[str] = frozenset()

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> CmuxCapabilities:
        raw = dict(data or {})
        command_values = raw.get("commands") or raw.get("methods") or raw.get("capabilities") or []
        if isinstance(command_values, Mapping):
            commands = frozenset(str(key) for key in command_values.keys())
        else:
            commands = frozenset(str(item) for item in command_values if isinstance(item, str))
        return cls(raw=raw, commands=commands)

    def supports(self, command: str) -> bool:
        return command in self.commands


@dataclass(frozen=True)
class CmuxHITLPolicy:
    focus_new_surfaces: bool = False
    browser_focus: bool = False
    allow_select_workspace: bool = False
    notify: bool = True
    open_browser: bool = True

    @classmethod
    def non_disruptive(cls) -> CmuxHITLPolicy:
        return cls()

    @classmethod
    def interrupt_for_handoff(cls) -> CmuxHITLPolicy:
        return cls(focus_new_surfaces=True, browser_focus=True, allow_select_workspace=True, notify=True, open_browser=True)

    def focus_value(self, *, browser: bool = False) -> str:
        allowed = self.browser_focus if browser else self.focus_new_surfaces
        return "true" if allowed else "false"


@dataclass(frozen=True)
class CmuxWorkspaceLayout:
    workspace_ref: str | None
    workspace_id: str | None = None
    panes: dict[str, str] = field(default_factory=dict)
    surfaces: dict[str, str] = field(default_factory=dict)
    target: CmuxTarget | None = None

    @property
    def agent_surface_ref(self) -> str | None:
        return self.surfaces.get("agent")

    @property
    def run_note_surface_ref(self) -> str | None:
        return self.surfaces.get("run_note")

    @property
    def task_note_surface_ref(self) -> str | None:
        return self.surfaces.get("task_note")

    @property
    def activity_surface_ref(self) -> str | None:
        return self.surfaces.get("activity_timeline")

    @property
    def review_browser_surface_ref(self) -> str | None:
        return self.surfaces.get("review_browser")

    @property
    def helper_pane_ref(self) -> str | None:
        return self.panes.get("helper")

    @classmethod
    def from_session(cls, record: Mapping[str, Any] | None) -> CmuxWorkspaceLayout:
        record = record or {}
        layout = _mapping(record.get("cmux_layout"))
        surfaces = _str_dict(layout.get("surfaces"))
        panes = _str_dict(layout.get("panes"))
        legacy_surface = _first_str(record.get("surface_ref"))
        if legacy_surface:
            surfaces["agent"] = legacy_surface
        workspace_ref = _first_str(record.get("workspace_ref"), layout.get("workspace_ref"))
        workspace_id = _first_str(record.get("workspace_id"), layout.get("workspace_id"))
        return cls(
            workspace_ref=workspace_ref,
            workspace_id=workspace_id,
            panes=panes,
            surfaces=surfaces,
            target=CmuxTarget.from_json(_mapping(layout.get("target"))),
        )

    def to_session_patch(self) -> dict[str, Any]:
        layout = {
            "workspace_ref": self.workspace_ref,
            "workspace_id": self.workspace_id,
            "panes": {key: value for key, value in self.panes.items() if value},
            "surfaces": {key: value for key, value in self.surfaces.items() if value},
        }
        if self.target:
            layout["target"] = {
                key: value
                for key, value in {
                    "workspace_ref": self.target.workspace_ref,
                    "workspace_id": self.target.workspace_id,
                    "pane_ref": self.target.pane_ref,
                    "pane_id": self.target.pane_id,
                    "surface_ref": self.target.surface_ref,
                    "surface_id": self.target.surface_id,
                    "socket_path": self.target.socket_path,
                }.items()
                if value
            }
        return {
            key: value
            for key, value in {
                "workspace_ref": self.workspace_ref,
                "workspace_id": self.workspace_id,
                "surface_ref": self.agent_surface_ref,
                "cmux_layout": _without_none(layout),
            }.items()
            if value is not None
        }

    def with_surface(self, role: str, surface_ref: str | None) -> CmuxWorkspaceLayout:
        if not surface_ref:
            return self
        surfaces = dict(self.surfaces)
        surfaces[role] = surface_ref
        return CmuxWorkspaceLayout(
            workspace_ref=self.workspace_ref,
            workspace_id=self.workspace_id,
            panes=dict(self.panes),
            surfaces=surfaces,
            target=self.target,
        )

    def with_pane(self, role: str, pane_ref: str | None) -> CmuxWorkspaceLayout:
        if not pane_ref:
            return self
        panes = dict(self.panes)
        panes[role] = pane_ref
        return CmuxWorkspaceLayout(
            workspace_ref=self.workspace_ref,
            workspace_id=self.workspace_id,
            panes=panes,
            surfaces=dict(self.surfaces),
            target=self.target,
        )


@dataclass(frozen=True)
class CmuxSession:
    task_id: str
    run_id: str | None
    status: str | None
    layout: CmuxWorkspaceLayout
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace_ref(self) -> str | None:
        return self.layout.workspace_ref

    @property
    def surface_ref(self) -> str | None:
        return self.layout.agent_surface_ref

    @classmethod
    def from_record(cls, task_id: str, record: Mapping[str, Any]) -> CmuxSession:
        return cls(
            task_id=str(record.get("task_id") or task_id),
            run_id=_first_str(record.get("run_id")),
            status=_first_str(record.get("status")),
            layout=CmuxWorkspaceLayout.from_session(record),
            raw=dict(record),
        )


@dataclass(frozen=True)
class CmuxArtifactCapture:
    path: Path
    layout: CmuxWorkspaceLayout | None = None


@dataclass(frozen=True)
class CmuxRuntimeState:
    sessions: dict[str, CmuxSession] = field(default_factory=dict)

    @classmethod
    def from_sessions_data(cls, data: Mapping[str, Any] | None) -> CmuxRuntimeState:
        session_data = _mapping((data or {}).get("sessions"))
        return cls(
            sessions={
                str(task_id): CmuxSession.from_record(str(task_id), _mapping(record))
                for task_id, record in session_data.items()
            }
        )

    @classmethod
    def load(cls, config: Any) -> CmuxRuntimeState:
        from .sessions import read_sessions

        return cls.from_sessions_data(read_sessions(config))

    def workspace_refs(self) -> set[str]:
        return {session.workspace_ref for session in self.sessions.values() if session.workspace_ref}

    def find_by_workspace(self, workspace_ref: str) -> CmuxSession | None:
        return next((session for session in self.sessions.values() if session.workspace_ref == workspace_ref), None)


class CmuxAdapter:
    def __init__(
        self,
        *,
        socket_path: str | None = None,
        password: str | None = None,
        default_timeout: int | None = 30,
    ):
        self.socket_path = socket_path
        self.password = password
        self.default_timeout = default_timeout

    def command(self, *args: str | Path, json_mode: bool = False, id_format: str | None = None) -> list[str]:
        command = ["cmux"]
        if self.socket_path:
            command.extend(["--socket", self.socket_path])
        if self.password:
            command.extend(["--password", self.password])
        if json_mode:
            command.append("--json")
        if id_format:
            command.extend(["--id-format", id_format])
        command.extend(str(arg) for arg in args)
        return command

    def run(
        self,
        *args: str | Path,
        json_mode: bool = False,
        id_format: str | None = None,
        timeout: int | None | object = _DEFAULT_TIMEOUT,
        check: bool = False,
    ) -> CmuxCommandResult:
        command = self.command(*args, json_mode=json_mode, id_format=id_format)
        run_timeout = self.default_timeout if timeout is _DEFAULT_TIMEOUT else timeout
        completed = subprocess.run(command, text=True, capture_output=True, timeout=run_timeout)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parsed_json = _parse_json(stdout)
        result = CmuxCommandResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            parsed_json=parsed_json,
        )
        if check:
            result.require_ok()
        return result

    def json(self, *args: str | Path, id_format: str | None = None) -> dict[str, Any]:
        result = self.run(*args, json_mode=True, id_format=id_format)
        if not result.ok:
            return {}
        return result.json_dict()

    def ping(self) -> bool:
        return self.run("ping").ok

    def capabilities(self) -> CmuxCapabilities:
        return CmuxCapabilities.from_json(self.json("capabilities", id_format="both"))

    def identify(self) -> CmuxTarget:
        data = self.json("identify", id_format="both")
        return CmuxTarget.from_json(data) if data else CmuxTarget.from_env()

    def new_workspace(self, *, name: str, description: str, cwd: str | Path, command: str, focus: bool = False) -> str:
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
        result = self.run(*args)
        if not result.ok:
            raise RuntimeError(f"cmux new-workspace failed: {result.stderr or result.stdout}")
        ref = _workspace_ref_from_data(result.json_dict()) or _workspace_ref_from_text(result.stdout)
        if ref:
            return ref
        raise RuntimeError(f"Unexpected cmux new-workspace response: {result.stdout!r}")

    def markdown_open(
        self,
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
        result = self.run(*args)
        if not result.ok:
            return None
        return surface_ref_from_output(result.stdout)

    def set_status(self, workspace_ref: str, status: str, *, icon: str = "sparkle", color: str = "#4c71f2") -> None:
        self.run("set-status", "agent", status, "--icon", icon, "--color", color, "--workspace", workspace_ref)

    def set_activity(self, workspace_ref: str, label: str, *, icon: str, color: str) -> None:
        self.run(
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

    def set_progress(self, workspace_ref: str, value: float, *, label: str) -> None:
        self.run("set-progress", f"{value:.2f}", "--label", label, "--workspace", workspace_ref)

    def clear_progress(self, workspace_ref: str) -> None:
        self.run("clear-progress", "--workspace", workspace_ref)

    def log(self, message: str, *, workspace_ref: str | None = None, source: str = "conductor") -> None:
        args = ["log", "--source", source]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        args.append(message)
        self.run(*args)

    def notify(self, title: str, body: str, workspace_ref: str | None = None) -> None:
        args = ["notify", "--title", title, "--body", body]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        self.run(*args)

    def open_browser_split(self, url: str, workspace_ref: str, *, focus: bool = True) -> str | None:
        result = self.run(
            "new-pane",
            "--type",
            "browser",
            "--direction",
            "right",
            "--workspace",
            workspace_ref,
            "--url",
            url,
            "--focus",
            "true" if focus else "false",
        )
        if not result.ok:
            return None
        return surface_ref_from_output(result.stdout)

    def open_browser_in_helper(
        self,
        layout: CmuxWorkspaceLayout,
        url: str,
        *,
        policy: CmuxHITLPolicy | None = None,
        role: str = "review_browser",
    ) -> CmuxWorkspaceLayout:
        if not layout.workspace_ref:
            raise ValueError("Cannot open browser without a cmux workspace")
        focus_policy = policy or CmuxHITLPolicy.non_disruptive()
        helper_pane = layout.helper_pane_ref or self.discover_helper_pane(layout)
        if helper_pane:
            result = self.run(
                "new-surface",
                "--workspace",
                layout.workspace_ref,
                "--pane",
                helper_pane,
                "--type",
                "browser",
                "--url",
                url,
                "--focus",
                focus_policy.focus_value(browser=True),
            )
        else:
            result = self.run(
                "new-pane",
                "--type",
                "browser",
                "--direction",
                "right",
                "--workspace",
                layout.workspace_ref,
                "--url",
                url,
                "--focus",
                focus_policy.focus_value(browser=True),
            )
        if not result.ok:
            return layout
        pane_ref = pane_ref_from_output(result.stdout) or helper_pane
        surface_ref = surface_ref_from_output(result.stdout)
        return layout.with_pane("helper", pane_ref).with_surface(role, surface_ref)

    def discover_helper_pane(self, layout: CmuxWorkspaceLayout) -> str | None:
        if not layout.workspace_ref:
            return None
        panes = self.json("list-panes", "--workspace", layout.workspace_ref).get("panes", [])
        for pane in panes:
            pane_ref = _first_str(pane.get("ref"))
            surfaces = pane.get("surfaces") if isinstance(pane.get("surfaces"), list) else []
            if any(surface.get("ref") == layout.agent_surface_ref for surface in surfaces):
                continue
            if any(surface.get("type") == "browser" for surface in surfaces):
                return pane_ref
        return None

    def browser_wait(self, surface_ref: str, *, load_state: str = "complete", timeout_ms: int = 15000) -> CmuxCommandResult:
        return self.run(
            "browser",
            surface_ref,
            "wait",
            "--load-state",
            load_state,
            "--timeout-ms",
            str(timeout_ms),
        )

    def browser_snapshot(self, surface_ref: str) -> dict[str, Any]:
        return self.json("browser", surface_ref, "snapshot", "--interactive", "--compact")

    def browser_screenshot(self, surface_ref: str, out_path: str | Path) -> Path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run("browser", surface_ref, "screenshot", "--out", path)
        return path

    def select_workspace(self, workspace_ref: str) -> None:
        self.run("select-workspace", "--workspace", workspace_ref)

    def terminal_surface(self, workspace_ref: str) -> str | None:
        panes = self.json("list-panes", "--workspace", workspace_ref).get("panes", [])
        for pane in panes:
            pane_ref = pane.get("ref")
            if not pane_ref:
                continue
            surfaces = self.json("list-pane-surfaces", "--workspace", workspace_ref, "--pane", str(pane_ref)).get(
                "surfaces", []
            )
            for surface in surfaces:
                if surface.get("type") == "terminal" and surface.get("ref"):
                    return str(surface["ref"])
        return None

    def wait_for_screen_text(
        self,
        workspace_ref: str,
        surface_ref: str | None,
        text: str,
        *,
        timeout: float = 30.0,
        interval: float = 0.25,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in self.read_screen(workspace_ref, surface_ref=surface_ref):
                return True
            time.sleep(interval)
        return False

    def send(self, workspace_ref: str, message: str, *, surface_ref: str | None = None) -> CmuxCommandResult:
        args = ["send", "--workspace", workspace_ref]
        if surface_ref:
            args.extend(["--surface", surface_ref])
        args.append(message)
        return self.run(*args)

    def send_enter(self, workspace_ref: str, *, surface_ref: str | None = None) -> CmuxCommandResult:
        args = ["send-key", "--workspace", workspace_ref]
        if surface_ref:
            args.extend(["--surface", surface_ref])
        args.append("enter")
        return self.run(*args)

    def close_workspace(self, workspace_ref: str) -> CmuxCommandResult:
        return self.run("close-workspace", "--workspace", workspace_ref)

    def list_workspaces(self) -> list[dict[str, Any]]:
        data = self.json("list-workspaces")
        return list(data.get("workspaces", []))

    def workspace_exists(self, workspace_ref: str) -> bool:
        return self.run("read-screen", "--workspace", workspace_ref, timeout=5).ok

    def workspace_surface_refs(self, workspace_ref: str) -> set[str]:
        refs: set[str] = set()
        panes = self.json("list-panes", "--workspace", workspace_ref).get("panes", [])
        for pane in panes:
            refs.update(str(ref) for ref in pane.get("surface_refs", []) if ref)
            surfaces = pane.get("surfaces") if isinstance(pane.get("surfaces"), list) else []
            refs.update(str(surface.get("ref")) for surface in surfaces if surface.get("ref"))
            pane_ref = pane.get("ref")
            if pane_ref:
                pane_surfaces = self.json(
                    "list-pane-surfaces",
                    "--workspace",
                    workspace_ref,
                    "--pane",
                    str(pane_ref),
                ).get("surfaces", [])
                refs.update(str(surface.get("ref")) for surface in pane_surfaces if surface.get("ref"))
        return refs

    def surface_exists(self, workspace_ref: str, surface_ref: str) -> bool:
        return surface_ref in self.workspace_surface_refs(workspace_ref)

    def read_screen(self, workspace_ref: str, *, surface_ref: str | None = None) -> str:
        args = ["read-screen", "--workspace", workspace_ref]
        if surface_ref:
            args.extend(["--surface", surface_ref])
        data = self.json(*args)
        if "text" in data:
            return str(data["text"])
        return self.run(*args).stdout

    def create_task_workspace(
        self,
        *,
        task_id: str,
        title: str,
        cwd: str | Path,
        command: str,
        policy: CmuxHITLPolicy | None = None,
    ) -> CmuxWorkspaceLayout:
        policy = policy or CmuxHITLPolicy.non_disruptive()
        workspace_ref = self.new_workspace(
            name=task_id,
            description=title,
            cwd=cwd,
            command=command,
            focus=policy.focus_new_surfaces,
        )
        return CmuxWorkspaceLayout(
            workspace_ref=workspace_ref,
            target=CmuxTarget(workspace_ref=workspace_ref, socket_path=self.socket_path),
        )

    def open_task_context(
        self,
        layout: CmuxWorkspaceLayout,
        *,
        task_note: str | Path,
        run_note: str | Path,
        activity_timeline: str | Path | None = None,
        policy: CmuxHITLPolicy | None = None,
    ) -> CmuxWorkspaceLayout:
        if not layout.workspace_ref:
            raise ValueError("Cannot open task context without a cmux workspace")
        policy = policy or CmuxHITLPolicy.non_disruptive()
        agent_surface = layout.agent_surface_ref or self.terminal_surface(layout.workspace_ref)
        layout = layout.with_surface("agent", agent_surface)
        run_surface = self.markdown_open(
            run_note,
            layout.workspace_ref,
            surface_ref=agent_surface,
            direction="right",
            focus=policy.focus_new_surfaces,
        )
        layout = layout.with_surface("run_note", run_surface)
        task_surface = self.markdown_open(
            task_note,
            layout.workspace_ref,
            surface_ref=run_surface or agent_surface,
            direction="down",
            focus=policy.focus_new_surfaces,
        )
        layout = layout.with_surface("task_note", task_surface)
        if activity_timeline:
            activity_surface = self.markdown_open(
                activity_timeline,
                layout.workspace_ref,
                surface_ref=task_surface or run_surface or agent_surface,
                direction="down",
                focus=policy.focus_new_surfaces,
            )
            layout = layout.with_surface("activity_timeline", activity_surface)
        return layout

    def send_to_agent(self, layout: CmuxWorkspaceLayout, message: str) -> None:
        if not layout.workspace_ref:
            raise ValueError("Cannot send to agent without a cmux workspace")
        self.send(layout.workspace_ref, message, surface_ref=layout.agent_surface_ref)
        self.send_enter(layout.workspace_ref, surface_ref=layout.agent_surface_ref)

    def surface_status(
        self,
        layout: CmuxWorkspaceLayout,
        *,
        status: str | None = None,
        activity: str | None = None,
        icon: str = "sparkle",
        color: str = "#4c71f2",
    ) -> None:
        if not layout.workspace_ref:
            return
        if status:
            self.set_status(layout.workspace_ref, status, icon=icon, color=color)
        if activity:
            self.set_activity(layout.workspace_ref, activity, icon=icon, color=color)

    def present_handoff(
        self,
        layout: CmuxWorkspaceLayout,
        *,
        pr_url: str,
        focus_policy: CmuxHITLPolicy | None = None,
    ) -> CmuxWorkspaceLayout:
        if not layout.workspace_ref:
            return layout
        policy = focus_policy or CmuxHITLPolicy.non_disruptive()
        if policy.open_browser:
            layout = self.open_browser_in_helper(layout, pr_url, policy=policy, role="review_browser")
        if policy.allow_select_workspace:
            self.select_workspace(layout.workspace_ref)
        if policy.notify:
            self.notify("Pull request ready", pr_url, layout.workspace_ref)
        return layout

    def capture_review_artifact(
        self,
        artifact_path: str | Path,
        *,
        title: str,
        evidence: Mapping[str, Any],
        layout: CmuxWorkspaceLayout | None = None,
        policy: CmuxHITLPolicy | None = None,
    ) -> Path:
        return self.capture_review_artifact_with_layout(
            artifact_path,
            title=title,
            evidence=evidence,
            layout=layout,
            policy=policy,
        ).path

    def capture_review_artifact_with_layout(
        self,
        artifact_path: str | Path,
        *,
        title: str,
        evidence: Mapping[str, Any],
        layout: CmuxWorkspaceLayout | None = None,
        policy: CmuxHITLPolicy | None = None,
    ) -> CmuxArtifactCapture:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_artifact_html(title=title, evidence=evidence), encoding="utf-8")
        opened_layout = layout
        if layout and layout.workspace_ref:
            focus_policy = policy or CmuxHITLPolicy.non_disruptive()
            if focus_policy.open_browser:
                opened_layout = self.open_browser_in_helper(
                    layout,
                    path.resolve().as_uri(),
                    policy=focus_policy,
                    role="artifact",
                )
        return CmuxArtifactCapture(path=path, layout=opened_layout)


_default_adapter = CmuxAdapter()


def run_cmux(*args: str, json_mode: bool = False, timeout: int | None = 30) -> tuple[str, int]:
    result = _default_adapter.run(*args, json_mode=json_mode, timeout=timeout)
    return result.stdout, result.returncode


def cmux_json(*args: str) -> dict[str, Any]:
    return _default_adapter.json(*args)


def ping() -> bool:
    return _default_adapter.ping()


def new_workspace(*, name: str, description: str, cwd: str | Path, command: str, focus: bool = False) -> str:
    return _default_adapter.new_workspace(name=name, description=description, cwd=cwd, command=command, focus=focus)


def markdown_open(
    path: str | Path,
    workspace_ref: str,
    *,
    surface_ref: str | None = None,
    direction: str | None = None,
    focus: bool = False,
) -> str | None:
    return _default_adapter.markdown_open(
        path,
        workspace_ref,
        surface_ref=surface_ref,
        direction=direction,
        focus=focus,
    )


def surface_ref_from_output(out: str) -> str | None:
    data = _parse_json(out)
    ref = _surface_ref_from_data(data if isinstance(data, dict) else {})
    if ref:
        return ref
    for token in out.replace(",", " ").split():
        if token.startswith("surface="):
            return token.split("=", 1)[1]
        if token.startswith("surface:"):
            return token
    return None


def pane_ref_from_output(out: str) -> str | None:
    data = _parse_json(out)
    ref = _pane_ref_from_data(data if isinstance(data, dict) else {})
    if ref:
        return ref
    for token in out.replace(",", " ").split():
        if token.startswith("pane="):
            return token.split("=", 1)[1]
        if token.startswith("pane:"):
            return token
    return None


def set_status(workspace_ref: str, status: str, *, icon: str = "sparkle", color: str = "#4c71f2") -> None:
    _default_adapter.set_status(workspace_ref, status, icon=icon, color=color)


def set_activity(workspace_ref: str, label: str, *, icon: str, color: str) -> None:
    _default_adapter.set_activity(workspace_ref, label, icon=icon, color=color)


def set_progress(workspace_ref: str, value: float, *, label: str) -> None:
    _default_adapter.set_progress(workspace_ref, value, label=label)


def clear_progress(workspace_ref: str) -> None:
    _default_adapter.clear_progress(workspace_ref)


def log(message: str, *, workspace_ref: str | None = None, source: str = "conductor") -> None:
    _default_adapter.log(message, workspace_ref=workspace_ref, source=source)


def notify(title: str, body: str, workspace_ref: str | None = None) -> None:
    _default_adapter.notify(title, body, workspace_ref)


def open_browser_split(url: str, workspace_ref: str) -> None:
    _default_adapter.open_browser_split(url, workspace_ref, focus=True)


def open_browser_in_helper(
    layout: CmuxWorkspaceLayout,
    url: str,
    *,
    policy: CmuxHITLPolicy | None = None,
    role: str = "review_browser",
) -> CmuxWorkspaceLayout:
    return _default_adapter.open_browser_in_helper(layout, url, policy=policy, role=role)


def browser_wait(surface_ref: str, *, load_state: str = "complete", timeout_ms: int = 15000) -> CmuxCommandResult:
    return _default_adapter.browser_wait(surface_ref, load_state=load_state, timeout_ms=timeout_ms)


def browser_snapshot(surface_ref: str) -> dict[str, Any]:
    return _default_adapter.browser_snapshot(surface_ref)


def browser_screenshot(surface_ref: str, out_path: str | Path) -> Path:
    return _default_adapter.browser_screenshot(surface_ref, out_path)


def select_workspace(workspace_ref: str) -> None:
    _default_adapter.select_workspace(workspace_ref)


def terminal_surface(workspace_ref: str) -> str | None:
    return _default_adapter.terminal_surface(workspace_ref)


def wait_for_screen_text(
    workspace_ref: str,
    surface_ref: str | None,
    text: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.25,
) -> bool:
    return _default_adapter.wait_for_screen_text(
        workspace_ref,
        surface_ref,
        text,
        timeout=timeout,
        interval=interval,
    )


def send(workspace_ref: str, message: str, *, surface_ref: str | None = None) -> CmuxCommandResult:
    return _default_adapter.send(workspace_ref, message, surface_ref=surface_ref)


def send_enter(workspace_ref: str, *, surface_ref: str | None = None) -> CmuxCommandResult:
    return _default_adapter.send_enter(workspace_ref, surface_ref=surface_ref)


def close_workspace(workspace_ref: str) -> CmuxCommandResult:
    return _default_adapter.close_workspace(workspace_ref)


def list_workspaces() -> list[dict[str, Any]]:
    return _default_adapter.list_workspaces()


def workspace_exists(workspace_ref: str) -> bool:
    return _default_adapter.workspace_exists(workspace_ref)


def workspace_surface_refs(workspace_ref: str) -> set[str]:
    return _default_adapter.workspace_surface_refs(workspace_ref)


def surface_exists(workspace_ref: str, surface_ref: str) -> bool:
    return _default_adapter.surface_exists(workspace_ref, surface_ref)


def read_screen(workspace_ref: str, *, surface_ref: str | None = None) -> str:
    return _default_adapter.read_screen(workspace_ref, surface_ref=surface_ref)


def create_task_workspace(
    *,
    task_id: str,
    title: str,
    cwd: str | Path,
    command: str,
    policy: CmuxHITLPolicy | None = None,
) -> CmuxWorkspaceLayout:
    return _default_adapter.create_task_workspace(
        task_id=task_id,
        title=title,
        cwd=cwd,
        command=command,
        policy=policy,
    )


def open_task_context(
    layout: CmuxWorkspaceLayout,
    *,
    task_note: str | Path,
    run_note: str | Path,
    activity_timeline: str | Path | None = None,
    policy: CmuxHITLPolicy | None = None,
) -> CmuxWorkspaceLayout:
    return _default_adapter.open_task_context(
        layout,
        task_note=task_note,
        run_note=run_note,
        activity_timeline=activity_timeline,
        policy=policy,
    )


def send_to_agent(layout: CmuxWorkspaceLayout, message: str) -> None:
    _default_adapter.send_to_agent(layout, message)


def surface_status(
    layout: CmuxWorkspaceLayout,
    *,
    status: str | None = None,
    activity: str | None = None,
    icon: str = "sparkle",
    color: str = "#4c71f2",
) -> None:
    _default_adapter.surface_status(layout, status=status, activity=activity, icon=icon, color=color)


def present_handoff(
    layout: CmuxWorkspaceLayout,
    *,
    pr_url: str,
    focus_policy: CmuxHITLPolicy | None = None,
) -> CmuxWorkspaceLayout:
    return _default_adapter.present_handoff(layout, pr_url=pr_url, focus_policy=focus_policy)


def capture_review_artifact(
    artifact_path: str | Path,
    *,
    title: str,
    evidence: Mapping[str, Any],
    layout: CmuxWorkspaceLayout | None = None,
    policy: CmuxHITLPolicy | None = None,
) -> Path:
    return _default_adapter.capture_review_artifact(
        artifact_path,
        title=title,
        evidence=evidence,
        layout=layout,
        policy=policy,
    )


def capture_review_artifact_with_layout(
    artifact_path: str | Path,
    *,
    title: str,
    evidence: Mapping[str, Any],
    layout: CmuxWorkspaceLayout | None = None,
    policy: CmuxHITLPolicy | None = None,
) -> CmuxArtifactCapture:
    return _default_adapter.capture_review_artifact_with_layout(
        artifact_path,
        title=title,
        evidence=evidence,
        layout=layout,
        policy=policy,
    )


def durable_asset_root(branch: str | None, artifact_slug: str, *, fallback_root: str | Path) -> Path:
    branch_slug = safe_path_slug(branch or "task")
    artifact_slug = safe_path_slug(artifact_slug)
    configured_root = os.environ.get("VAULT_CONDUCTOR_ASSET_ROOT")
    preferred_root = Path(configured_root) if configured_root else Path("/cmux-assets")
    try:
        root = preferred_root / branch_slug / artifact_slug
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        root = Path(fallback_root) / "cmux-assets" / branch_slug / artifact_slug
        root.mkdir(parents=True, exist_ok=True)
        return root


def safe_path_slug(value: str) -> str:
    slug = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "artifact"


def _parse_json(text: str) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_str(*values: Any) -> str | None:
    for value in values:
        if value is not None and value != "":
            return str(value)
    return None


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None and item != ""}


def _without_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _without_none(item) for key, item in value.items() if item is not None}
    return value


def _workspace_ref_from_data(data: Mapping[str, Any]) -> str | None:
    workspace = _mapping(data.get("workspace"))
    return _first_str(workspace.get("ref"), workspace.get("id"), data.get("workspace_ref"), data.get("ref"), data.get("id"))


def _workspace_ref_from_text(text: str) -> str | None:
    for token in text.split():
        if token.startswith("workspace:"):
            return token
    return None


def _surface_ref_from_data(data: Mapping[str, Any]) -> str | None:
    surface = _mapping(data.get("surface"))
    return _first_str(surface.get("ref"), surface.get("id"), data.get("surface_ref"), data.get("ref"), data.get("id"))


def _pane_ref_from_data(data: Mapping[str, Any]) -> str | None:
    pane = _mapping(data.get("pane"))
    return _first_str(pane.get("ref"), pane.get("id"), data.get("pane_ref"), data.get("pane_id"))


def _render_artifact_html(*, title: str, evidence: Mapping[str, Any]) -> str:
    rows = []
    for key, value in evidence.items():
        if isinstance(value, (Mapping, list, tuple)):
            rendered = json.dumps(value, indent=2, sort_keys=True)
        else:
            rendered = str(value)
        rows.append(
            "<tr>"
            f"<th>{escape(str(key))}</th>"
            f"<td><pre>{escape(rendered)}</pre></td>"
            "</tr>"
        )
    rows_html = "\n".join(rows) or "<tr><td><pre>No evidence captured.</pre></td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #17202a; }}
    h1 {{ font-size: 22px; margin: 0 0 18px; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 1100px; }}
    th, td {{ border: 1px solid #d7dde5; padding: 10px 12px; vertical-align: top; }}
    th {{ background: #f5f7fa; text-align: left; width: 220px; }}
    pre {{ margin: 0; white-space: pre-wrap; word-break: break-word; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }}
  </style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <table>
    {rows_html}
  </table>
</body>
</html>
"""
