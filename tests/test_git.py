from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wtui.git import (
    Worktree,
    delete_worktree,
    display_branch,
    find_repo_root,
    is_worktree_registered,
    parse_worktree_porcelain,
    prune_worktrees,
    remove_worktree_directory,
    safe_worktree_name,
    switch_worktree_branch,
    unlock_worktree,
    unique_worktree_path,
)


class GitParsingTests(unittest.TestCase):
    def test_find_repo_root_uses_main_worktree_from_related_worktree(self) -> None:
        output = """worktree /repo
HEAD abcdef1234567890
branch refs/heads/main

worktree /repo/.worktrees/feature
HEAD 1234567890abcdef
branch refs/heads/feature

"""

        with patch("wtui.git.run_git") as run_git:
            run_git.return_value.stdout = output
            root = find_repo_root(Path("/repo/.worktrees/feature/src"))

        self.assertEqual(root, Path("/repo"))
        run_git.assert_called_once_with(
            ["worktree", "list", "--porcelain"],
            cwd=Path("/repo/.worktrees/feature/src"),
        )

    def test_find_repo_root_accepts_a_file_in_a_related_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src" / "module.py"
            source.parent.mkdir()
            source.touch()

            with patch("wtui.git.run_git") as run_git:
                run_git.return_value.stdout = "worktree /repo\nHEAD abcdef\nbranch refs/heads/main\n\n"
                root = find_repo_root(source)

        self.assertEqual(root, Path("/repo"))
        run_git.assert_called_once_with(
            ["worktree", "list", "--porcelain"],
            cwd=source.parent,
        )

    def test_find_repo_root_rejects_empty_worktree_output(self) -> None:
        with patch("wtui.git.run_git") as run_git:
            run_git.return_value.stdout = ""
            with self.assertRaisesRegex(RuntimeError, "did not report a main worktree"):
                find_repo_root(Path("/repo"))

    def test_parse_worktree_porcelain(self) -> None:
        output = """worktree /repo
HEAD abcdef1234567890
branch refs/heads/main

worktree /repo/.worktrees/feature-one
HEAD 1234567890abcdef
branch refs/heads/feature/one

worktree /repo/.worktrees/detached
HEAD 9999999999999999
detached
locked do not touch

"""

        worktrees = parse_worktree_porcelain(output)

        self.assertEqual(len(worktrees), 3)
        self.assertEqual(worktrees[0].path, Path("/repo"))
        self.assertEqual(worktrees[0].branch, "refs/heads/main")
        self.assertEqual(worktrees[1].branch, "refs/heads/feature/one")
        self.assertTrue(worktrees[2].detached)
        self.assertEqual(worktrees[2].locked, "do not touch")

    def test_display_branch_strips_local_ref_prefix(self) -> None:
        self.assertEqual(display_branch("refs/heads/feature/one"), "feature/one")
        self.assertEqual(display_branch(None, detached=True), "(detached)")

    def test_safe_worktree_name_sanitizes_branch_for_path(self) -> None:
        self.assertEqual(safe_worktree_name(" feature/foo "), "feature-foo")
        self.assertEqual(safe_worktree_name("bug fix: auth"), "bug-fix-auth")
        self.assertEqual(safe_worktree_name("..."), "worktree")

    def test_unique_worktree_path_adds_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "feature-x").mkdir()

            self.assertEqual(unique_worktree_path(root, "feature/x"), root / "feature-x-2")

    def test_switch_worktree_branch_switches_to_existing_branch(self) -> None:
        with (
            patch("wtui.git.local_branch_exists", return_value=True),
            patch("wtui.git.run_git") as run_git,
        ):
            result = switch_worktree_branch(Path("/repo"), Path("/repo/.worktrees/feature"), "feature")

        self.assertEqual(result, "switched")
        run_git.assert_called_once_with(["switch", "feature"], cwd=Path("/repo/.worktrees/feature"))

    def test_switch_worktree_branch_creates_missing_branch(self) -> None:
        with (
            patch("wtui.git.local_branch_exists", return_value=False),
            patch("wtui.git.run_git") as run_git,
        ):
            result = switch_worktree_branch(Path("/repo"), Path("/repo/.worktrees/feature"), "new-feature")

        self.assertEqual(result, "created")
        run_git.assert_called_once_with(
            ["switch", "-c", "new-feature"],
            cwd=Path("/repo/.worktrees/feature"),
        )

    def test_switch_worktree_branch_requires_a_name(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "branch name is required"):
            switch_worktree_branch(Path("/repo"), Path("/repo/.worktrees/feature"), "  ")

    def test_prune_worktrees_expires_immediately(self) -> None:
        with patch("wtui.git.run_git") as run_git:
            prune_worktrees(Path("/repo"))

        run_git.assert_called_once_with(["worktree", "prune", "--expire", "now"], cwd=Path("/repo"))

    def test_unlock_worktree_runs_git_unlock(self) -> None:
        with patch("wtui.git.run_git") as run_git:
            unlock_worktree(Path("/repo"), Path("/repo/.worktrees/locked"))

        run_git.assert_called_once_with(
            ["worktree", "unlock", "/repo/.worktrees/locked"],
            cwd=Path("/repo"),
        )

    def test_remove_worktree_directory_removes_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worktree"
            path.mkdir()
            (path / "file.txt").write_text("data")

            remove_worktree_directory(path)

            self.assertFalse(path.exists())

    def test_delete_worktree_prunes_missing_metadata(self) -> None:
        worktree = Worktree(path=Path("/repo/.worktrees/missing"), missing=True)

        with patch("wtui.git.prune_worktrees") as prune_worktrees, patch("wtui.git.is_worktree_registered", return_value=False):
            deleted = delete_worktree(Path("/repo"), worktree)

        self.assertEqual(deleted, "metadata")
        prune_worktrees.assert_called_once_with(Path("/repo"))

    def test_delete_worktree_force_removes_existing_worktree(self) -> None:
        worktree = Worktree(path=Path("/repo/.worktrees/feature"))

        with (
            patch("wtui.git.remove_worktree_directory") as remove_worktree_directory,
            patch("wtui.git.prune_worktrees") as prune_worktrees,
            patch("wtui.git.is_worktree_registered", return_value=False),
            patch.object(Path, "exists", return_value=False),
        ):
            deleted = delete_worktree(Path("/repo"), worktree)

        self.assertEqual(deleted, "worktree")
        remove_worktree_directory.assert_called_once_with(Path("/repo/.worktrees/feature"))
        prune_worktrees.assert_called_once_with(Path("/repo"))

    def test_delete_worktree_unlocks_locked_worktree_before_delete(self) -> None:
        worktree = Worktree(path=Path("/repo/.worktrees/locked"), locked="testing")

        with (
            patch("wtui.git.unlock_worktree") as unlock_worktree,
            patch("wtui.git.remove_worktree_directory"),
            patch("wtui.git.prune_worktrees"),
            patch("wtui.git.is_worktree_registered", return_value=False),
            patch.object(Path, "exists", return_value=False),
        ):
            deleted = delete_worktree(Path("/repo"), worktree)

        self.assertEqual(deleted, "worktree")
        unlock_worktree.assert_called_once_with(Path("/repo"), Path("/repo/.worktrees/locked"))

    def test_delete_worktree_raises_when_git_does_not_remove_it(self) -> None:
        worktree = Worktree(path=Path("/repo/.worktrees/feature"))

        with (
            patch("wtui.git.remove_worktree_directory"),
            patch("wtui.git.prune_worktrees"),
            patch("wtui.git.is_worktree_registered", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "worktree was not removed"):
                delete_worktree(Path("/repo"), worktree)

    def test_is_worktree_registered_reads_porcelain_output(self) -> None:
        output = """worktree /repo
HEAD abcdef1234567890
branch refs/heads/main

worktree /repo/.worktrees/feature
HEAD 1234567890abcdef
branch refs/heads/feature

"""

        with patch("wtui.git.run_git") as run_git:
            run_git.return_value.stdout = output
            self.assertTrue(is_worktree_registered(Path("/repo"), Path("/repo/.worktrees/feature")))
            self.assertFalse(is_worktree_registered(Path("/repo"), Path("/repo/.worktrees/missing")))


if __name__ == "__main__":
    unittest.main()
