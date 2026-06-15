"""CliGitBackend — subprocess implementation of GitBackend, 1.8.3.1-safe.

This is the ONLY layer that imports subprocess and the only place git text
formats are parsed. Every command here is built to run on git 1.8.3.1:
  - no `git -C`            -> we pass cwd=
  - no `--porcelain=v2`    -> plain `--porcelain` (v1)
  - `%d` not `%D`, `-s` not `--no-patch`, etc.
P0 scope: read methods are implemented; write methods are stubbed.
"""
from __future__ import annotations

import io
import os
import subprocess
import tarfile

from gitkit.backend.base import BackendError, GitBackend, MergeResult
from gitkit.backend.capabilities import detect
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

# log --format field/record separators: control chars that won't appear in content.
_FS = "\x1f"  # unit separator between fields
_RS = "\x1e"  # record separator between commits
_LOG_FORMAT = _FS.join(["%H", "%P", "%d", "%an", "%ad", "%s"]) + _RS


class CliGitBackend(GitBackend):
    def __init__(self, root: str, git: str = "git"):
        self.root = root
        self.git = git
        self._caps: Capabilities | None = None
        # Clean, parse-friendly environment: no pager, no color leakage.
        self._env = dict(os.environ)
        self._env["GIT_PAGER"] = "cat"
        # every invocation's args (without the boilerplate prefix) are recorded
        # here so the UI can surface the actual git command that ran
        self.cmdlog: list[list[str]] = []
        # content of a concrete commit (by 40-hex sha) never changes, so it is
        # safe to memoise forever — makes re-visiting commits while scrolling free
        self._commit_cache: dict = {}

    def _commit_cached(self, key, sha: str, fn):
        """Memoise an immutable per-commit read. Bypassed for non-sha refs
        (e.g. 'HEAD', short sha) whose content could change."""
        if len(sha) != 40:
            return fn()
        hit = self._commit_cache.get(key)
        if hit is not None:
            return hit
        val = fn()
        self._commit_cache[key] = val
        if len(self._commit_cache) > 400:  # simple bounded FIFO trim
            for k in list(self._commit_cache)[:100]:
                del self._commit_cache[k]
        return val

    # ── low-level runner ─────────────────────────────────────────
    def _argv(self, args: list[str]) -> list[str]:
        # --no-pager + color off applied to every invocation.
        return [self.git, "--no-pager", "-c", "color.ui=false", *args]

    def _run(self, args: list[str], *, check: bool = True) -> bytes:
        self.cmdlog.append(list(args))
        argv = self._argv(args)
        proc = subprocess.run(
            argv,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env,
        )
        if check and proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            raise BackendError(
                f"git {' '.join(args)} failed ({proc.returncode})",
                argv=argv,
                stderr=stderr,
            )
        return proc.stdout

    def _text(self, args: list[str], *, check: bool = True) -> str:
        return self._run(args, check=check).decode("utf-8", "surrogateescape")

    def _run_full(self, args: list[str]):
        """Run without raising; return (returncode, stdout_bytes, stderr_text)."""
        self.cmdlog.append(list(args))
        argv = self._argv(args)
        proc = subprocess.run(argv, cwd=self.root, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=self._env)
        return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")

    # ── capabilities / repo basics ───────────────────────────────
    def capabilities(self) -> Capabilities:
        if self._caps is None:
            self._caps = detect(self.git)
        return self._caps

    def is_repo(self) -> bool:
        out = self._run(["rev-parse", "--is-inside-work-tree"], check=False)
        return out.decode("utf-8", "replace").strip() == "true"

    def repo_root(self) -> str:
        return self._text(["rev-parse", "--show-toplevel"]).strip()

    # ── read / status ────────────────────────────────────────────
    def current_branch(self) -> str | None:
        name = self._text(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        return None if name == "HEAD" else name  # 'HEAD' => detached

    def is_detached(self) -> bool:
        return self.current_branch() is None

    def repo_state(self) -> RepoState:
        branch = self.current_branch()
        head = self._text(["rev-parse", "HEAD"], check=False).strip()
        return RepoState(
            root=self.repo_root(),
            current_branch=branch,
            detached=branch is None,
            head_sha=head,
            files=self.status(),
        )

    def status(self) -> list[FileEntry]:
        # porcelain v1 + -z: NUL-separated records, no quoting/escaping.
        data = self._run(["status", "--porcelain", "-z"])
        return parse_status_z(data)

    def log(self, *, limit: int = 200, skip: int = 0, all_refs: bool = True) -> list[Commit]:
        args = ["log", "--topo-order", "--decorate=short", f"--format={_LOG_FORMAT}",
                "--date=short", f"-n{limit}"]
        if skip:
            args.append(f"--skip={skip}")
        if all_refs:
            args.insert(1, "--all")
        out = self._text(args, check=False)
        return parse_log_records(out)

    def branches(self) -> list[BranchInfo]:
        # CHEAP / hot-path: just names + upstream (2 calls, independent of branch
        # count). ahead/behind is the expensive bit (one rev-list per branch), so
        # it is NOT computed here — call branch_status(name) on demand for that.
        out = self._text(["for-each-ref", "--format=%(refname:short)", "refs/heads/"])
        current = self.current_branch()
        upstreams = self._upstream_map()
        result: list[BranchInfo] = []
        for name in out.splitlines():
            name = name.strip()
            if not name:
                continue
            result.append(
                BranchInfo(
                    name=name,
                    upstream=upstreams.get(name),
                    ahead=0,
                    behind=0,
                    is_current=(name == current),
                    upstream_gone=False,
                )
            )
        return result

    def _upstream_map(self) -> dict:
        """{branch: 'origin/main'} from ONE `git config` call (1.8.3.1-safe),
        instead of a `rev-parse @{upstream}` per branch."""
        code, out, _ = self._run_full(["config", "--get-regexp", r"^branch\."])
        if code != 0:
            return {}
        remotes: dict = {}
        merges: dict = {}
        for line in out.decode("utf-8", "replace").splitlines():
            if " " not in line:
                continue
            key, val = line.split(" ", 1)
            if key.startswith("branch.") and key.endswith(".remote"):
                remotes[key[len("branch."):-len(".remote")]] = val.strip()
            elif key.startswith("branch.") and key.endswith(".merge"):
                merges[key[len("branch."):-len(".merge")]] = val.strip()
        upstream: dict = {}
        for name, merge in merges.items():
            short = merge[len("refs/heads/"):] if merge.startswith("refs/heads/") else merge
            remote = remotes.get(name)
            if remote and remote != ".":
                upstream[name] = f"{remote}/{short}"
            elif remote == ".":
                upstream[name] = short
        return upstream

    def branch_status(self, name: str) -> tuple:
        """(upstream, ahead, behind, gone) for ONE branch — 1.8.3.1-safe, computed
        on demand. No upstream → (None, 0, 0, False); upstream configured but its
        ref is missing → (name, 0, 0, True)."""
        code, out, _ = self._run_full(
            ["rev-parse", "--abbrev-ref", f"{name}@{{upstream}}"])
        if code != 0:  # no upstream configured for this branch
            return None, 0, 0, False
        upstream = out.decode("utf-8", "replace").strip()
        code, out, _ = self._run_full(
            ["rev-list", "--left-right", "--count", f"{name}@{{upstream}}...{name}"])
        if code != 0:  # upstream configured but its ref is gone
            return upstream, 0, 0, True
        nums = out.decode("utf-8", "replace").split()
        if len(nums) >= 2:
            behind, ahead = int(nums[0]), int(nums[1])  # left=upstream-only, right=branch-only
            return upstream, ahead, behind, False
        return upstream, 0, 0, False

    def remote_reachable(self) -> set:
        # every commit reachable from any remote-tracking ref (refs/remotes/*)
        out = self._text(["rev-list", "--remotes"], check=False)
        return set(out.split())

    def remote_branches(self) -> list[RemoteBranch]:
        # %(objectname:short) is 2.11+, so take full objectname and shorten here.
        fmt = "%(refname:short)\t%(objectname)"
        out = self._text(["for-each-ref", f"--format={fmt}", "refs/remotes/"])
        return parse_remote_branch_lines(out)

    def remotes(self) -> list[Remote]:
        out = self._text(["remote", "-v"], check=False)
        seen: dict[str, str] = {}
        for line in out.splitlines():
            if "(fetch)" not in line:
                continue
            name, rest = line.split("\t", 1)
            url = rest.rsplit(" ", 1)[0]
            seen.setdefault(name, url)
        return [Remote(name=n, url=u) for n, u in seen.items()]

    # ── diff ─────────────────────────────────────────────────────
    def diff_files(self, *, staged: bool = False) -> list[DiffFile]:
        args = ["diff", "--numstat", "-z"]
        if staged:
            args.append("--cached")
        data = self._run(args)
        return parse_numstat_z(data)

    def diff_text(self, *, staged: bool = False) -> str:
        args = ["diff", "--no-color"]
        if staged:
            args.append("--cached")
        return self._text(args)

    def show_text(self, sha: str) -> str:
        return self._commit_cached(
            ("show", sha), sha, lambda: self._text(["show", "--no-color", sha]))

    def commit_files(self, sha: str) -> list[DiffFile]:
        return self._commit_cached(("files", sha), sha, lambda: self._commit_files(sha))

    def _commit_files(self, sha: str) -> list[DiffFile]:
        # `--format=` drops the commit header so only numstat lines remain
        out = self._text(["show", "--numstat", "--format=", sha])
        result: list[DiffFile] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            added = 0 if cols[0] == "-" else int(cols[0])
            removed = 0 if cols[1] == "-" else int(cols[1])
            result.append(DiffFile(path=cols[2], added=added, removed=removed, status="M"))
        return result

    def commit_file_diff(self, sha: str, path: str) -> str:
        return self._commit_cached(
            ("fdiff", sha, path), sha,
            lambda: self._text(["show", "--no-color", sha, "--", path]))

    def commit_message(self, sha: str) -> str:
        return self._commit_cached(
            ("msg", sha), sha,
            lambda: self._text(["log", "-1", "--format=%an  %ad%n%n%B", "--date=short", sha]))

    def file_diff(self, path: str, *, staged: bool = False) -> str:
        args = ["diff", "--no-color"]
        if staged:
            args.append("--cached")
        args += ["--", path]
        return self._text(args)

    # ── staging / commit ─────────────────────────────────────────
    def stage(self, paths: list[str]) -> None:
        if paths:
            self._run(["add", "--", *paths])

    def unstage(self, paths: list[str]) -> None:
        if paths:  # index -> HEAD (not `restore --staged`, which is 2.23+)
            self._run(["reset", "-q", "HEAD", "--", *paths])

    def discard(self, paths: list[str]) -> None:
        # ≈ svn revert; refuse the catch-all forms — the safe boundary lives in
        # Flow, but never let a stray '.' wipe the whole worktree from here.
        bad = {"", ".", "*", "./"}
        if any(p in bad for p in paths):
            raise BackendError("discard refused a catch-all path",
                               argv=["checkout", "--", *paths], stderr="unsafe path")
        if paths:
            self._run(["checkout", "--", *paths])

    def commit(self, message: str) -> Commit:
        self._run(["commit", "-m", message])
        return self.log(limit=1, all_refs=False)[0]

    # ── branch / merge ───────────────────────────────────────────
    def create_branch(self, name: str) -> None:
        self._run(["branch", name])

    def checkout(self, name: str) -> None:
        self._run(["checkout", name])

    def can_fast_forward(self, name: str) -> bool:
        # merging <name> into HEAD fast-forwards iff HEAD is an ancestor of <name>
        code, _, _ = self._run_full(["merge-base", "--is-ancestor", "HEAD", name])
        return code == 0

    def merge(self, name: str) -> MergeResult:
        ff = self.can_fast_forward(name)
        code, _, _ = self._run_full(["merge", "--no-edit", name])
        if code == 0:
            return MergeResult(ok=True, fast_forward=ff, conflicts=[])
        conflicts = self._unmerged_paths()
        return MergeResult(ok=False, fast_forward=False, conflicts=conflicts)

    def revert(self, sha: str, mainline: int | None = None) -> MergeResult:
        # creates an inverse commit on top of HEAD; never rewrites history.
        args = ["revert", "--no-edit"]
        if mainline is not None:  # required to revert a merge commit
            args += ["-m", str(mainline)]
        args.append(sha)
        code, _, _ = self._run_full(args)
        if code == 0:
            return MergeResult(ok=True, fast_forward=False, conflicts=[])
        return MergeResult(ok=False, fast_forward=False,
                           conflicts=self._unmerged_paths())

    def describe_commit(self, sha: str) -> str:
        out = self._text(["log", "-1", "--format=%h %s", sha], check=False).strip()
        return out or sha[:7]

    def _unmerged_paths(self) -> list[str]:
        data = self._run(["diff", "--name-only", "--diff-filter=U", "-z"], check=False)
        return [p.decode("utf-8", "surrogateescape") for p in data.split(b"\x00") if p]

    # ── conflict resolution (mid-merge / mid-revert) ─────────────
    def pending_op(self) -> str | None:
        for head, name in (("MERGE_HEAD", "merge"),
                           ("REVERT_HEAD", "revert"),
                           ("CHERRY_PICK_HEAD", "cherry-pick")):
            code, _, _ = self._run_full(["rev-parse", "-q", "--verify", head])
            if code == 0:
                return name
        return None

    def is_merging(self) -> bool:
        code, _, _ = self._run_full(["rev-parse", "-q", "--verify", "MERGE_HEAD"])
        return code == 0

    def unmerged_paths(self) -> list[str]:
        return self._unmerged_paths()

    def conflict_text(self, path: str) -> str:
        full = os.path.join(self.root, path)
        try:
            with open(full, "r", encoding="utf-8", errors="surrogateescape") as f:
                return f.read()
        except OSError:
            return ""

    def merging_branch(self) -> str | None:
        code, out, _ = self._run_full(["rev-parse", "-q", "--verify", "MERGE_HEAD"])
        if code != 0:
            return None
        shas = out.decode("utf-8", "replace").split()
        if not shas:
            return None
        code, out2, _ = self._run_full(["name-rev", "--name-only", shas[0]])
        label = out2.decode("utf-8", "replace").strip() if code == 0 else ""
        return label or shas[0][:7]

    def checkout_ours(self, path: str) -> None:
        self._run(["checkout", "--ours", "--", path])
        self._run(["add", "--", path])

    def checkout_theirs(self, path: str) -> None:
        self._run(["checkout", "--theirs", "--", path])
        self._run(["add", "--", path])

    def merge_abort(self) -> None:
        self._run(["merge", "--abort"])  # 1.7.4+; restores pre-merge HEAD/worktree

    def revert_abort(self) -> None:
        self._run(["revert", "--abort"])  # 1.7.8+; restores pre-revert state

    def complete_merge(self) -> Commit:
        self._run(["commit", "--no-edit"])  # completes a merge OR revert (MERGE_MSG)
        return self.log(limit=1, all_refs=False)[0]

    # ── remote ───────────────────────────────────────────────────
    def fetch(self, remote: str) -> None:
        self._run(["fetch", remote])

    def pull_ff_only(self, remote: str) -> None:
        self._run(["pull", "--ff-only", remote])

    def push_preview(self) -> int:
        code, out, _ = self._run_full(["rev-list", "--count", "@{u}..HEAD"])
        return int(out.decode().strip()) if code == 0 and out.strip() else 0

    def push(self, remote: str, branch: str) -> None:
        self._run(["push", remote, branch])

    # ── export / stash ───────────────────────────────────────────
    def archive(self, dest_dir: str, ref: str = "HEAD") -> None:
        data = self._run(["archive", "--format=tar", ref])  # ≈ svn export
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.extractall(dest_dir)

    def stash_save(self, message: str = "") -> None:
        args = ["stash", "save"]  # not `stash push` (2.13+)
        if message:
            args.append(message)
        self._run(args)

    def stash_pop(self) -> None:
        self._run(["stash", "pop"])


# ── pure parsers (version-specific, but no subprocess → unit-testable) ──
def parse_status_z(data: bytes) -> list[FileEntry]:
    """Parse `git status --porcelain -z` output into FileEntry rows.

    Records are NUL-separated; each is `XY<space>path`. Rename/copy entries
    carry their source path in the FOLLOWING NUL token.
    """
    tokens = data.split(b"\x00")
    entries: list[FileEntry] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok:
            i += 1
            continue
        x = chr(tok[0])
        y = chr(tok[1])
        path = tok[3:].decode("utf-8", "surrogateescape")
        orig = None
        if x in ("R", "C") or y in ("R", "C"):
            i += 1
            if i < len(tokens):
                orig = tokens[i].decode("utf-8", "surrogateescape")
        entries.append(
            FileEntry(path=path, index_status=x, worktree_status=y, orig_path=orig)
        )
        i += 1
    return entries


def parse_log_records(text: str) -> list[Commit]:
    """Parse the _RS/_FS-delimited `git log` stream into Commit nodes."""
    commits: list[Commit] = []
    for record in text.split(_RS):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, deco, author, date, subject = record.split(_FS)
        commits.append(
            Commit(
                sha=sha,
                short_sha=sha[:7],
                parents=parents.split() if parents else [],
                refs=_parse_decoration(deco),
                author=author,
                date=date,
                subject=subject,
            )
        )
    return commits


def parse_remote_branch_lines(text: str) -> list[RemoteBranch]:
    """Parse `for-each-ref refs/remotes/` (name<TAB>objectname) into RemoteBranch.

    Drops the symbolic 'origin/HEAD' pointer: it short-forms to just the remote
    name (no '/') or ends in '/HEAD', and is not a real branch. We avoid
    %(symref) for the check because it is git 2.8+.
    """
    result: list[RemoteBranch] = []
    for line in text.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        name = parts[0]
        sha = parts[1] if len(parts) > 1 else ""
        if "/" not in name or name.endswith("/HEAD"):
            continue  # the 'origin/HEAD -> origin/master' symbolic pointer
        remote = name.split("/", 1)[0]
        result.append(RemoteBranch(name=name, remote=remote, short_sha=sha[:7]))
    return result


def parse_numstat_z(data: bytes) -> list[DiffFile]:
    """Parse `git diff --numstat -z`. Binary files report '-' for counts."""
    result: list[DiffFile] = []
    for tok in (t for t in data.split(b"\x00") if t):
        cols = tok.decode("utf-8", "surrogateescape").split("\t")
        if len(cols) < 3:
            continue
        added = 0 if cols[0] == "-" else int(cols[0])
        removed = 0 if cols[1] == "-" else int(cols[1])
        result.append(DiffFile(path=cols[2], added=added, removed=removed, status="M"))
    return result


# ── parsing helpers (version-specific, live with the backend) ────────
def _parse_decoration(deco: str) -> list[str]:
    """' (HEAD -> main, origin/main, tag: v1)' -> ['HEAD','main','origin/main','tag: v1']."""
    deco = deco.strip()
    if not deco:
        return []
    if deco.startswith("(") and deco.endswith(")"):
        deco = deco[1:-1]
    refs: list[str] = []
    for part in deco.split(", "):
        part = part.strip()
        if " -> " in part:  # 'HEAD -> main'
            refs.extend(p.strip() for p in part.split(" -> "))
        elif part:
            refs.append(part)
    return refs


