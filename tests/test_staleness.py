"""Unit tests for the behind/diverged classifier that drives the merge/push/pull
staleness guards. Pure logic — a stub backend supplies crafted BranchInfo."""
import unittest

from gitkit.core.flow import Flow
from gitkit.core.models import BranchInfo


class _StubBackend:
    """Only `branches()` is exercised by Flow.upstream_state."""

    def __init__(self, infos):
        self._infos = infos

    def branches(self):
        return self._infos


def _flow(*infos):
    return Flow(_StubBackend(list(infos)))


def _bi(name, upstream, ahead, behind, gone=False):
    return BranchInfo(name=name, upstream=upstream, ahead=ahead, behind=behind,
                      upstream_gone=gone)


class TestUpstreamState(unittest.TestCase):
    def test_no_upstream(self):
        st = _flow(_bi("feat", None, 0, 0)).upstream_state("feat")
        self.assertEqual(st.kind, "none")
        self.assertIsNone(st.remote)
        self.assertFalse(st.needs_attention)

    def test_upstream_gone_is_none(self):
        st = _flow(_bi("x", "origin/x", 3, 0, gone=True)).upstream_state("x")
        self.assertEqual(st.kind, "none")

    def test_current_when_level(self):
        st = _flow(_bi("main", "origin/main", 0, 0)).upstream_state("main")
        self.assertEqual(st.kind, "current")
        self.assertFalse(st.needs_attention)

    def test_ahead_only_is_ok(self):
        # local commits to push, nothing to pull → not a staleness problem
        st = _flow(_bi("main", "origin/main", 2, 0)).upstream_state("main")
        self.assertEqual(st.kind, "ahead")
        self.assertFalse(st.needs_attention)
        self.assertFalse(st.ff_updatable)

    def test_behind_is_ff_updatable(self):
        st = _flow(_bi("main", "origin/main", 0, 1)).upstream_state("main")
        self.assertEqual(st.kind, "behind")
        self.assertTrue(st.needs_attention)
        self.assertTrue(st.ff_updatable)   # strictly behind → can fast-forward
        self.assertEqual(st.remote, "origin")

    def test_diverged_needs_attention_but_not_ff(self):
        st = _flow(_bi("main", "origin/main", 2, 1)).upstream_state("main")
        self.assertEqual(st.kind, "diverged")
        self.assertTrue(st.needs_attention)
        self.assertFalse(st.ff_updatable)  # both sides moved → real merge needed

    def test_unknown_branch_is_none(self):
        st = _flow(_bi("main", "origin/main", 0, 1)).upstream_state("missing")
        self.assertEqual(st.kind, "none")

    def test_remote_parsed_from_upstream(self):
        st = _flow(_bi("dev", "upstream/dev", 0, 4)).upstream_state("dev")
        self.assertEqual(st.remote, "upstream")


if __name__ == "__main__":
    unittest.main()
