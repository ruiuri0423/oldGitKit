"""Version-agnostic data models shared across all layers.

These dataclasses are the language the backend speaks to the rest of the app.
Nothing here knows about git text formats — that lives inside each backend.
See docs/architecture/layers.md §2.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileEntry:
    """One row of `git status` — porcelain X/Y status carried as-is.

    index_status (X) = index <-> HEAD   (what `add` has staged)
    worktree_status (Y) = worktree <-> index   (changed but not staged)
    """

    path: str
    index_status: str  # 'M' 'A' 'D' 'R' 'C' '?' ' '
    worktree_status: str  # 'M' 'D' '?' ' '
    orig_path: str | None = None  # source path for renames/copies

    @property
    def is_untracked(self) -> bool:
        return self.index_status == "?"

    @property
    def is_staged(self) -> bool:
        return self.index_status not in (" ", "?")

    @property
    def is_unstaged(self) -> bool:
        # A worktree change that isn't an untracked marker.
        return self.worktree_status not in (" ", "?")

    @property
    def category(self) -> str:
        """Primary panel label. A file can also qualify for a second panel
        (e.g. 'MM' is both staged and modified) — use the booleans for that."""
        if self.is_untracked:
            return "untracked"
        if self.is_staged:
            return "staged"
        return "modified"


@dataclass
class Commit:
    """One Tree node. `parents` drives the DAG; rendering lives upstream."""

    sha: str
    short_sha: str
    parents: list[str]  # multiple => merge; empty => root
    refs: list[str]  # decoration names, parens already stripped
    author: str
    date: str  # YYYY-MM-DD
    subject: str

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass
class BranchInfo:
    name: str
    upstream: str | None  # e.g. 'origin/main'; None if no upstream
    ahead: int = 0
    behind: int = 0
    is_current: bool = False
    upstream_gone: bool = False  # upstream branch was deleted ([gone])


@dataclass
class Remote:
    name: str
    url: str


@dataclass
class RemoteBranch:
    """A remote-tracking branch under refs/remotes/ (e.g. 'origin/main').

    After a plain clone only the default branch is local; every other branch
    lives here. The right panel's 'Remote' section is built from these.
    """

    name: str  # 'origin/release/7.7.7'
    remote: str  # 'origin'
    short_sha: str


@dataclass
class DiffFile:
    path: str
    added: int
    removed: int
    status: str  # 'M' 'A' 'D' 'R'


@dataclass
class RepoState:
    root: str
    current_branch: str | None  # None => detached HEAD
    detached: bool
    head_sha: str
    files: list[FileEntry] = field(default_factory=list)


@dataclass
class Capabilities:
    version: tuple[int, int, int]
    has_dash_C: bool  # git -C (1.8.5+)
    has_switch_restore: bool  # git switch/restore (2.23+)
    has_porcelain_v2: bool  # status --porcelain=v2 (2.11+)

    def at_least(self, major: int, minor: int, patch: int = 0) -> bool:
        return self.version >= (major, minor, patch)
