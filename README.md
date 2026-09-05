# worktree-tui

`wtui` is a small dependency-free TUI for managing git worktrees stored under a repo-local `.worktrees/` directory.

## Run

From this repo:

```sh
./worktree-tui/bin/wtui
```

Or add `worktree-tui/bin` to your `PATH` and run:

```sh
wtui
```

Run it from anywhere inside the main worktree or any related linked worktree. `wtui` resolves the main worktree and shows the complete shared worktree list, so creation continues to use the main checkout's `.worktrees/` directory.

You can also point it at a directory or file in any related worktree:

```sh
wtui --repo ../some-linked-worktree/src/example.py
```

## Keys

- `j` / `k` or arrow keys: move selection
- `n`: create a new worktree under `.worktrees/`
- `b`: give the selected worktree a branch, switching to an existing local branch or creating a new one
- `d`: remove the selected worktree
- `r`: refresh
- `s` or `enter`: open a shell in the selected worktree
- `?`: show help
- `q` or `esc`: quit

## Behavior

- New worktrees are created at `.worktrees/<sanitized-branch-name>`.
- The new worktree prompt starts with editable default text: `vansh/`.
- If the branch already exists locally, `wtui` runs `git worktree add <path> <branch>`.
- If the branch does not exist locally, `wtui` runs `git worktree add -b <branch> <path>`.
- Assigning a branch uses `git switch <branch>` when it exists locally, or `git switch -c <branch>` to create it.
- Delete removes the selected worktree directory directly, then cleans Git metadata with `git worktree prune --expire now`.
- Delete unlocks locked worktree metadata before pruning when needed.
- Delete verifies the selected worktree disappeared from Git's worktree list before reporting success.
- Destructive actions ask for a single-key `y` confirmation.
- The main worktree is shown but cannot be removed.

## Test

```sh
python3 -m unittest discover -s worktree-tui/tests
```
