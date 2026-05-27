from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .constants import DEFAULT_COLUMNS


def expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


@dataclass
class AgentConfig:
    enabled: bool = True
    type: str = "command"
    command: str = "cmux"
    mode: str = "interactive"
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "AgentConfig":
        return cls(
            enabled=bool(value.get("enabled", True)),
            type=str(value.get("type", "command")),
            command=str(value.get("command", "")),
            mode=str(value.get("mode", "interactive")),
            args=[str(item) for item in value.get("args", [])],
            env={str(k): str(v) for k, v in (value.get("env") or {}).items()},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "type": self.type,
            "command": self.command,
            "mode": self.mode,
            "args": self.args,
            "env": self.env,
        }


@dataclass
class Config:
    version: int
    vault_path: Path
    repos_root: Path
    board_file: str
    worktrees_root: Path
    logs_root: Path
    prompts_root: Path
    state_root: Path
    columns: dict[str, str]
    human_authority: dict[str, Any]
    repo_discovery: dict[str, Any]
    branching: dict[str, Any]
    agents: dict[str, AgentConfig]
    commands: dict[str, str]
    obsidian: dict[str, Any]
    flags: dict[str, bool]

    @property
    def board_path(self) -> Path:
        return self.vault_path / self.board_file

    @property
    def control_room_dir(self) -> Path:
        return self.vault_path / "00 Control Room"

    @property
    def projects_dir(self) -> Path:
        return self.vault_path / "10 Projects"

    @property
    def tasks_dir(self) -> Path:
        return self.vault_path / "20 Agent Tasks"

    @property
    def runs_dir(self) -> Path:
        return self.vault_path / "30 Agent Runs"

    @property
    def templates_dir(self) -> Path:
        return self.vault_path / "50 Templates"

    @property
    def system_dir(self) -> Path:
        return self.vault_path / "90 System"


def default_config(
    vault: str | Path | None = None,
    repos: str | Path | None = None,
    runtime_root: str | Path | None = None,
) -> Config:
    vault_path = expand_path(vault or "~/Agent Control Room")
    repos_root = expand_path(repos or "~/repos")
    runtime = expand_path(runtime_root or "~/.agent-control-room")
    return Config(
        version=1,
        vault_path=vault_path,
        repos_root=repos_root,
        board_file="00 Control Room/Agent Control Room.md",
        worktrees_root=runtime / "worktrees",
        logs_root=runtime / "logs",
        prompts_root=runtime / "prompts",
        state_root=runtime / "state",
        columns=dict(DEFAULT_COLUMNS),
        human_authority={
            "human_only_statuses": ["done"],
            "never_auto_merge": True,
            "never_auto_delete_worktree": True,
        },
        repo_discovery={
            "max_depth": 3,
            "ignore": ["node_modules", ".cache", ".venv", "dist", "build"],
            "include_nested_repos": False,
        },
        branching={"prefix": "agent", "slug_max_length": 64},
        agents={
            "codex": AgentConfig(command="cmux", args=["codex-teams"]),
            "claude": AgentConfig(command="cmux", args=["claude-teams"]),
            "custom": AgentConfig(enabled=False, command="", mode="exec", args=[]),
        },
        commands={
            "default_setup": "",
            "default_test": "",
            "default_lint": "",
            "default_typecheck": "",
        },
        obsidian={
            "cli_command": "obsidian",
            "open_after_init": True,
            "open_board_after_init": True,
        },
        flags={"json": False, "dry_run": False, "verbose": False},
    )


def load_config(
    vault: str | Path | None = None,
    repos: str | Path | None = None,
    config: str | Path | None = None,
    runtime_root: str | Path | None = None,
    json_output: bool | None = None,
    dry_run: bool | None = None,
    verbose: bool | None = None,
) -> Config:
    base = default_config(vault=vault, repos=repos, runtime_root=runtime_root)
    config_path = expand_path(config) if config else base.system_dir / "control-room.config.yml"
    file_data: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        file_data = normalize_keys(loaded)

    merged = merge_config(config_to_mapping(base), file_data)
    if vault is not None:
        merged["vault_path"] = str(expand_path(vault))
    if repos is not None:
        merged["repos_root"] = str(expand_path(repos))
    if runtime_root is not None:
        runtime = expand_path(runtime_root)
        merged["worktrees_root"] = str(runtime / "worktrees")
        merged["logs_root"] = str(runtime / "logs")
        merged["prompts_root"] = str(runtime / "prompts")
        merged["state_root"] = str(runtime / "state")
    if json_output is not None:
        merged.setdefault("flags", {})["json"] = bool(json_output)
    if dry_run is not None:
        merged.setdefault("flags", {})["dry_run"] = bool(dry_run)
    if verbose is not None:
        merged.setdefault("flags", {})["verbose"] = bool(verbose)
    upgrade_builtin_agents(merged)

    return config_from_mapping(merged)


