from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .markdown import parse_markdown, replace_section, stringify_markdown, write_file_atomic
from .tasks import TaskNote, now_iso, read_all_task_notes


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
    sync_project_notes(config, repos)
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


def sync_project_notes(config: Config, repos: list[RepoEntry] | None = None) -> dict[str, int]:
    if repos is None:
        repos = [RepoEntry.from_mapping(item) for item in load_repo_registry(config).get("repos", [])]
    for repo in repos:
        upsert_project_note(config, repo)
    return {"synced": len(repos)}


def upsert_project_note(config: Config, repo: RepoEntry) -> None:
    path = config.projects_dir / f"{repo.name}.md"
    existing_frontmatter: dict[str, Any] = {}
    existing_body = ""
    if path.exists():
        existing_frontmatter, existing_body = parse_markdown(path.read_text(encoding="utf-8"))
    now = now_iso()
    frontmatter = {
        "type": "project",
        "repo": repo.name,
        "repo_path": repo.path,
        "default_branch": repo.default_branch,
        "default_agent": repo.default_agent,
        "status": repo.status,
        "created": scalar_string(existing_frontmatter.get("created") or now),
        "updated": now,
    }
    body = render_project_note_body(config, repo, existing_body)
    write_file_atomic(path, stringify_markdown(frontmatter, body))


def render_project_note_body(config: Config, repo: RepoEntry, existing_body: str = "") -> str:
    body = existing_body or default_project_note_body(repo)
    body = replace_section(body, "Repo", f"{repo.name} is registered at `{display_path(repo.path)}`.")
    body = replace_section(body, "Common commands", existing_or_default_section(existing_body, "Common commands", commands_body(repo)))
    body = replace_section(body, "Agent rules", existing_or_default_section(existing_body, "Agent rules", instructions_body(repo)))
    body = replace_section(body, "Active tasks", active_tasks_table(config, repo.name))
    body = replace_section(body, "Completed tasks", completed_tasks_table(config, repo.name))
    return body


def default_project_note_body(repo: RepoEntry) -> str:
    return f"""# Repo

{repo.name} is registered at `{display_path(repo.path)}`.

# Common commands

{commands_body(repo)}

# Agent rules

{instructions_body(repo)}

# Active tasks

No active tasks.

# Completed tasks

No completed tasks.
"""


def existing_or_default_section(existing_body: str, heading: str, default: str) -> str:
    existing = section_content(existing_body, heading)
    return existing if existing else default


def section_content(body: str, heading: str) -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    heading_line = f"# {heading}"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading_line)
    except StopIteration:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("# "):
            end = index
            break
    return "\n".join(lines[start + 1 : end]).strip()


def commands_body(repo: RepoEntry) -> str:
    return "\n".join(f"- {name}: `{command}`" for name, command in sorted(repo.commands.items()))


def instructions_body(repo: RepoEntry) -> str:
    return "\n".join(f"- {instruction}" for instruction in (repo.instructions or []))


def active_tasks_table(config: Config, repo_name: str) -> str:
    tasks = [task for task in read_all_task_notes(config) if task.frontmatter.repo == repo_name and task.frontmatter.status != "done"]
    tasks.sort(key=lambda task: task.frontmatter.updated, reverse=True)
    tasks.sort(key=lambda task: priority_rank(task.frontmatter.priority))
    lines = ["| Task | Status | Priority | Agent | Updated |", "| --- | --- | --- | --- | --- |"]
    if not tasks:
        lines.append("| No active tasks. |  |  |  | |")
    else:
        for task in tasks:
            frontmatter = task.frontmatter
            lines.append(
                "| "
                + " | ".join(
                    [
                        task_link(task),
                        table_cell(frontmatter.status),
                        table_cell(frontmatter.priority),
                        table_cell(frontmatter.agent),
                        table_cell(frontmatter.updated),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def completed_tasks_table(config: Config, repo_name: str) -> str:
    tasks = [task for task in read_all_task_notes(config) if task.frontmatter.repo == repo_name and task.frontmatter.status == "done"]
    tasks.sort(key=lambda task: task.frontmatter.completed or task.frontmatter.updated, reverse=True)
    lines = ["| Task | Completed | Agent | Tests |", "| --- | --- | --- | --- |"]
    if not tasks:
        lines.append("| No completed tasks. |  |  | |")
    else:
        for task in tasks:
            frontmatter = task.frontmatter
            lines.append(
                "| "
                + " | ".join(
                    [
                        task_link(task),
                        table_cell(frontmatter.completed or frontmatter.updated),
                        table_cell(frontmatter.agent),
                        table_cell(frontmatter.last_test_status or ""),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def task_link(task: TaskNote) -> str:
    link_path = task.path.removesuffix(".md")
    return table_cell(f"[[{link_path}]]")


def table_cell(value: str | None) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def priority_rank(priority: str | None) -> int:
    if priority and len(priority) > 1 and priority[0].upper() == "P" and priority[1:].isdigit():
        return int(priority[1:])
    return 99


def display_path(value: str) -> str:
    path = Path(value).expanduser()
    try:
        return f"~/{path.resolve().relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def scalar_string(value: Any) -> str:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    text = str(value)
    if len(text) > 10 and text[4] == "-" and text[7] == "-" and text[10] == " ":
        return f"{text[:10]}T{text[11:]}"
    return text


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
