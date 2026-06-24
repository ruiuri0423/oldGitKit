"""Info panel: the file-list is paged so a commit touching many files renders a
page at a time (and stays responsive) instead of building every row at once."""
import os
import shutil
import subprocess
import tempfile
import unittest

try:  # the git-1.8.3.1 CI job tests backend/core/graph only — no Textual there
    import gitkit.ui.app as appmod
    from gitkit.ui.app import GitkitApp, DiffFileItem, _MoreFilesItem
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


class InfoFilePagingCase(unittest.IsolatedAsyncioTestCase):
    N = 60       # files in the commit
    PAGE = 20    # small page so the test is quick

    def setUp(self):
        self._orig_page = appmod.INFO_FILE_PAGE
        appmod.INFO_FILE_PAGE = self.PAGE
        self.d = tempfile.mkdtemp(prefix="gitkit_info_")
        subprocess.run(["git", "init", "-q", self.d], check=True)
        _git(self.d, "config", "user.email", "t@e.co")
        _git(self.d, "config", "user.name", "T")
        for i in range(self.N):  # one commit touching N files
            with open(os.path.join(self.d, f"f{i:03d}.txt"), "w") as f:
                f.write("x\n")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "many files")

    def tearDown(self):
        appmod.INFO_FILE_PAGE = self._orig_page
        shutil.rmtree(self.d, ignore_errors=True)

    def _counts(self, app):
        lv = app.query_one("#difflist", ListView)
        files = sum(1 for c in lv.children if isinstance(c, DiffFileItem))
        more = sum(1 for c in lv.children if isinstance(c, _MoreFilesItem))
        return files, more

    async def _wait_files(self, app, pilot, n=60):
        for _ in range(n):
            await pilot.pause(0.04)
            if self._counts(app)[0] > 0:
                return

    async def test_large_commit_pages_and_loads_more(self):
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            await self._wait_files(app, pilot)   # HEAD commit auto-selected
            self.assertEqual(self._counts(app), (self.PAGE, 1))  # one page + sentinel

            app._load_more_files()
            for _ in range(20):
                await pilot.pause(0.03)
            self.assertEqual(self._counts(app), (2 * self.PAGE, 1))

            app._load_more_files()               # last page → sentinel gone
            for _ in range(20):
                await pilot.pause(0.03)
            self.assertEqual(self._counts(app), (self.N, 0))


if __name__ == "__main__":
    unittest.main()
