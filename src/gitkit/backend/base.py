"""GitBackend — the unified semantic contract.

Upper layers depend only on this interface, never on subprocess or git text.
Swapping the underlying git (or moving to libgit2) means writing a new
implementation of this ABC; the Flow/UI layers stay untouched.
See docs/architecture/layers.md §3-4.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from gitkit.core.models import (
    BranchInfo,
    Capabilities,
    Commit,
    DiffFile,
    FileEntry,
    Remote,
    RemoteBranch,
    RepoState,
)


class BackendError(Exception):
    """A git invocation failed. Flow catches this and translates to a human
    message — raw stderr never reaches the user directly."""

    def __init__(self, msg: str, *, argv: list[str], stderr: str):
        super().__init__(msg)
        self.argv = argv
        self.stderr = stderr


@dataclass
class MergeResult:
    ok: bool
    fast_forward: bool
    conflicts: list[str] = field(default_factory=list)


class GitBackend(ABC):
    # ── capabilities / repo basics ───────────────────────────────
    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def is_repo(self) -> bool: ...

    @abstractmethod
    def repo_root(self) -> str: ...

    # ── read / status (read-only) ────────────────────────────────
    @abstractmethod
    def repo_state(self) -> RepoState: ...

    @abstractmethod
    def current_branch(self) -> str | None: ...  # None => detached

    @abstractmethod
    def is_detached(self) -> bool: ...

    @abstractmethod
    def status(self) -> list[FileEntry]: ...

    @abstractmethod
    def log(self, *, limit: int = 200, skip: int = 0, all_refs: bool = True,
            order: str = "topo") -> list[Commit]: ...

    @abstractmethod
    def branches(self) -> list[BranchInfo]: ...  # local, refs/heads/ (cheap: no ahead/behind)

    @abstractmethod
    def branch_status(self, name: str) -> tuple:
        ...  # (upstream, ahead, behind, gone) for ONE branch — the costly part, on demand

    @abstractmethod
    def remote_branches(self) -> list[RemoteBranch]: ...  # refs/remotes/

    @abstractmethod
    def remotes(self) -> list[Remote]: ...

    @abstractmethod
    def remote_reachable(self) -> set: ...  # shas reachable from any remote ref

    # ── diff ─────────────────────────────────────────────────────
    @abstractmethod
    def diff_files(self, *, staged: bool = False) -> list[DiffFile]: ...

    @abstractmethod
    def diff_text(self, *, staged: bool = False) -> str: ...

    @abstractmethod
    def show_text(self, sha: str) -> str: ...

    @abstractmethod
    def commit_files(self, sha: str) -> list[DiffFile]: ...  # per-file +/- of a commit

    @abstractmethod
    def commit_file_diff(self, sha: str, path: str) -> str: ...  # one file of a commit

    @abstractmethod
    def commit_message(self, sha: str) -> str: ...  # full message + author/date

    @abstractmethod
    def file_diff(self, path: str, *, staged: bool = False) -> str: ...  # working/index

    # ── staging / commit (safe writes) ───────────────────────────
    @abstractmethod
    def stage(self, paths: list[str]) -> None: ...

    @abstractmethod
    def unstage(self, paths: list[str]) -> None: ...

    @abstractmethod
    def discard(self, paths: list[str]) -> None: ...  # ~ svn revert

    @abstractmethod
    def commit(self, message: str) -> Commit: ...

    # ── branch / merge ───────────────────────────────────────────
    @abstractmethod
    def create_branch(self, name: str) -> None: ...

    @abstractmethod
    def checkout(self, name: str) -> None: ...

    @abstractmethod
    def can_fast_forward(self, name: str) -> bool: ...

    @abstractmethod
    def merge(self, name: str) -> MergeResult: ...

    @abstractmethod
    def revert(self, sha: str, mainline: int | None = None) -> MergeResult: ...

    @abstractmethod
    def describe_commit(self, sha: str) -> str: ...  # 'shortsha subject'

    # ── conflict resolution (mid-merge / mid-revert) ─────────────
    @abstractmethod
    def pending_op(self) -> str | None: ...  # 'merge' | 'revert' | 'cherry-pick' | None

    @abstractmethod
    def is_merging(self) -> bool: ...  # a merge is in progress (MERGE_HEAD exists)

    @abstractmethod
    def revert_abort(self) -> None: ...  # back to pre-revert state

    @abstractmethod
    def unmerged_paths(self) -> list[str]: ...  # files with conflicts

    @abstractmethod
    def conflict_text(self, path: str) -> str: ...  # working file incl. <<< === >>>

    @abstractmethod
    def merging_branch(self) -> str | None: ...  # human label for what's merging in

    @abstractmethod
    def checkout_ours(self, path: str) -> None: ...  # keep our side + stage

    @abstractmethod
    def checkout_theirs(self, path: str) -> None: ...  # take their side + stage

    @abstractmethod
    def merge_abort(self) -> None: ...  # back to pre-merge state

    @abstractmethod
    def complete_merge(self) -> Commit: ...  # commit the resolved merge

    # ── remote (async + cancellable: the slow / network ops) ────
    @abstractmethod
    async def fetch(self, remote: str) -> None: ...

    @abstractmethod
    async def pull_ff_only(self, remote: str) -> None: ...

    @abstractmethod
    def push_preview(self) -> int: ...

    @abstractmethod
    async def push(self, remote: str, branch: str) -> None: ...

    # ── export / stash ───────────────────────────────────────────
    @abstractmethod
    def archive(self, dest_dir: str, ref: str = "HEAD") -> None: ...

    @abstractmethod
    def stash_save(self, message: str = "") -> None: ...

    @abstractmethod
    def stash_pop(self) -> None: ...
