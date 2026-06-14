# gitkit

[![CI](https://github.com/ruiuri0423/oldGitKit/actions/workflows/ci.yml/badge.svg)](https://github.com/ruiuri0423/oldGitKit/actions/workflows/ci.yml)

A terminal UI (TUI) that helps a team migrating from **SVN to git** *see* what git is
doing — the 3-stage index, local vs. remote, branch topology, detached HEAD, merges and
conflicts — instead of memorising commands.

It targets a constrained deployment: **CentOS 7**, system **git 1.8.3.1** (2013),
air-gapped, no root, miniconda Python 3.9. Every git command issued is 1.8.3.1-safe.

## Architecture

A strict layering keeps git specifics in one place and the UI testable:

```
 Textual TUI (ui/app.py)         ← keys, panels, modals, graph rendering
        │  calls only Flow methods
 Flow / Business Unit (core/flow.py)   ← safe-write boundary, dry-run previews,
        │  depends only on the ABC       error → zh translation
 GitBackend ABC (backend/base.py)
        │  one implementation
 CliGitBackend (backend/cli_git.py)    ← the ONLY layer that touches subprocess/git text
        │
      git 1.8.3.1
```

- **UI** never sees raw git text or stderr; it calls `Flow`, gets a short status string
  or a `FlowError` (already translated to Chinese).
- **Flow** is the safe boundary: it pre-checks preconditions, produces previews, and never
  lets a catch-all path reach a destructive command.
- **Backend** parses every git text format and is built for git 1.8 (no `git -C`,
  no `--porcelain=v2`, `%d` not `%D`, `-z` NUL parsing, etc.). Swapping git (or moving to
  libgit2) means writing one new backend; upper layers stay untouched.

## Key features

- **Branch-tree graph** (`graph/lanes.py`): first-publish-stable lanes (col0 = the trunk
  containing the root), box-drawing connectors, solid `●│` for commits on a remote vs.
  hollow/dashed `○╎` for local-only.
- **3-stage staging**, commit, branch create+switch, a filterable **branches popup**.
- **fetch / pull --ff-only / push** with a **staleness guard**: before merge/push/pull it
  detects *behind* / *diverged* from upstream and prompts to fetch & update first.
- **Conflict resolver**: on a merge conflict a guide screen opens — pick ours / theirs /
  manual per file, then complete or abort.
- **Command log** (`o`) of the actual git commands run and their results.

## Run

```sh
python -m gitkit /path/to/repo
```

## Develop

```sh
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e .
python -m unittest discover -s tests
```

See `docs/` for the layering rationale, the git-1.8 command map, and the tree/DAG
rendering pipeline.
