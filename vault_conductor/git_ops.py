from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Config
from .repos import is_git_repo
from .tasks import TaskNote


def run_git(args: list[str], cwd: Path | str | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)


def assert_inside(child: Path | str, parent: Path | str) -> None:
    child_path = Path(child).expanduser().resolve()
    parent_path = Path(parent).expanduser().resolve()
    if child_path != parent_path and parent_path not in child_path.parents:
        raise ValueError(f"Unsafe path outside allowed root: {child_path}")


def has_remote(repo_path: Path | str, remote: str = "origin") -> bool:
    result = run_git(["-C", str(repo_path), "remote"])
    return remote in result.stdout.split()


def fetch_origin(repo_path: Path | str) -> None:
    if has_remote(repo_path):
        run_git(["-C", str(repo_path), "fetch", "origin"])


def branch_exists(repo_path: Path | str, branch: str) -> bool:
    return run_git(["-C", str(repo_path), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0


def remote_branch_exists(repo_path: Path | str, branch: str) -> bool:
    return run_git(["-C", str(repo_path), "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"]).returncode == 0


def has_head_commit(repo_path: Path | str) -> bool:
    return run_git(["-C", str(repo_path), "rev-parse", "--verify", "HEAD^{commit}"]).returncode == 0


def ensure_worktree(config: Config, task: TaskNote) -> None:
    repo_path = Path(task.frontmatter.repo_path).expanduser().resolve()
    worktree_path = Path(task.frontmatter.worktree).expanduser().resolve()
    assert_inside(worktree_path, config.worktrees_root)
    if not is_git_repo(repo_path):
        raise ValueError(f"Cannot create worktree; not a git repository: {repo_path}")
    if not has_head_commit(repo_path):
        raise ValueError(
            f"Cannot create worktree for {task.frontmatter.id}; repo has no commits: {repo_path}. "
            "Create an initial commit before starting an agent task."
        )
    if worktree_path.exists():
        if is_git_repo(worktree_path):
            return
        raise ValueError(f"Worktree path exists but is not a git worktree: {worktree_path}")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    fetch_origin(repo_path)
    args = ["-C", str(repo_path), "worktree", "add", str(worktree_path)]
    branch = task.frontmatter.branch
    if branch_exists(repo_path, branch):
        args.append(branch)
    else:
        base = task.frontmatter.base_branch or "main"
        base_ref = f"origin/{base}" if remote_branch_exists(repo_path, base) else base
        args.extend(["-b", branch, base_ref])
    result = run_git(args)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create worktree:\n  git {' '.join(args)}\n{result.stderr or result.stdout}")


def remove_worktree(config: Config, task: TaskNote, force: bool = False) -> None:
    worktree_path = Path(task.frontmatter.worktree).expanduser().resolve()
    assert_inside(worktree_path, config.worktrees_root)
    if not worktree_path.exists():
        return
    if is_git_repo(worktree_path):
        args = ["-C", task.frontmatter.repo_path, "worktree", "remove", str(worktree_path)]
        if force:
            args.append("--force")
        result = run_git(args)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
    elif force:
        shutil.rmtree(worktree_path)
    else:
        raise ValueError(f"Refusing to delete non-worktree path without --force: {worktree_path}")


def git_status_short(path: Path | str) -> str:
    return run_git(["-C", str(path), "status", "--short"]).stdout


def get_diff_stat(path: Path | str) -> str:
    status = git_status_short(path).strip()
    diff = run_git(["-C", str(path), "diff", "--stat"]).stdout.strip()
    return "\n".join(part for part in [status, diff] if part)


def get_branch_diff_stat(path: Path | str, base_branch: str = "main") -> str:
    base_ref = f"origin/{base_branch}"
    if run_git(["-C", str(path), "rev-parse", "--verify", "--quiet", base_ref]).returncode != 0:
        base_ref = base_branch
    result = run_git(["-C", str(path), "diff", "--stat", f"{base_ref}...HEAD"])
    if result.returncode == 0:
        return result.stdout.strip()
    return run_git(["-C", str(path), "diff", "--stat", f"{base_ref}..HEAD"]).stdout.strip()


def get_diff_name_only(path: Path | str) -> list[str]:
    return [line for line in run_git(["-C", str(path), "diff", "--name-only"]).stdout.splitlines() if line]


def get_full_diff(path: Path | str) -> str:
    return run_git(["-C", str(path), "diff"]).stdout
