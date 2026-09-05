from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wtui.git import Worktree
from wtui.tui import WorktreeTui, edit_text


class TuiDeleteTests(unittest.TestCase):
    def test_edit_text_inserts_at_cursor(self) -> None:
        buffer, cursor, done, canceled = edit_text("vansh/", 6, ord("a"))

        self.assertEqual(buffer, "vansh/a")
        self.assertEqual(cursor, 7)
        self.assertFalse(done)
        self.assertFalse(canceled)

    def test_edit_text_backspace_edits_default_value(self) -> None:
        buffer, cursor, done, canceled = edit_text("vansh/", 6, 127)

        self.assertEqual(buffer, "vansh")
        self.assertEqual(cursor, 5)
        self.assertFalse(done)
        self.assertFalse(canceled)

    def test_edit_text_enter_completes(self) -> None:
        buffer, cursor, done, canceled = edit_text("vansh/feature", 13, 10)

        self.assertEqual(buffer, "vansh/feature")
        self.assertEqual(cursor, 13)
        self.assertTrue(done)
        self.assertFalse(canceled)

    def test_edit_text_escape_cancels(self) -> None:
        _, _, done, canceled = edit_text("vansh/feature", 13, 27)

        self.assertTrue(done)
        self.assertTrue(canceled)

    def test_remove_deletes_locked_worktree_after_confirmation(self) -> None:
        app = WorktreeTui(repo_root=Path("/repo"))
        worktree = Worktree(path=Path("/repo/.worktrees/locked"), locked="testing")
        app.worktrees = [worktree]

        with (
            patch.object(app, "confirm", return_value=True),
            patch.object(app, "draw"),
            patch.object(app, "refresh"),
            patch("wtui.tui.delete_worktree", return_value="worktree") as delete_worktree,
        ):
            app.remove(object())

        delete_worktree.assert_called_once_with(Path("/repo"), worktree)
        self.assertEqual(app.message, "Deleted worktree: .worktrees/locked")

    def test_remove_draws_deleting_status_before_blocking_delete(self) -> None:
        app = WorktreeTui(repo_root=Path("/repo"))
        worktree = Worktree(path=Path("/repo/.worktrees/feature"))
        app.worktrees = [worktree]
        stdscr = object()

        def assert_deleting_status(repo_root: Path, selected: Worktree) -> str:
            self.assertEqual(repo_root, Path("/repo"))
            self.assertEqual(selected, worktree)
            self.assertEqual(app.message, "Deleting .worktrees/feature...")
            return "worktree"

        with (
            patch.object(app, "confirm", return_value=True),
            patch.object(app, "draw") as draw,
            patch.object(app, "refresh"),
            patch("wtui.tui.delete_worktree", side_effect=assert_deleting_status),
        ):
            app.remove(stdscr)

        draw.assert_called_once()
        self.assertIs(draw.call_args.args[0], stdscr)

    def test_remove_handles_keyboard_interrupt_without_traceback(self) -> None:
        app = WorktreeTui(repo_root=Path("/repo"))
        worktree = Worktree(path=Path("/repo/.worktrees/feature"))
        app.worktrees = [worktree]

        with (
            patch.object(app, "confirm", return_value=True),
            patch.object(app, "draw"),
            patch.object(app, "refresh"),
            patch("wtui.tui.delete_worktree", side_effect=KeyboardInterrupt),
        ):
            app.remove(object())

        self.assertEqual(app.error, "Delete interrupted: .worktrees/feature")

    def test_set_branch_creates_branch_for_selected_worktree(self) -> None:
        app = WorktreeTui(repo_root=Path("/repo"))
        worktree = Worktree(path=Path("/repo/.worktrees/feature"), detached=True)
        app.worktrees = [worktree]
        stdscr = object()

        with (
            patch.object(app, "prompt", return_value="vansh/feature") as prompt,
            patch.object(app, "refresh"),
            patch("wtui.tui.switch_worktree_branch", return_value="created") as switch_branch,
        ):
            app.set_branch(stdscr)

        prompt.assert_called_once_with(stdscr, "Branch name: ", default="vansh/")
        switch_branch.assert_called_once_with(Path("/repo"), worktree.path, "vansh/feature")
        self.assertEqual(app.message, "Created and switched to vansh/feature in .worktrees/feature")

    def test_set_branch_rejects_missing_worktree(self) -> None:
        app = WorktreeTui(repo_root=Path("/repo"))
        app.worktrees = [Worktree(path=Path("/repo/.worktrees/missing"), missing=True)]

        app.set_branch(object())

        self.assertEqual(app.error, "Selected worktree path is missing.")


if __name__ == "__main__":
    unittest.main()
