"""v3 branch-tree layout tests on synthetic DAGs (deterministic, no real repo)."""
import unittest

from gitkit.core.models import Commit
from gitkit.graph.lanes import (
    G,
    GraphCache,
    build_layout,
    _conn_string,
    _node_string,
    render_graph,
)


def _lanes(*cols, dashed=False):
    return frozenset((c, dashed) for c in cols)


def commit(sha, parents):
    return Commit(sha=sha, short_sha=sha[:7], parents=list(parents),
                  refs=[], author="t", date="2026-06-13", subject=sha)


# M is a merge: first parent A (trunk), second parent B (a side branch).
DIAMOND = [
    commit("M", ["A", "B"]),
    commit("A", ["base"]),
    commit("B", ["base"]),
    commit("base", []),
]


class TestDecompose(unittest.TestCase):
    def test_first_parent_defines_branch(self):
        branches, branch_of, _ = build_layout(DIAMOND, head_sha="M")
        # M and A share the trunk (A is M's FIRST parent); B is its own branch
        self.assertEqual(branch_of["M"], branch_of["A"])
        self.assertNotEqual(branch_of["M"], branch_of["B"])
        # base joins the trunk (leftmost / most trunk-ward)
        self.assertEqual(branch_of["base"], branch_of["M"])

    def test_merge_target_placement(self):
        branches, branch_of, _ = build_layout(DIAMOND, head_sha="M")
        trunk = branches[branch_of["M"]]
        side = branches[branch_of["B"]]
        self.assertEqual(trunk.column, 0)            # primary trunk = col0
        self.assertEqual(side.column, trunk.column + 1)  # merged branch sits next to it
        self.assertTrue(side.merged)
        self.assertEqual(side.merge_commit, "M")

    def test_trunk_is_root_chain_regardless_of_head(self):
        # col0 = the chain containing the root (base), no matter what HEAD is →
        # lanes stay put when switching branches (first-publish stable layout)
        for head in (None, "M", "B"):
            branches, branch_of, _ = build_layout(DIAMOND, head_sha=head)
            self.assertEqual(branches[branch_of["base"]].column, 0)
            self.assertEqual(branches[branch_of["M"]].column, 0)   # M,A,base trunk
            self.assertEqual(branches[branch_of["B"]].column, 1)   # side branch


class TestStrings(unittest.TestCase):
    def test_node_string(self):
        # two lanes active, node on col0 (all on-remote → solid)
        self.assertEqual(_node_string(_lanes(0, 1), 0, False, True, 2, G), "● │")
        self.assertEqual(_node_string(_lanes(0, 1), 1, True, True, 2, G), "│ ◆")

    def test_node_string_local_only(self):
        # node not on remote → hollow; dashed lane → ╎
        self.assertEqual(_node_string(_lanes(0, dashed=True), 0, False, False, 1, G), "○")
        self.assertEqual(
            _node_string(_lanes(0, 1, dashed=True), 0, False, True, 2, G), "● ╎")

    def test_conn_adjacent_spawn(self):
        self.assertEqual(_conn_string(_lanes(0), frozenset({(0, 1)}), 2, G), "├─╮")

    def test_conn_cross_column_uses_straight_line(self):
        # a cross-column edge passes OVER the lane it crosses as ─ (not ┼), so it
        # reads as one continuous horizontal line
        s = _conn_string(_lanes(0, 1), frozenset({(0, 2)}), 3, G)
        self.assertEqual(s, "├───╮")

    def test_conn_double_converge(self):
        s = _conn_string(_lanes(0), frozenset({(1, 0), (2, 0)}), 3, G)
        self.assertEqual(s, "├─┴─╯")


class TestRenderAndCache(unittest.TestCase):
    def test_render_has_one_node_per_commit(self):
        lines = render_graph(DIAMOND, head_sha="M")
        nodes = [c for _, c in lines if c is not None]
        self.assertEqual([c.sha for c in nodes], ["M", "A", "B", "base"])

    def test_cache_reuses_and_matches(self):
        cache = GraphCache()
        first = cache.render(DIAMOND, "M")
        again = cache.render(DIAMOND, "M")
        self.assertIs(first, again)  # identical signature -> same object (full hit)
        # per-row strings match a fresh render
        fresh = render_graph(DIAMOND, head_sha="M")
        self.assertEqual([g for g, _ in first], [g for g, _ in fresh])

    def test_cache_rerenders_on_change(self):
        cache = GraphCache()
        cache.render(DIAMOND, "M")
        extended = [commit("N", ["M"])] + DIAMOND  # a new top commit
        out = cache.render(extended, "N")
        self.assertEqual([c.sha for _, c in out if c is not None],
                         ["N", "M", "A", "B", "base"])


if __name__ == "__main__":
    unittest.main()
