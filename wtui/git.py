"""Git helpers for wtui.

This module stays free of curses so it can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable, Literal


class GitError(RuntimeError):
    """Raised when a git command fails."""


@dataclass
class Worktree:
    path: Path
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    bare: bool = False
    locked: str | None = None
    prunable: str | None = None
    dirty: bool | None = None
    missing: bool = False
    is_main: bool = False
    is_managed: bool = False


def run_git(args: Iterable[str], cwd: Path | str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git and return a text-mode CompletedProcess."""

    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise GitError(message)
    return result


def find_repo_root(cwd: Path | str | None = None) -> Path:
    search_path: Path | str | None = cwd
    if cwd is not None:
        candidate = Path(cwd)
        if candidate.is_file():
            search_path = candidate.parent

    result = run_git(["worktree", "list", "--porcelain"], cwd=search_path)
    worktrees = parse_worktree_porcelain(result.stdout)
    if not worktrees:
        raise GitError("git did not report a main worktree")

    # Git always lists the main worktree first. Resolving it here means wtui
    # uses one shared .worktrees directory even when launched from a linked
    # worktree or one of its subdirectories.
    return worktrees[0].path.resolve()


def parse_worktree_porcelain(output: str) -> list[Worktree]:
    worktrees: list[Worktree] = []
    current: dict[str, object] | None = None

    def finish() -> None:
        nonlocal current
        if not current:
            return
        path = current.get("path")
        if path is None:
            current = None
            return
        worktrees.append(
            Worktree(
                path=Path(str(path)),
                head=current.get("head") if isinstance(current.get("head"), str) else None,
                branch=current.get("branch") if isinstance(current.get("branch"), str) else None,
                detached=bool(current.get("detached")),
                bare=bool(current.get("bare")),
                locked=current.get("locked") if isinstance(current.get("locked"), str) else None,
                prunable=current.get("prunable") if isinstance(current.get("prunable"), str) else None,
            )
        )
        current = None

    for raw_line in output.splitlines():
        line = raw_line.rstrip("\n")
        if not line:
            finish()
            continue

        key, _, value = line.partition(" ")
        if key == "worktree":
            finish()
            current = {"path": value}
            continue
        if current is None:
            continue

        if key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value
        elif key == "detached":
            current["detached"] = True
        elif key == "bare":
            current["bare"] = True
        elif key == "locked":
            current["locked"] = value or "locked"
        elif key == "prunable":
            current["prunable"] = value or "prunable"

    finish()
    return worktrees


def display_branch(branch_ref: str | None, detached: bool = False) -> str:
    if branch_ref:
        prefix = "refs/heads/"
        if branch_ref.startswith(prefix):
            return branch_ref[len(prefix) :]
        return branch_ref
    if detached:
        return "(detached)"
    return "(unknown)"


def safe_worktree_name(branch_name: str) -> str:
    name = branch_name.strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    name = name.strip(".-")
    return name or "worktree"


def unique_worktree_path(worktree_root: Path, branch_name: str) -> Path:
    base_name = safe_worktree_name(branch_name)
    candidate = worktree_root / base_name
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        candidate = worktree_root / f"{base_name}-{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def list_worktrees(repo_root: Path, worktree_dir: str = ".worktrees") -> list[Worktree]:
    result = run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
    worktrees = parse_worktree_porcelain(result.stdout)
    managed_root = repo_root / worktree_dir

    for index, worktree in enumerate(worktrees):
        worktree.path = worktree.path.resolve()
        worktree.is_main = index == 0 or worktree.path == repo_root
        worktree.is_managed = is_relative_to(worktree.path, managed_root)
        worktree.missing = not worktree.path.exists()
        worktree.dirty = None if worktree.missing else is_dirty(worktree.path)

    return worktrees


def is_dirty(worktree_path: Path) -> bool:
    result = run_git(["status", "--short"], cwd=worktree_path, check=False)
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def local_branch_exists(repo_root: Path, branch_name: str) -> bool:
    result = run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"], cwd=repo_root, check=False)
    return result.returncode == 0


def create_worktree(repo_root: Path, branch_name: str, worktree_dir: str = ".worktrees") -> Path:
    branch = branch_name.strip()
    if not branch:
        raise GitError("branch name is required")

    worktree_root = repo_root / worktree_dir
    worktree_root.mkdir(parents=True, exist_ok=True)
    target_path = unique_worktree_path(worktree_root, branch)

    if local_branch_exists(repo_root, branch):
        args = ["worktree", "add", str(target_path), branch]
    else:
        args = ["worktree", "add", "-b", branch, str(target_path)]

    run_git(args, cwd=repo_root)
    return target_path


def switch_worktree_branch(repo_root: Path, worktree_path: Path, branch_name: str) -> Literal["created", "switched"]:
    branch = branch_name.strip()
    if not branch:
        raise GitError("branch name is required")

    if local_branch_exists(repo_root, branch):
        run_git(["switch", branch], cwd=worktree_path)
        return "switched"

    run_git(["switch", "-c", branch], cwd=worktree_path)
    return "created"


def unlock_worktree(repo_root: Path, worktree_path: Path) -> None:
    run_git(["worktree", "unlock", str(worktree_path)], cwd=repo_root)


def remove_worktree_directory(worktree_path: Path) -> None:
    if not worktree_path.exists():
        return
    if worktree_path.is_symlink() or not worktree_path.is_dir():
        raise GitError(f"worktree path is not a directory: {worktree_path}")
    try:
        shutil.rmtree(worktree_path)
    except OSError as error:
        raise GitError(f"failed to delete worktree directory {worktree_path}: {error}") from error


def prune_worktrees(repo_root: Path) -> None:
    run_git(["worktree", "prune", "--expire", "now"], cwd=repo_root)


def is_worktree_registered(repo_root: Path, worktree_path: Path) -> bool:
    result = run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
    target = worktree_path.resolve()
    return any(worktree.path.resolve() == target for worktree in parse_worktree_porcelain(result.stdout))


def delete_worktree(repo_root: Path, worktree: Worktree) -> Literal["metadata", "worktree"]:
    if worktree.locked:
        unlock_worktree(repo_root, worktree.path)

    if worktree.missing or worktree.prunable:
        prune_worktrees(repo_root)
        if is_worktree_registered(repo_root, worktree.path):
            raise GitError(f"stale worktree metadata was not removed: {worktree.path}")
        return "metadata"

    remove_worktree_directory(worktree.path)
    prune_worktrees(repo_root)
    if is_worktree_registered(repo_root, worktree.path) or worktree.path.exists():
        raise GitError(f"worktree was not removed: {worktree.path}")
    return "worktree"