KEY_MAP = {
    "vaultPath": "vault_path",
    "reposRoot": "repos_root",
    "boardFile": "board_file",
    "worktreesRoot": "worktrees_root",
    "logsRoot": "logs_root",
    "promptsRoot": "prompts_root",
    "stateRoot": "state_root",
    "humanAuthority": "human_authority",
    "humanOnlyStatuses": "human_only_statuses",
    "neverAutoMerge": "never_auto_merge",
    "neverAutoDeleteWorktree": "never_auto_delete_worktree",
    "repoDiscovery": "repo_discovery",
    "maxDepth": "max_depth",
    "includeNestedRepos": "include_nested_repos",
    "slugMaxLength": "slug_max_length",
    "defaultSetup": "default_setup",
    "defaultTest": "default_test",
    "defaultLint": "default_lint",
    "defaultTypecheck": "default_typecheck",
    "cliCommand": "cli_command",
    "openAfterInit": "open_after_init",
    "openBoardAfterInit": "open_board_after_init",
}


def normalize_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_keys(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        mapped = KEY_MAP.get(str(key), str(key))
        out[mapped] = normalize_keys(item)
    return out


def merge_config(target: Any, source: Any) -> Any:
    if source is None:
        return target
    if isinstance(source, list):
        return source
    if not isinstance(source, dict):
        return source
    if not isinstance(target, dict):
        return source
    out = dict(target)
    for key, value in source.items():
        if key == "agents":
            agents = dict(out.get("agents", {}))
            for agent_name, agent_value in (value or {}).items():
                agents[agent_name] = {**agents.get(agent_name, {}), **(agent_value or {})}
            out[key] = agents
        else:
            out[key] = merge_config(out.get(key), value)
    return out


def config_from_mapping(value: dict[str, Any]) -> Config:
    agents = {
        str(name): AgentConfig.from_mapping(agent or {})
        for name, agent in (value.get("agents") or {}).items()
    }
    return Config(
        version=int(value.get("version", 1)),
        vault_path=expand_path(value["vault_path"]),
        repos_root=expand_path(value["repos_root"]),
        board_file=str(value.get("board_file", "00 Control Room/Agent Control Room.md")),
        worktrees_root=expand_path(value["worktrees_root"]),
        logs_root=expand_path(value["logs_root"]),
        prompts_root=expand_path(value["prompts_root"]),
        state_root=expand_path(value["state_root"]),
        columns={str(k): str(v) for k, v in value.get("columns", DEFAULT_COLUMNS).items()},
        human_authority=dict(value.get("human_authority", {})),
        repo_discovery=dict(value.get("repo_discovery", {})),
        branching=dict(value.get("branching", {})),
        agents=agents,
        commands={str(k): str(v) for k, v in value.get("commands", {}).items()},
        obsidian=dict(value.get("obsidian", {})),
        flags={str(k): bool(v) for k, v in value.get("flags", {}).items()},
    )


def upgrade_builtin_agents(value: dict[str, Any]) -> None:
    agents = value.setdefault("agents", {})
    codex = agents.get("codex")
    if isinstance(codex, dict) and codex.get("command") == "codex" and "exec" in codex.get("args", []):
        codex.update({"command": "cmux", "args": ["codex-teams"], "mode": "interactive"})
    claude = agents.get("claude")
    if isinstance(claude, dict) and claude.get("command") == "claude" and claude.get("args") in (["{{prompt}}"], []):
        claude.update({"command": "cmux", "args": ["claude-teams"], "mode": "interactive"})


def config_to_mapping(config: Config) -> dict[str, Any]:
    return {
        "version": config.version,
        "vault_path": str(config.vault_path),
        "repos_root": str(config.repos_root),
        "board_file": config.board_file,
        "worktrees_root": str(config.worktrees_root),
        "logs_root": str(config.logs_root),
        "prompts_root": str(config.prompts_root),
        "state_root": str(config.state_root),
        "columns": config.columns,
        "human_authority": config.human_authority,
        "repo_discovery": config.repo_discovery,
        "branching": config.branching,
        "agents": {name: agent.as_dict() for name, agent in config.agents.items()},
        "commands": config.commands,
        "obsidian": config.obsidian,
        "flags": config.flags,
    }


def to_tilde(path: str | Path) -> str:
    resolved = expand_path(path)
    home = Path.home().resolve()
    if resolved == home:
        return "~"
    try:
        return f"~/{resolved.relative_to(home)}"
    except ValueError:
        return str(resolved)


def config_to_yaml(config: Config) -> str:
    data = config_to_mapping(config)
    for key in ["vault_path", "repos_root", "worktrees_root", "logs_root", "prompts_root", "state_root"]:
        data[key] = to_tilde(data[key])
    return yaml.safe_dump(data, sort_keys=False, width=120)
