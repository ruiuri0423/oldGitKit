"""Flow — the Business Unit that the UI talks to.

It wraps a GitBackend and is the one place where:
  * the safe-write boundary lives (it only exposes safe actions; it never lets a
    catch-all path reach `discard`, and it pre-checks pre-conditions),
  * dry-run previews are produced (commit file-list, merge fast-forward, push
    count), and
  * raw BackendError / git stderr is translated into a human (zh) message.

UI calls a Flow method; on success it returns a short status string, on failure
it raises FlowError(message) — the UI shows that message verbatim in the status
bar and never sees raw git stderr. See docs/architecture/layers.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from gitkit.backend.base import BackendError, GitBackend
from gitkit.core.models import Commit, DiffFile

# substring → human message; first match wins
_TRANSLATE = [
    ("nothing to commit", "沒有要 commit 的變更"),
    ("no changes added to commit", "沒有已暫存的變更"),
    ("would be overwritten", "會覆蓋到本地未提交的變更,請先 commit 或 stash"),
    ("Your local changes", "本地有未提交的變更,請先 commit 或 stash"),
    ("non-fast-forward", "遠端有新 commit,請先 pull 再 push"),
    ("failed to push", "push 失敗:遠端有新 commit,請先 pull"),
    ("Could not read from remote", "無法連線到 remote"),
    ("Permission denied", "remote 認證失敗 / 權限不足"),
    ("not a git repository", "這裡不是 git repo"),
    ("did not match any file", "找不到該檔案"),
    ("did not match any", "找不到該分支 / 路徑"),
    ("already exists", "已存在"),
    ("CONFLICT", "發生合併衝突"),
    ("no tracking information", "此分支沒有設定 upstream"),
    ("no upstream", "此分支沒有設定 upstream"),
    ("unsafe path", "拒絕對 '.' 之類的萬用路徑做還原"),
]


def translate(stderr: str) -> str:
    s = stderr or ""
    for key, msg in _TRANSLATE:
        if key.lower() in s.lower():
            return msg
    return s.splitlines()[0] if s.strip() else "git 指令失敗"


class FlowError(Exception):
    """A user-facing, already-translated failure."""


@dataclass
class UpstreamState:
    """How a local branch sits relative to its upstream — drives the
    'behind / diverged' guards before merge / push / pull."""
    name: str
    upstream: Optional[str]  # 'origin/main' or None
    ahead: int
    behind: int
    kind: str  # none | current | ahead | behind | diverged

    @property
    def remote(self) -> Optional[str]:
        return self.upstream.split("/")[0] if self.upstream else None

    @property
    def ff_updatable(self) -> bool:
        return self.kind == "behind"  # strictly behind → can fast-forward to upstream

    @property
    def needs_attention(self) -> bool:
        return self.kind in ("behind", "diverged")


class Flow:
    def __init__(self, backend: GitBackend):
        self.be = backend

    def _do(self, fn, ok_msg: str) -> str:
        try:
            fn()
        except BackendError as e:
            raise FlowError(translate(e.stderr or str(e)))
        return ok_msg

    # ── staging ──────────────────────────────────────────────────
    def stage(self, paths: List[str]) -> str:
        if not paths:
            raise FlowError("沒有選取檔案")
        return self._do(lambda: self.be.stage(paths), f"已暫存 {len(paths)} 個檔案")

    def unstage(self, paths: List[str]) -> str:
        if not paths:
            raise FlowError("沒有選取檔案")
        return self._do(lambda: self.be.unstage(paths), f"已取消暫存 {len(paths)} 個檔案")

    def discard(self, paths: List[str]) -> str:
        if not paths:
            raise FlowError("沒有選取檔案")
        return self._do(lambda: self.be.discard(paths),
                        f"已還原 {len(paths)} 個檔案(丟棄工作區變更)")

    # ── commit ───────────────────────────────────────────────────
    def commit_preview(self) -> List[DiffFile]:
        return self.be.diff_files(staged=True)

    def commit(self, message: str) -> str:
        if not message.strip():
            raise FlowError("commit 訊息不可為空")
        if not self.be.diff_files(staged=True):
            raise FlowError("沒有已暫存的檔案,無法 commit")
        try:
            c: Commit = self.be.commit(message)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        return f"已 commit {c.short_sha}:{c.subject}"

    # ── branch / merge ───────────────────────────────────────────
    def create_branch(self, name: str) -> str:
        name = name.strip()
        if not name:
            raise FlowError("分支名不可為空")
        return self._do(lambda: self.be.create_branch(name), f"已建立分支 {name}")

    def checkout(self, name: str) -> str:
        return self._do(lambda: self.be.checkout(name), f"已切換到 {name}")

    def merge_preview(self, name: str) -> str:
        return ("fast-forward(不會產生 merge commit)" if self.be.can_fast_forward(name)
                else "會建立一個 merge commit")

    def merge(self, name: str) -> str:
        try:
            r = self.be.merge(name)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        if r.ok:
            return f"已合併 {name}" + ("(fast-forward)" if r.fast_forward else "(merge commit)")
        if r.conflicts:
            shown = ", ".join(r.conflicts[:5])
            raise FlowError(f"合併衝突,請手動解決:{shown}")
        raise FlowError(f"合併 {name} 失敗")

    def merge_into(self, target: str) -> str:
        """Merge the CURRENT branch INTO `target` (checkout target, then merge)."""
        source = self.be.current_branch()
        if source is None:
            raise FlowError("detached HEAD,沒有可合併的來源分支")
        if source == target:
            raise FlowError("來源與目標相同")
        try:
            self.be.checkout(target)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        try:
            r = self.be.merge(source)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        if r.ok:
            return (f"已把 {source} 合併進 {target}"
                    + ("(fast-forward)" if r.fast_forward else "(merge commit)"))
        if r.conflicts:
            raise FlowError(f"合併衝突,請手動解決:{', '.join(r.conflicts[:5])}")
        raise FlowError(f"把 {source} 合併進 {target} 失敗")

    # ── staleness (behind / diverged guards) ─────────────────────
    def upstream_state(self, name: str) -> UpstreamState:
        """Classify a local branch against its upstream (read-only). Used to warn
        before merge/push/pull when the branch is behind or has diverged. Queries
        just this one branch (branch_status) rather than every branch."""
        upstream, ahead, behind, gone = self.be.branch_status(name)
        if upstream is None or gone:
            return UpstreamState(name, None, 0, 0, "none")
        if behind == 0:
            kind = "ahead" if ahead > 0 else "current"
        elif ahead == 0:
            kind = "behind"
        else:
            kind = "diverged"
        return UpstreamState(name, upstream, ahead, behind, kind)

    async def update_then_merge(self, target: str, remote: str) -> str:
        """Fast-forward `target` to its upstream first, then merge the current
        branch into it — the clean path when the target was merely behind."""
        source = self.be.current_branch()
        if source is None:
            raise FlowError("detached HEAD,沒有可合併的來源分支")
        if source == target:
            raise FlowError("來源與目標相同")
        try:
            self.be.checkout(target)
            await self.be.pull_ff_only(remote)  # bring target up to its upstream
            r = self.be.merge(source)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        if r.ok:
            return (f"已更新 {target} 到最新並合併 {source}"
                    + ("(fast-forward)" if r.fast_forward else "(merge commit)"))
        if r.conflicts:
            raise FlowError(f"合併衝突,請手動解決:{', '.join(r.conflicts[:5])}")
        raise FlowError(f"把 {source} 合併進 {target} 失敗")

    async def update_then_push(self, remote: str, branch: str) -> str:
        """Fast-forward the current branch to its upstream, then push."""
        try:
            await self.be.pull_ff_only(remote)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        return await self.push(remote, branch)

    def integrate(self, remote: str) -> str:
        """Merge the current branch's upstream INTO it — the real-merge path for a
        diverged branch where ff-only pull cannot proceed."""
        branch = self.be.current_branch()
        if branch is None:
            raise FlowError("detached HEAD,無法整合")
        info = next((b for b in self.be.branches() if b.name == branch), None)
        if info is None or info.upstream is None:
            raise FlowError("此分支沒有設定 upstream")
        try:
            r = self.be.merge(info.upstream)  # e.g. merge origin/main into main
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        if r.ok:
            return (f"已把 {info.upstream} 整合進 {branch}"
                    + ("(fast-forward)" if r.fast_forward else "(merge commit)"))
        if r.conflicts:
            raise FlowError(f"整合衝突,請手動解決:{', '.join(r.conflicts[:5])}")
        raise FlowError(f"整合 {info.upstream} 失敗")

    # ── revert (safe rollback: an inverse commit, no history rewrite) ──
    def revert(self, sha: str, mainline: int = None) -> str:
        if self.be.current_branch() is None:
            raise FlowError("detached HEAD,無法 revert(會產生 commit,請先切到分支)")
        try:
            r = self.be.revert(sha, mainline=mainline)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        if r.ok:
            return f"已建立反向 commit 撤銷 {sha[:7]}(歷史未改寫)"
        if r.conflicts:
            raise FlowError(f"revert 衝突,請手動解決:{', '.join(r.conflicts[:5])}")
        raise FlowError(f"revert {sha[:7]} 失敗")

    # ── conflict resolution (mid-merge / mid-revert) ─────────────
    def pending_op(self) -> str:
        return self.be.pending_op()

    def in_progress(self) -> bool:
        return self.be.pending_op() is not None

    def is_merging(self) -> bool:
        return self.be.is_merging()

    def conflicts(self) -> List[str]:
        return self.be.unmerged_paths()

    def conflict_text(self, path: str) -> str:
        return self.be.conflict_text(path)

    def incoming_label(self) -> str:
        if self.be.pending_op() == "revert":
            return "revert 後(還原)的內容"
        return self.be.merging_branch() or "對方分支"

    def resolve_ours(self, path: str) -> str:
        return self._do(lambda: self.be.checkout_ours(path), f"已採用我方版本:{path}")

    def resolve_theirs(self, path: str) -> str:
        return self._do(lambda: self.be.checkout_theirs(path), f"已採用對方版本:{path}")

    def mark_resolved(self, paths: List[str]) -> str:
        if not paths:
            raise FlowError("沒有選取檔案")
        return self._do(lambda: self.be.stage(paths),
                        f"已標記為已解決:{len(paths)} 個檔案")

    def abort(self) -> str:
        """Abort the in-progress merge or revert, restoring the prior state."""
        if self.be.pending_op() == "revert":
            return self._do(self.be.revert_abort, "已放棄 revert,回到原狀")
        return self._do(self.be.merge_abort, "已放棄合併,回到合併前的狀態")

    def complete(self) -> str:
        """Commit the resolved merge or revert."""
        if self.be.unmerged_paths():
            raise FlowError("還有未解決的衝突,無法完成")
        op = self.be.pending_op()
        if op == "revert" and not self.be.diff_files(staged=True):
            # everything resolved to "ours" → the revert undoes nothing
            raise FlowError("revert 後沒有任何變更(等於未撤銷);若要結束請按『放棄 revert』")
        try:
            c: Commit = self.be.complete_merge()  # commit --no-edit (merge or revert)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        verb = "revert" if op == "revert" else "合併"
        return f"已完成{verb} {c.short_sha}:{c.subject}"

    # ── remote (async + cancellable) ─────────────────────────────
    async def fetch(self, remote: str) -> str:
        try:
            await self.be.fetch(remote)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        return f"已 fetch {remote}(更新遠端追蹤分支)"

    async def pull(self, remote: str) -> str:
        if self.be.current_branch() is None:
            raise FlowError("detached HEAD,無法 pull(請先切換到一個分支)")
        before = self.be.repo_state().head_sha
        try:
            await self.be.pull_ff_only(remote)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        after = self.be.repo_state().head_sha
        if before == after:
            return f"{remote}:已是最新,沒有可前進的 commit"
        return f"已 pull {remote},HEAD 前進到 {after[:7]}"

    def push_preview(self) -> int:
        return self.be.push_preview()

    async def push(self, remote: str, branch: str) -> str:
        try:
            n = self.be.push_preview()
        except BackendError:
            n = 0
        try:
            await self.be.push(remote, branch)
        except BackendError as e:
            raise FlowError(translate(e.stderr))
        return f"已 push {n} 筆到 {remote}/{branch}"
