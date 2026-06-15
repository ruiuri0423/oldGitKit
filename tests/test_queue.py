"""Command-queue tests for the TUI: how git operations are classified and run.

The app funnels every write through ONE serial queue. Each op is classified into
(modal?, cancellable?):

  * network (fetch/pull/push)            → modal + cancellable (Esc kills git)
  * tree-modifying local (commit/merge…) → modal, NOT cancellable ("執行中")
  * staging (stage/unstage/discard)      → no modal, runs in the background queue

These tests pin that classification down and verify the queue actually serialises
work (one command finishes before the next starts) and that only modal commands
raise a ProgressModal while background staging does not.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

try:  # the git-1.8.3.1 CI job tests backend/core/graph only — no Textual there
    from gitkit.ui.app import GitkitApp, ProgressModal
except ImportError as e:
    raise unittest.SkipTest(f"Textual not installed (UI tests skipped): {e}")


def _git(d, *a):
    subprocess.run(["git", *a], cwd=d, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _init_repo():
    d = tempfile.mkdtemp(prefix="gitkit_q_")
    subprocess.run(["git", "init", "-q", d], check=True)
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "Tester")
    with open(os.path.join(d, "a.txt"), "w") as f:
        f.write("hello\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "init")
    return d


class ClassifyCase(unittest.TestCase):
    """Pure classification — modal command vs background staging."""

    def setUp(self):
        self.d = _init_repo()
        self.app = GitkitApp(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_staging_runs_in_background(self):
        for name in ("stage", "unstage", "discard"):
            fn = getattr(self.app.flow, name)
            self.assertEqual(self.app._classify(fn),
                             {"modal": False, "cancellable": False},
                             f"{name} should be backgroundable, no modal")

    def test_tree_modifying_local_is_modal_not_cancellable(self):
        for name in ("commit", "checkout", "revert", "merge_into",
                     "integrate", "create_branch"):
            fn = getattr(self.app.flow, name)
            self.assertEqual(self.app._classify(fn),
                             {"modal": True, "cancellable": False},
                             f"{name} should be a non-cancellable modal op")

    def test_network_ops_are_modal_and_cancellable(self):
        for name in ("fetch", "pull", "push", "update_then_merge",
                     "update_then_push"):
            fn = getattr(self.app.flow, name)
            self.assertEqual(self.app._classify(fn),
                             {"modal": True, "cancellable": True},
                             f"{name} should be a cancellable modal op")


async def _settle(app, pilot, n=80):
    """Pump until the queue is idle and the main screen is back on top."""
    for _ in range(n):
        await pilot.pause(0.02)
        if (app._cmd_queue is not None and app._cmd_queue.empty()
                and app._cmd_task is None
                and not isinstance(app.screen, ProgressModal)):
            return
    raise AssertionError("queue never settled")


class QueueCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.d = _init_repo()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    async def test_commands_run_serially(self):
        import asyncio
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)  # let the initial repo load finish
            events = []

            def make(tag):
                async def coro():
                    events.append(("start", tag))
                    await asyncio.sleep(0.04)
                    events.append(("end", tag))
                    return f"{tag} ok"
                return coro

            for tag in ("a", "b", "c"):
                app._enqueue(make(tag), tag, modal=False, cancellable=False)
            await _settle(app, pilot)

        # never interleaved: each start is immediately followed by its own end
        self.assertEqual(
            events,
            [("start", "a"), ("end", "a"),
             ("start", "b"), ("end", "b"),
             ("start", "c"), ("end", "c")])

    async def test_modal_op_shows_progress_background_op_does_not(self):
        import asyncio
        app = GitkitApp(self.d)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(app, pilot)
            gate = asyncio.Event()

            async def held():
                await gate.wait()
                return "done"

            # background staging-style op: no ProgressModal
            app._enqueue(held, "stage x", modal=False, cancellable=False)
            for _ in range(6):
                await pilot.pause(0.02)
            self.assertNotIsInstance(app.screen, ProgressModal,
                                     "background op must not raise a modal")
            self.assertIn("執行中", app._status_msg)
            gate.set()
            await _settle(app, pilot)

            # modal tree-modifying op: ProgressModal is shown while it runs
            gate2 = asyncio.Event()

            async def held2():
                await gate2.wait()
                return "done"

            app._enqueue(held2, "commit", modal=True, cancellable=False)
            shown = False
            for _ in range(20):
                await pilot.pause(0.02)
                if isinstance(app.screen, ProgressModal):
                    shown = True
                    break
            self.assertTrue(shown, "modal op must raise a ProgressModal")
            gate2.set()
            await _settle(app, pilot)


if __name__ == "__main__":
    unittest.main()
