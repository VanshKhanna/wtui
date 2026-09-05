"""Curses UI for wtui."""

from __future__ import annotations

import argparse
import curses
from pathlib import Path
import os
import subprocess
import sys

from wtui import __version__
from wtui.git import (
    GitError,
    Worktree,
    create_worktree,
    delete_worktree,
    display_branch,
    find_repo_root,
    list_worktrees,
    switch_worktree_branch,
)


def edit_text(buffer: str, cursor: int, key: int) -> tuple[str, int, bool, bool]:
    if key in (curses.KEY_ENTER, 10, 13):
        return buffer, cursor, True, False
    if key == 27:
        return buffer, cursor, True, True
    if key in (curses.KEY_BACKSPACE, 8, 127):
        if cursor > 0:
            return buffer[: cursor - 1] + buffer[cursor:], cursor - 1, False, False
        return buffer, cursor, False, False
    if key in (curses.KEY_DC,):
        if cursor < len(buffer):
            return buffer[:cursor] + buffer[cursor + 1 :], cursor, False, False
        return buffer, cursor, False, False
    if key in (curses.KEY_LEFT, 2):
        return buffer, max(cursor - 1, 0), False, False
    if key in (curses.KEY_RIGHT, 6):
        return buffer, min(cursor + 1, len(buffer)), False, False
    if key in (curses.KEY_HOME, 1):
        return buffer, 0, False, False
    if key in (curses.KEY_END, 5):
        return buffer, len(buffer), False, False
    if key == 21:
        return "", 0, False, False
    if 32 <= key <= 126:
        return buffer[:cursor] + chr(key) + buffer[cursor:], cursor + 1, False, False
    return buffer, cursor, False, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage git worktrees stored under .worktrees.")
    parser.add_argument(
        "--repo",
        default=".",
        help="path in any related worktree, defaults to the current directory",
    )
    parser.add_argument("--worktree-dir", default=".worktrees", help="worktree directory inside the repo")
    parser.add_argument("--version", action="version", version=f"wtui {__version__}")
    args = parser.parse_args(argv)

    try:
        repo_root = find_repo_root(Path(args.repo).resolve())
    except GitError as error:
        print(f"wtui: {error}", file=sys.stderr)
        return 1

    app = WorktreeTui(repo_root=repo_root, worktree_dir=args.worktree_dir)
    return curses.wrapper(app.run)


