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
        msg = self.flow.complete_merge()
        self.assertIn("已完成合併", msg)
        self.assertFalse(self.flow.is_merging())
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "other change\n")

    def test_resolve_ours_keeps_our_side(self):
        self._make_conflict()
        self.flow.resolve_ours("a.txt")
        self.flow.complete_merge()
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "main change\n")

    def test_manual_mark_resolved(self):
        self._make_conflict()
        _write(self.d, "a.txt", "hand merged\n")
        self.flow.mark_resolved(["a.txt"])
        self.assertEqual(self.flow.conflicts(), [])
        self.flow.complete_merge()
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "hand merged\n")

    def test_complete_blocked_while_unresolved(self):
        self._make_conflict()
        with self.assertRaises(FlowError):
            self.flow.complete_merge()  # still has conflicts

    def test_abort_restores_pre_merge(self):
        self._make_conflict()
        self.flow.abort_merge()
        self.assertFalse(self.flow.is_merging())
        with open(os.path.join(self.d, "a.txt")) as f:
            self.assertEqual(f.read(), "main change\n")


class TranslateCase(unittest.TestCase):
    def test_known_messages(self):
        self.assertIn("pull", translate("error: failed to push some refs (non-fast-forward)"))
        self.assertIn("upstream", translate("There is no tracking information for the current branch."))

    def test_unknown_returns_first_line(self):
        self.assertEqual(translate("weird error\nsecond line"), "weird error")


if __name__ == "__main__":
    unittest.main()
