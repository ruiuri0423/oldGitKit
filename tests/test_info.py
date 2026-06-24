"""Info panel: the file-list shows a FIXED window of files (pagination, not
accumulation) so widget count never grows with the commit size; and "/" searches
the files, jumping to the picked file's page and opening its diff."""
import os
import shutil
import subprocess
import tempfile
import unittest

try:  # the git-1.8.3.1 CI job tests backend/core/graph only — no Textual there
    import gitkit.ui.app as appmod
    from gitkit.ui.app import (GitkitApp, DiffFileItem, _PageNavItem,
                               FileSearchModal)
    from textual.widgets import ListView
except ImportError as e:
    raise unittest.SkipTest(f"Textual not installed (UI tests skipped): {e}")


def _git(d, *a):
    subprocess.run(["git", *a], cwd=d, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


async def _settle(app, pilot, n=60):
    from gitkit.ui.app import ProgressModal
    for _ in range(n):
        await pilot.pause(0.03)
        if (app._cmd_queue is not None and app._cmd_queue.empty()
                and app._cmd_task is None
                and not isinstance(app.screen, ProgressModal)):
            return
    raise AssertionError("never settled")


class InfoPagingCase(unittest.IsolatedAsyncioTestCase):
    N = 60       # files in the commit
    PAGE = 20    # small page so the test is quick

    def setUp(self):
        self._orig = appmod.INFO_FILE_PAGE
        appmod.INFO_FILE_PAGE = self.PAGE
        self.d = tempfile.mkdtemp(prefix="gitkit_info_")
        subprocess.run(["git", "init", "-q", self.d], check=True)
        _git(self.d, "config", "user.email", "t@e.co")
        _git(self.d, "config", "user.name", "T")
        for i in range(self.N):
            with open(os.path.join(self.d, f"f{i:03d}.txt"), "w") as f:
                f.write("x\n")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "many files")

    def tearDown(self):
        appmod.INFO_FILE_PAGE = self._orig
        shutil.rmtree(self.d, ignore_errors=True)

    def _counts(self, app):
        lv = app.query_one("#difflist", ListView)
        files = sum(1 for c in lv.children if isinstance(c, DiffFileItem))
        prev = sum(1 for c in lv.children
                   if isinstance(c, _PageNavItem) and c.delta < 0)
        nxt = sum(1 for c in lv.children
                  if isinstance(c, _PageNavItem) and c.delta > 0)
        return files, prev, nxt

    async def _wait_files(self, app, pilot, n=60):
        for _ in range(n):
            await pilot.pause(0.04)
            if self._counts(app)[0] > 0:
                return

    async def test_fixed_window_pagination(self):
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            await self._wait_files(app, pilot)
            self.assertEqual(self._counts(app), (self.PAGE, 0, 1))  # page1: next only

            app._page_files(+1)
            for _ in range(15):
                await pilot.pause(0.03)
            # KEY: still a FIXED window (20), not accumulated to 40
            self.assertEqual(self._counts(app), (self.PAGE, 1, 1))  # prev + next
            # cursor lands on the FIRST file of the new page (not the nav row)
            lv = app.query_one("#difflist", ListView)
            self.assertIsInstance(lv.highlighted_child, DiffFileItem)
            self.assertEqual(lv.highlighted_child.path, "f020.txt")  # page1 first file

            app._page_files(+1)
            for _ in range(15):
                await pilot.pause(0.03)
            self.assertEqual(self._counts(app), (self.PAGE, 1, 0))  # last page: prev only
            self.assertEqual(app._info_page, 2)

    async def test_search_opens_and_jumps_to_file(self):
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            await self._wait_files(app, pilot)

            app.action_search_files()                 # "/"
            await pilot.pause(0.1)
            self.assertIsInstance(app.screen, FileSearchModal)
            await pilot.press("escape")
            await pilot.pause(0.1)

            target = "f045.txt"                        # index 45 → page 45//20 = 2
            app._after_file_search(target)
            for _ in range(30):
                await pilot.pause(0.03)
            self.assertEqual(app._info_page, 45 // self.PAGE)
            lv = app.query_one("#difflist", ListView)
            hl = lv.highlighted_child
            self.assertIsInstance(hl, DiffFileItem)
            self.assertEqual(hl.path, target)


if __name__ == "__main__":
    unittest.main()