class WorktreeTui:
    def __init__(self, repo_root: Path, worktree_dir: str = ".worktrees") -> None:
        self.repo_root = repo_root
        self.worktree_dir = worktree_dir
        self.worktrees: list[Worktree] = []
        self.selected = 0
        self.message = ""
        self.error = ""
        self.running = True

    def run(self, stdscr: curses.window) -> int:
        curses.curs_set(0)
        stdscr.keypad(True)
        self.refresh()

        while self.running:
            self.draw(stdscr)
            key = stdscr.getch()
            self.handle_key(stdscr, key)

        return 0

    def refresh(self) -> None:
        try:
            self.worktrees = list_worktrees(self.repo_root, self.worktree_dir)
            self.selected = min(self.selected, max(len(self.worktrees) - 1, 0))
            self.message = f"Loaded {len(self.worktrees)} worktree(s)."
            self.error = ""
        except GitError as error:
            self.error = str(error)

    def handle_key(self, stdscr: curses.window, key: int) -> None:
        self.message = ""
        self.error = ""

        if key in (ord("q"), 27):
            self.running = False
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move_selection(1)
        elif key in (curses.KEY_UP, ord("k")):
            self.move_selection(-1)
        elif key == ord("r"):
            self.refresh()
        elif key == ord("n"):
            self.create(stdscr)
        elif key == ord("b"):
            self.set_branch(stdscr)
        elif key == ord("d"):
            self.remove(stdscr)
        elif key in (ord("s"), curses.KEY_ENTER, 10, 13):
            self.spawn_shell(stdscr)
        elif key == ord("?"):
            self.message = "Keys: j/k move, n new, b branch, d delete, r refresh, s/enter shell, q quit"

    def move_selection(self, delta: int) -> None:
        if not self.worktrees:
            self.selected = 0
            return
        self.selected = max(0, min(len(self.worktrees) - 1, self.selected + delta))

    def create(self, stdscr: curses.window) -> None:
        branch = self.prompt(stdscr, "New branch name: ", default="vansh/")
        if not branch:
            self.message = "Create canceled."
            return
        try:
            target = create_worktree(self.repo_root, branch, self.worktree_dir)
            self.refresh()
            self.message = f"Created {target.relative_to(self.repo_root)}"
        except GitError as error:
            self.error = str(error)

    def remove(self, stdscr: curses.window) -> None:
        worktree = self.current_worktree()
        if worktree is None:
            return
        if worktree.is_main:
            self.error = "Refusing to remove the main worktree."
            return

        label = self.relative_path(worktree.path)
        if not self.confirm(stdscr, f"Delete {label}? Press y to confirm."):
            self.message = "Delete canceled."
            return

        self.message = f"Deleting {label}..."
        self.error = ""
        self.draw(stdscr)
        try:
            deleted = delete_worktree(self.repo_root, worktree)
            self.refresh()
            if deleted == "metadata":
                self.message = f"Deleted stale metadata: {label}"
            else:
                self.message = f"Deleted worktree: {label}"
        except GitError as error:
            self.error = str(error)
        except KeyboardInterrupt:
            try:
                self.refresh()
            except GitError as error:
                self.error = f"Delete interrupted; refresh failed: {error}"
            else:
                self.error = f"Delete interrupted: {label}"

    def set_branch(self, stdscr: curses.window) -> None:
        worktree = self.current_worktree()
        if worktree is None:
            return
        if worktree.missing:
            self.error = "Selected worktree path is missing."
            return
        if worktree.bare:
            self.error = "Cannot assign a branch to a bare worktree."
            return

        current_branch = display_branch(worktree.branch, worktree.detached)
        default = current_branch if worktree.branch else "vansh/"
        branch = self.prompt(stdscr, "Branch name: ", default=default)
        if not branch:
            self.message = "Branch change canceled."
            return

        try:
            result = switch_worktree_branch(self.repo_root, worktree.path, branch)
            self.refresh()
            action = "Created and switched to" if result == "created" else "Switched to"
            self.message = f"{action} {branch} in {self.relative_path(worktree.path)}"
        except GitError as error:
            self.error = str(error)

    def spawn_shell(self, stdscr: curses.window) -> None:
        worktree = self.current_worktree()
        if worktree is None:
            return
        if worktree.missing:
            self.error = "Selected worktree path is missing."
            return

        shell = os.environ.get("SHELL", "/bin/sh")
        curses.def_prog_mode()
        curses.endwin()
        try:
            subprocess.run([shell], cwd=worktree.path, check=False)
        finally:
            curses.reset_prog_mode()
            stdscr.keypad(True)
            curses.curs_set(0)
            self.refresh()

    def current_worktree(self) -> Worktree | None:
        if not self.worktrees:
            self.error = "No worktrees found."
            return None
        return self.worktrees[self.selected]

    def draw(self, stdscr: curses.window) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        if height < 8 or width < 50:
            self.addstr(stdscr, 0, 0, "Terminal too small for wtui.", curses.A_BOLD)
            stdscr.refresh()
            return

        title = f"wtui {__version__}  repo: {self.repo_root}"
        self.addstr(stdscr, 0, 0, self.truncate(title, width - 1), curses.A_BOLD)
        self.addstr(stdscr, 1, 0, f"worktree dir: {self.worktree_dir}")

        header = self.format_row("BRANCH", "PATH", "HEAD", "STATE", width)
        self.addstr(stdscr, 3, 0, header, curses.A_UNDERLINE)

        max_rows = height - 7
        start = max(0, self.selected - max_rows + 1)
        visible = self.worktrees[start : start + max_rows]

        for offset, worktree in enumerate(visible):
            index = start + offset
            attr = curses.A_REVERSE if index == self.selected else curses.A_NORMAL
            marker = ">" if index == self.selected else " "
            branch = display_branch(worktree.branch, worktree.detached)
            path = self.relative_path(worktree.path)
            head = (worktree.head or "")[:8]
            state = self.state_label(worktree)
            row = marker + " " + self.format_row(branch, path, head, state, width - 2)
            self.addstr(stdscr, 4 + offset, 0, row, attr)

        status_line = self.error or self.message
        attr = curses.A_BOLD if self.error else curses.A_NORMAL
        self.addstr(stdscr, height - 2, 0, self.truncate(status_line, width - 1), attr)
        footer = "j/k move  n new  b branch  d delete  r refresh  s/enter shell  ? help  q quit"
        self.addstr(stdscr, height - 1, 0, self.truncate(footer, width - 1), curses.A_DIM)
        stdscr.refresh()

    def state_label(self, worktree: Worktree) -> str:
        labels: list[str] = []
        if worktree.is_main:
            labels.append("main")
        elif worktree.is_managed:
            labels.append("managed")
        else:
            labels.append("external")

        if worktree.missing:
            labels.append("missing")
        elif worktree.dirty:
            labels.append("dirty")
        else:
            labels.append("clean")

        if worktree.locked:
            labels.append("locked")
        if worktree.prunable:
            labels.append("prunable")
        return ",".join(labels)

    def format_row(self, branch: str, path: str, head: str, state: str, width: int) -> str:
        branch_width = min(28, max(14, width // 4))
        head_width = 8
        state_width = min(24, max(12, width // 5))
        path_width = max(10, width - branch_width - head_width - state_width - 6)

        return (
            f"{self.truncate(branch, branch_width):<{branch_width}}  "
            f"{self.truncate(path, path_width):<{path_width}}  "
            f"{self.truncate(head, head_width):<{head_width}}  "
            f"{self.truncate(state, state_width):<{state_width}}"
        )

    def prompt(self, stdscr: curses.window, label: str, default: str = "") -> str:
        return self.read_line(stdscr, label, default=default)

    def confirm(self, stdscr: curses.window, label: str) -> bool:
        height, width = stdscr.getmaxyx()
        y = height - 2
        stdscr.move(y, 0)
        stdscr.clrtoeol()
        self.addstr(stdscr, y, 0, self.truncate(label, width - 1), curses.A_BOLD)
        stdscr.refresh()
        answer = stdscr.getch()
        curses.flushinp()
        return answer in (ord("y"), ord("Y"))

    def read_line(self, stdscr: curses.window, label: str, default: str = "") -> str:
        buffer = default
        cursor = len(buffer)
        curses.curs_set(1)
        curses.noecho()
        try:
            while True:
                height, width = stdscr.getmaxyx()
                y = height - 2
                input_x = min(len(label), width - 1)
                field_width = max(width - input_x - 1, 1)
                visible_start = max(0, cursor - field_width + 1)
                visible = buffer[visible_start : visible_start + field_width]
                cursor_x = min(input_x + cursor - visible_start, width - 1)

                stdscr.move(y, 0)
                stdscr.clrtoeol()
                self.addstr(stdscr, y, 0, self.truncate(label, width - 1), curses.A_BOLD)
                self.addstr(stdscr, y, input_x, visible)
                stdscr.move(y, cursor_x)
                stdscr.refresh()

                key = stdscr.getch()
                buffer, cursor, done, canceled = edit_text(buffer, cursor, key)
                if canceled:
                    return ""
                if done:
                    return buffer.strip()
        finally:
            curses.curs_set(0)

    def relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)

    @staticmethod
    def truncate(value: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(value) <= width:
            return value
        if width == 1:
            return value[:1]
        return value[: width - 1] + "~"

    @staticmethod
    def addstr(stdscr: curses.window, y: int, x: int, value: str, attr: int = curses.A_NORMAL) -> None:
        height, width = stdscr.getmaxyx()
        if y >= height or x >= width:
            return
        stdscr.addstr(y, x, value[: max(width - x - 1, 0)], attr)
