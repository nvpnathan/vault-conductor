from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .markdown import stringify_markdown, write_file_atomic
from .tasks import now_iso


@dataclass
class RepoEntry:
    name: str
    path: str
    default_branch: str = "main"
    default_agent: str = "codex"
    status: str = "active"
    last_scanned: str = ""
    commands: dict[str, str] = field(default_factory=dict)
    instructions: list[str] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RepoEntry":
        return cls(
            name=str(value["name"]),
            path=str(Path(value["path"]).expanduser().resolve()),
            default_branch=str(value.get("default_branch", "main")),
            default_agent=str(value.get("default_agent", "codex")),
            status=str(value.get("status", "active")),
            last_scanned=str(value.get("last_scanned", "")),
            commands={str(k): str(v) for k, v in (value.get("commands") or {}).items()},
            instructions=[str(item) for item in value.get("instructions", [])] if value.get("instructions") else None,
        )

    def to_mapping(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "path": self.path,
            "default_branch": self.default_branch,
            "default_agent": self.default_agent,
            "status": self.status,
            "last_scanned": self.last_scanned,
            "commands": self.commands,
        }
        if self.instructions:
            data["instructions"] = self.instructions
        return data


def registry_path(config: Config) -> Path:
    return config.system_dir / "repo-registry.yml"


def load_repo_registry(config: Config) -> dict[str, Any]:
    path = registry_path(config)
    if not path.exists():
        return {"version": 1, "repos": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"version": 1, "repos": []}
    data.setdefault("version", 1)
    data.setdefault("repos", [])
    return data


def write_repo_registry(config: Config, registry: dict[str, Any]) -> None:
    write_file_atomic(registry_path(config), yaml.safe_dump(registry, sort_keys=False, width=120))


def find_repo(config: Config, name: str) -> RepoEntry | None:
    for item in load_repo_registry(config).get("repos", []):
        if item.get("name") == name:
            return RepoEntry.from_mapping(item)
    return None


def scan_repos(config: Config) -> dict[str, Any]:
    repos: list[RepoEntry] = []
    root = config.repos_root
    if root.exists():
        walk(root, 0, repos, config)
    repos.sort(key=lambda repo: repo.name)
    registry = {"version": 1, "repos": [repo.to_mapping() for repo in repos]}
    write_repo_registry(config, registry)
    for repo in repos:
        upsert_project_note(config, repo)
    return registry


def walk(root: Path, depth: int, repos: list[RepoEntry], config: Config) -> None:
    if depth > int(config.repo_discovery.get("max_depth", 3)):
        return
    if is_git_repo(root):
        local = read_repo_config(root)
        repos.append(
            RepoEntry(
                name=local.get("name") or root.name,
                path=str(root.resolve()),
                default_branch=local.get("default_branch") or get_default_branch(root),
                default_agent=local.get("default_agent") or "codex",
                status="active",
                last_scanned=now_iso(),
                commands=local.get("commands") or {},
                instructions=local.get("instructions"),
            )
        )
        if not config.repo_discovery.get("include_nested_repos", False):
            return
    ignore = set(config.repo_discovery.get("ignore", []))
    for child in root.iterdir():
        if not child.is_dir() or child.name in ignore or child.name == ".git":
            continue
        walk(child, depth + 1, repos, config)


def read_repo_config(repo_path: Path) -> dict[str, Any]:
    for filename in [".agent-control-room.yml", ".agent-control-room.yaml"]:
        path = repo_path / filename
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def upsert_project_note(config: Config, repo: RepoEntry) -> None:
    path = config.projects_dir / f"{repo.name}.md"
    frontmatter = {
        "type": "project",
        "repo": repo.name,
        "repo_path": repo.path,
        "default_branch": repo.default_branch,
        "default_agent": repo.default_agent,
        "status": repo.status,
        "created": now_iso(),
        "updated": now_iso(),
    }
    if path.exists():
        return
    body = f"# {repo.name}\n\nRepo path: `{repo.path}`\n"
    write_file_atomic(path, stringify_markdown(frontmatter, body))


def is_git_repo(path: Path | str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def get_default_branch(repo_path: Path | str) -> str:
    remote = subprocess.run(
        ["git", "-C", str(repo_path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        text=True,
        capture_output=True,
    )
    if remote.returncode == 0 and remote.stdout.strip():
        return remote.stdout.strip().removeprefix("origin/")
    current = subprocess.run(
        ["git", "-C", str(repo_path), "branch", "--show-current"],
        text=True,
        capture_output=True,
    )
    return current.stdout.strip() or "main"
