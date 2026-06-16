"""Tree pagination + scroll-robustness tests.

The commit Tree loads the log a page at a time and grows as the cursor nears the
bottom, so a repo with thousands of commits isn't capped at one page and the
initial load stays cheap. These tests use a small page size on a modest repo to
exercise the same paths, and hammer the cursor to guard against the
fast-scroll crash (a highlight touching a transiently-absent Info widget).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

try:  # the git-1.8.3.1 CI job tests backend/core/graph only — no Textual there
    import gitkit.ui.app as appmod
    from gitkit.ui.app import GitkitApp
    from textual.widgets import ListView
except ImportError as e:
    raise unittest.SkipTest(f"Textual not installed (UI tests skipped): {e}")


def _git(d, *a):
    subprocess.run(["git", *a], cwd=d, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _repo_with_commits(n):
    d = tempfile.mkdtemp(prefix="gitkit_tree_")
    subprocess.run(["git", "init", "-q", d], check=True)
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "Tester")
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("0\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "commit 0")
    for i in range(1, n):
        _git(d, "commit", "-q", "--allow-empty", "-m", f"commit {i}")
    return d


async def _settle(app, pilot, n=60):
    from gitkit.ui.app import ProgressModal
    for _ in range(n):
        await pilot.pause(0.02)
        if (app._cmd_queue is not None and app._cmd_queue.empty()
                and app._cmd_task is None
                and not isinstance(app.screen, ProgressModal)):
            return
    raise AssertionError("never settled")


class TreePaginationCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._page, self._prefetch = appmod.TREE_PAGE, appmod.TREE_PREFETCH
        appmod.TREE_PAGE = 10
        appmod.TREE_PREFETCH = 3
        self.d = _repo_with_commits(25)

    def tearDown(self):
        appmod.TREE_PAGE, appmod.TREE_PREFETCH = self._page, self._prefetch
        shutil.rmtree(self.d, ignore_errors=True)

    def _commit_rows(self, app):
        from gitkit.ui.app import CommitItem
        tree = app.query_one("#tree", ListView)
        return sum(1 for c in tree.children if isinstance(c, CommitItem))

    async def _wait_rows(self, app, pilot, want, n=60):
        for _ in range(n):
            await pilot.pause(0.03)
            if self._commit_rows(app) >= want:
                return
        return

    async def test_first_page_then_grows_on_scroll(self):
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            tree = app.query_one("#tree", ListView)
            tree.focus()
            # initial load is exactly one page, and more is known to exist (the
            # Tree refill runs in its own worker, so wait for it to land first)
            await self._wait_rows(app, pilot, 10)
            self.assertEqual(self._commit_rows(app), 10)
            self.assertTrue(app._tree_has_more)
            # scroll down — each page loads in the background as we near the end
            for _ in range(40):
                await pilot.press("down")
                await pilot.pause(0.03)
                if self._commit_rows(app) >= 25:
                    break
            await _settle(app, pilot)
            self.assertEqual(self._commit_rows(app), 25)   # all commits reachable
            self.assertFalse(app._tree_has_more)           # no phantom extra page

    async def test_fast_scroll_does_not_crash(self):
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            tree = app.query_one("#tree", ListView)
            tree.focus()
            for _ in range(200):           # hammer the cursor (auto-repeat-like)
                await pilot.press("down")
            for _ in range(30):
                await pilot.pause(0.03)
            self.assertIsNone(app._exception)
            self.assertEqual(self._commit_rows(app), 25)

    async def test_reload_keeps_cursor(self):
        # a reload whose commit set is unchanged (e.g. a checkout that only moves
        # HEAD) must refresh rows in place and NOT snap the cursor back to the top
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            tree = app.query_one("#tree", ListView)
            tree.focus()
            await self._wait_rows(app, pilot, 10)
            tree.index = 5
            await pilot.pause(0.05)
            sha = getattr(tree.highlighted_child, "sha", None)
            self.assertIsNotNone(sha)
            await pilot.press("r")                      # reload
            await _settle(app, pilot)
            for _ in range(20):
                await pilot.pause(0.03)
            self.assertEqual(tree.index, 5)             # cursor kept its place
            self.assertEqual(getattr(tree.highlighted_child, "sha", None), sha)


class HeadFlashCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gitkit_hf_")
        subprocess.run(["git", "init", "-q", self.d], check=True)
        _git(self.d, "config", "user.email", "t@e.co")
        _git(self.d, "config", "user.name", "T")
        for i in range(3):
            with open(os.path.join(self.d, "f.txt"), "w") as f:
                f.write(f"v{i}\n")
            _git(self.d, "add", "-A")
            _git(self.d, "commit", "-qm", f"c{i}")
        c0 = subprocess.run(["git", "-C", self.d, "rev-list", "--max-parents=0", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
        _git(self.d, "branch", "b1", c0)  # older commit → checkout moves HEAD only

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    async def test_checkout_clears_stale_head_flash(self):
        # the in-place reload (checkout only moves the HEAD label) must not leave
        # the previous HEAD row stuck with the blink highlight class
        from gitkit.ui.app import CommitItem
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            tree = app.query_one("#tree", ListView)
            tree.focus()
            for _ in range(20):
                await pilot.pause(0.03)
            head = next(c for c in tree.children
                        if isinstance(c, CommitItem) and "HEAD" in c.commit.refs)
            old_sha = head.sha
            head.add_class("head-flash")            # pretend mid-blink (bg showing)
            app._run_flow(app.flow.checkout, "b1")  # HEAD → oldest; same commit set
            await _settle(app, pilot)
            for _ in range(20):
                await pilot.pause(0.03)
            old_row = next(c for c in tree.children
                           if isinstance(c, CommitItem) and c.sha == old_sha)
            self.assertNotIn("HEAD", old_row.commit.refs)        # HEAD really moved
            self.assertFalse(old_row.has_class("head-flash"))    # stale bg cleared


if __name__ == "__main__":
    unittest.main()
