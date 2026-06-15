"""Integration tests for the write path (backend + Flow) on a temp repo.

These shell out to git (dev git is modern, but every command issued is
1.8.3.1-safe) and verify stage/unstage/discard/commit/branch/merge end to end.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from gitkit.backend.base import BackendError
from gitkit.backend.cli_git import CliGitBackend
from gitkit.core.flow import Flow, FlowError, translate


def _git(d, *a):
    subprocess.run(["git", *a], cwd=d, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _write(d, name, text):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(text)


class WriteCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gitkit_w_")
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", self.d],
                       check=True)
        _git(self.d, "config", "user.email", "t@example.com")
        _git(self.d, "config", "user.name", "Tester")
        _write(self.d, "a.txt", "hello\n")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-q", "-m", "init")
        self.be = CliGitBackend(root=self.d)
        self.flow = Flow(self.be)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _status(self):
        return {f.path: f for f in self.be.status()}

    def test_stage_then_commit(self):
        _write(self.d, "b.txt", "new\n")
        self.assertTrue(self._status()["b.txt"].is_untracked)
        self.flow.stage(["b.txt"])
        self.assertTrue(self._status()["b.txt"].is_staged)
        msg = self.flow.commit("add b")
        self.assertIn("add b", msg)
        self.assertEqual(self.be.status(), [])  # clean afterwards

    def test_unstage(self):
        _write(self.d, "b.txt", "new\n")
        self.flow.stage(["b.txt"])
        self.flow.unstage(["b.txt"])
        self.assertTrue(self._status()["b.txt"].is_untracked)

    def test_discard_restores_file(self):
        _write(self.d, "a.txt", "changed\n")
        self.assertTrue(self._status()["a.txt"].is_unstaged)
        self.flow.discard(["a.txt"])
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "hello\n")

    def test_discard_refuses_catch_all(self):
        with self.assertRaises(BackendError):
            self.be.discard(["."])

    def test_commit_without_staged_raises(self):
        with self.assertRaises(FlowError):
            self.flow.commit("nothing staged")

    def test_branch_checkout_merge_fast_forward(self):
        self.be.create_branch("feat")
        self.be.checkout("feat")
        _write(self.d, "c.txt", "c\n")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-q", "-m", "feat c")
        self.be.checkout("main")
        self.assertTrue(self.be.can_fast_forward("feat"))  # main is ancestor of feat
        r = self.be.merge("feat")
        self.assertTrue(r.ok)
        self.assertTrue(r.fast_forward)
        self.assertEqual(self.be.current_branch(), "main")

    def test_branch_without_upstream(self):
        self.be.create_branch("solo")
        b = next(x for x in self.be.branches() if x.name == "solo")
        self.assertIsNone(b.upstream)
        self.assertEqual((b.ahead, b.behind, b.upstream_gone), (0, 0, False))

    def test_branches_ahead_behind_1_8_safe(self):
        # the 1.8.3.1-safe path (@{upstream} + rev-list, no %(upstream:track)):
        # set an upstream, diverge, and check ahead/behind are reported.
        bare = tempfile.mkdtemp(prefix="gitkit_rem_")
        clone = tempfile.mkdtemp(prefix="gitkit_cl_")
        try:
            subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q",
                            "--bare", bare], check=True)
            _git(self.d, "remote", "add", "origin", bare)
            _git(self.d, "push", "-q", "-u", "origin", "main")  # main → origin/main
            subprocess.run(["git", "clone", "-q", bare, clone], check=True)
            _git(clone, "config", "user.email", "x@x")
            _git(clone, "config", "user.name", "x")
            _write(clone, "r.txt", "remote\n")
            _git(clone, "add", "-A"); _git(clone, "commit", "-q", "-m", "remote work")
            _git(clone, "push", "-q", "origin", "main")          # remote advances by 1
            _write(self.d, "l.txt", "local\n")
            _git(self.d, "add", "-A"); _git(self.d, "commit", "-q", "-m", "local work")
            _git(self.d, "fetch", "-q")                          # local advances by 1 → diverged
            b = next(x for x in self.be.branches() if x.name == "main")
            self.assertEqual(b.upstream, "origin/main")
            self.assertEqual(b.ahead, 1)
            self.assertEqual(b.behind, 1)
            self.assertFalse(b.upstream_gone)
        finally:
            shutil.rmtree(bare, ignore_errors=True)
            shutil.rmtree(clone, ignore_errors=True)

    def _make_conflict(self):
        """Leave the repo mid-merge with a.txt conflicted (on main, merging 'other')."""
        self.be.create_branch("other")
        _write(self.d, "a.txt", "main change\n")
        _git(self.d, "add", "-A"); _git(self.d, "commit", "-q", "-m", "main edit")
        self.be.checkout("other")
        _write(self.d, "a.txt", "other change\n")
        _git(self.d, "add", "-A"); _git(self.d, "commit", "-q", "-m", "other edit")
        self.be.checkout("main")
        with self.assertRaises(FlowError):
            self.flow.merge("other")  # conflict → FlowError

    def test_merge_conflict_reported(self):
        self._make_conflict()

    def test_conflict_state_and_incoming(self):
        self._make_conflict()
        self.assertTrue(self.flow.is_merging())
        self.assertEqual(self.flow.conflicts(), ["a.txt"])
        self.assertEqual(self.flow.incoming_label(), "other")
        self.assertIn("<<<<<<<", self.flow.conflict_text("a.txt"))

    def test_resolve_theirs_then_complete(self):
        self._make_conflict()
        self.flow.resolve_theirs("a.txt")          # take 'other' side
        self.assertEqual(self.flow.conflicts(), [])
        msg = self.flow.complete()
        self.assertIn("已完成合併", msg)
        self.assertFalse(self.flow.is_merging())
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "other change\n")

    def test_resolve_ours_keeps_our_side(self):
        self._make_conflict()
        self.flow.resolve_ours("a.txt")
        self.flow.complete()
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "main change\n")

    def test_manual_mark_resolved(self):
        self._make_conflict()
        _write(self.d, "a.txt", "hand merged\n")
        self.flow.mark_resolved(["a.txt"])
        self.assertEqual(self.flow.conflicts(), [])
        self.flow.complete()
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "hand merged\n")

    def test_complete_blocked_while_unresolved(self):
        self._make_conflict()
        with self.assertRaises(FlowError):
            self.flow.complete()  # still has conflicts

    def test_abort_restores_pre_merge(self):
        self._make_conflict()
        self.flow.abort()
        self.assertFalse(self.flow.is_merging())
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "main change\n")

    # ── revert ───────────────────────────────────────────────────
    def test_revert_clean(self):
        _write(self.d, "a.txt", "hello\nworld\n")
        _git(self.d, "commit", "-qam", "add world")
        head = self.be.repo_state().head_sha
        msg = self.flow.revert(head)            # undo the last commit
        self.assertIn("反向 commit", msg)
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "hello\n")   # back to before "add world"
        self.assertFalse(self.flow.in_progress())

    def test_revert_conflict_then_resolve(self):
        # revert an OLDER commit whose lines a later commit also touched → conflict
        _write(self.d, "a.txt", "v1\n"); _git(self.d, "commit", "-qam", "c1")
        target = self.be.repo_state().head_sha
        _write(self.d, "a.txt", "v2\n"); _git(self.d, "commit", "-qam", "c2")
        with self.assertRaises(FlowError):
            self.flow.revert(target)            # conflict
        self.assertEqual(self.flow.pending_op(), "revert")
        self.assertEqual(self.flow.conflicts(), ["a.txt"])
        self.flow.resolve_theirs("a.txt")       # take the revert's (restored) side
        msg = self.flow.complete()
        self.assertIn("revert", msg)
        self.assertFalse(self.flow.in_progress())

    def test_revert_empty_resolution_is_rejected(self):
        # resolving every conflict to "ours" leaves nothing to revert → clear error
        _write(self.d, "a.txt", "v1\n"); _git(self.d, "commit", "-qam", "c1")
        target = self.be.repo_state().head_sha
        _write(self.d, "a.txt", "v2\n"); _git(self.d, "commit", "-qam", "c2")
        with self.assertRaises(FlowError):
            self.flow.revert(target)
        self.flow.resolve_ours("a.txt")         # keep current → revert undoes nothing
        with self.assertRaises(FlowError):
            self.flow.complete()
        self.flow.abort()                       # the offered way out
        self.assertFalse(self.flow.in_progress())

    def test_revert_abort(self):
        _write(self.d, "a.txt", "v1\n"); _git(self.d, "commit", "-qam", "c1")
        target = self.be.repo_state().head_sha
        _write(self.d, "a.txt", "v2\n"); _git(self.d, "commit", "-qam", "c2")
        with self.assertRaises(FlowError):
            self.flow.revert(target)
        self.flow.abort()
        self.assertFalse(self.flow.in_progress())
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "v2\n")  # restored

    def test_revert_merge_needs_mainline(self):
        # build a merge commit, then revert it with mainline=1
        self.be.create_branch("br"); self.be.checkout("br")
        _write(self.d, "b.txt", "b\n"); _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "br work")
        self.be.checkout("main")
        _write(self.d, "a.txt", "main2\n"); _git(self.d, "commit", "-qam", "main work")
        self.be.merge("br")                     # non-ff merge commit
        head = self.be.repo_state().head_sha
        with self.assertRaises(FlowError):      # no mainline → git refuses
            self.flow.revert(head)
        self.assertFalse(self.flow.in_progress())
        msg = self.flow.revert(head, mainline=1)   # keep parent 1 (main)
        self.assertIn("反向 commit", msg)


class TranslateCase(unittest.TestCase):
    def test_known_messages(self):
        self.assertIn("pull", translate("error: failed to push some refs (non-fast-forward)"))
        self.assertIn("upstream", translate("There is no tracking information for the current branch."))

    def test_unknown_returns_first_line(self):
        self.assertEqual(translate("weird error\nsecond line"), "weird error")


if __name__ == "__main__":
    unittest.main()
