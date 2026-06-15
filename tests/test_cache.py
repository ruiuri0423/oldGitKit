"""Commit-read cache: shared LRU across the sync and async readers.

Pins the behaviour we converged on in review:
  * only immutable 40-hex shas are cached ('HEAD'/short refs bypass),
  * a second read of the same sha is served from cache (no git),
  * over capacity the LEAST-RECENTLY-USED entry is evicted (not FIFO) — a hot
    commit, read again, survives,
  * sync and async share one map/keys, so either populates a hit for the other,
  * an async read also refreshes LRU position (the hot path protects itself).
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest

from gitkit.backend.cli_git import CliGitBackend


def _git(d, *a):
    subprocess.run(["git", *a], cwd=d, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class CacheCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gitkit_cache_")
        subprocess.run(["git", "init", "-q", self.d], check=True)
        _git(self.d, "config", "user.email", "t@e.co")
        _git(self.d, "config", "user.name", "T")
        for i in range(5):
            with open(os.path.join(self.d, "f.txt"), "w") as f:
                f.write(f"v{i}\n")
            _git(self.d, "add", "-A")
            _git(self.d, "commit", "-qm", f"c{i}")
        self.be = CliGitBackend(self.d)
        self.shas = [c.sha for c in self.be.log(limit=10, all_refs=False)]
        self.be.cmdlog.clear()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _cached_shas(self):
        return {k[1] for k in self.be._commit_cache}  # key = (kind, sha[, path])

    def test_non_sha_bypasses_cache(self):
        self.be.commit_files("HEAD")               # short/symbolic → mutable
        self.assertEqual(len(self.be._commit_cache), 0)

    def test_second_read_hits_cache(self):
        sha = self.shas[0]
        n = len(self.be.cmdlog); self.be.commit_files(sha)
        self.assertGreater(len(self.be.cmdlog), n)  # 1st: ran git
        n = len(self.be.cmdlog); self.be.commit_files(sha)
        self.assertEqual(len(self.be.cmdlog), n)     # 2nd: served from cache

    def test_lru_evicts_least_recently_used(self):
        be = self.be
        be._CACHE_MAX = 3
        be._commit_cache.clear()
        a, b, c, d = self.shas[:4]
        be.commit_files(a); be.commit_files(b); be.commit_files(c)  # [a, b, c]
        be.commit_files(a)                                          # touch a → [b, c, a]
        be.commit_files(d)                                          # +d over cap → evict b
        self.assertEqual(self._cached_shas(), {a, c, d})
        self.assertNotIn(b, self._cached_shas())                   # LRU victim, not 'a'

    def test_sync_and_async_cross_hit(self):
        be = self.be
        sha = self.shas[0]
        be.commit_files(sha)                         # sync fills ("files", sha)
        n = len(be.cmdlog)
        asyncio.run(be.commit_files_async(sha))      # async reads the same key
        self.assertEqual(len(be.cmdlog), n)          # no new git command

    def test_async_read_refreshes_lru(self):
        be = self.be
        be._CACHE_MAX = 3
        be._commit_cache.clear()
        a, b, c, d = self.shas[:4]
        be.commit_files(a); be.commit_files(b); be.commit_files(c)  # [a, b, c]
        asyncio.run(be.commit_files_async(a))                      # touch a via async → [b, c, a]
        be.commit_files(d)                                         # evict b, keep a
        self.assertIn(a, self._cached_shas())
        self.assertNotIn(b, self._cached_shas())


if __name__ == "__main__":
    unittest.main()
