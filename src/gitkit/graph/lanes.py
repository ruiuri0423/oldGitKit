"""DAG lane layout for the Tree panel (version-agnostic, pure) — TEMP v3.

New model (replaces the old greedy renderer):
  1. Decompose the DAG into BRANCHES = maximal first-parent chains (git's
     definition of "trunk"): a commit continues a child's branch iff it is that
     child's FIRST parent, else it starts a new branch.
  2. Assign each branch a stable COLUMN. A merged branch is placed next to the
     branch it merges INTO (merge target) so the merge edge stays short; an
     unmerged branch is placed next to the branch it forks from. Overlapping
     branch intervals get different columns (interval scheduling).
  3. Render row by row. Lanes never shift mid-flight (columns are fixed), so the
     only diagonals are spawn (at a merge) and converge (at a fork point).

Connectors are single-row. Each edge is coloured as ONE continuous line (its
source lane's colour, including the ┼ crossings) so it stays followable across
lanes — see _conn_string(colors=) and ui._append_graph. A multi-row *staircase*
for far cross-column edges is intentionally deferred: it would route an edge
through shifting columns, which conflicts with the fixed-lane premise; whole-edge
colouring already covers the readability it aimed at. See docs/ui/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from gitkit.core.models import Commit

NODE = "●"
MERGE = "◆"
VLINE = "│"

# connection-mask box-drawing: each connector cell records which of up/down/
# left/right it links to, then maps to a continuous line glyph (no \ / breaks).
_U, _D, _L, _R = 1, 2, 4, 8
_GLYPH = {
    0: " ", _U | _D: "│", _L | _R: "─",
    _D | _R: "╭", _D | _L: "╮", _U | _R: "╰", _U | _L: "╯",
    _U | _D | _R: "├", _U | _D | _L: "┤", _D | _L | _R: "┬",
    _U | _L | _R: "┴", _U | _D | _L | _R: "┼",
    _U: "│", _D: "│", _L: "─", _R: "─",  # lone stubs fall back to a line
}


@dataclass
class Branch:
    id: int
    commits: List[str] = field(default_factory=list)  # shas, top (new) -> bottom (old)
    column: int = 0
    merged: bool = False
    merge_commit: Optional[str] = None   # the merge that brought this branch in (top)
    fork_commit: Optional[str] = None    # where this branch rejoins history (bottom)
    fork_branch: Optional[int] = None
    basis_branch: Optional[int] = None   # branch this column is placed relative to
    top_row: int = 0
    bottom_row: int = 0


# ── structure ────────────────────────────────────────────────────────
def _children(commits: List[Commit]) -> Dict[str, List[str]]:
    ch: Dict[str, List[str]] = {c.sha: [] for c in commits}
    for c in commits:
        for p in c.parents:
            if p in ch:
                ch[p].append(c.sha)
    return ch


_TRUNK_REFS = ("main", "master")


def _find_main_tip(commits: List[Commit]) -> Optional[str]:
    """The local main/master tip, used as the trunk that owns col0."""
    for c in commits:
        for r in c.refs:
            if r in _TRUNK_REFS:
                return c.sha
    return None


def _decompose(commits: List[Commit], primary_tip: Optional[str] = None):
    """Assign every commit to a branch (first-parent chain).

    Commits on the trunk's first-parent chain (from `primary_tip` down to the
    root) are kept as one branch, so at a plain fork the trunk isn't stolen by a
    side branch whose tip merely happens to be processed first.
    """
    index = {c.sha: c for c in commits}
    children = _children(commits)

    on_main = set()
    cur = primary_tip if (primary_tip and primary_tip in index) else None
    while cur:
        on_main.add(cur)
        ps = index[cur].parents
        cur = ps[0] if (ps and ps[0] in index) else None

    branch_of: Dict[str, int] = {}
    branches: Dict[int, Branch] = {}
    nid = 0
    for c in commits:  # newest first
        sha = c.sha
        # children that list this commit as their FIRST parent continue its branch
        cont = [ch for ch in children[sha]
                if index[ch].parents and index[ch].parents[0] == sha]
        if cont:
            main_cont = [ch for ch in cont if sha in on_main and ch in on_main]
            if main_cont:  # keep the trunk's chain together through forks
                bid = branch_of[main_cont[0]]
            else:
                bid = min(branch_of[ch] for ch in cont)  # stay trunk-ward
        else:
            bid = nid
            nid += 1
            branches[bid] = Branch(id=bid)
        branch_of[sha] = bid
        branches[bid].commits.append(sha)
    return index, branch_of, branches


def _annotate(commits, index, branch_of, branches):
    row_of = {c.sha: i for i, c in enumerate(commits)}
    # who brings each commit in as a 2nd+ (merge) parent
    merge_of: Dict[str, str] = {}
    for c in commits:
        for p in c.parents[1:]:
            merge_of.setdefault(p, c.sha)

    for b in branches.values():
        head, tail = b.commits[0], b.commits[-1]
        if head in merge_of:
            b.merged = True
            b.merge_commit = merge_of[head]
        tailc = index[tail]
        if tailc.parents and tailc.parents[0] in branch_of:
            b.fork_commit = tailc.parents[0]
            b.fork_branch = branch_of[b.fork_commit]

        if b.merged:
            b.basis_branch = branch_of[b.merge_commit]
            b.top_row = row_of[b.merge_commit] + 1
        else:
            b.basis_branch = b.fork_branch
            b.top_row = row_of[head]

        if b.fork_commit is not None:
            b.bottom_row = row_of[b.fork_commit] - 1
        else:
            b.bottom_row = row_of[tail]
        # guard against inverted spans on odd inputs
        b.bottom_row = max(b.bottom_row, b.top_row)
    return row_of, merge_of


def _assign_columns(branches: Dict[int, Branch], primary: int):
    occ: List[List[Tuple[int, int]]] = []

    def free(col, top, bottom):
        for (t, b) in occ[col]:
            if not (bottom < t or top > b):
                return False
        return True

    def place(b: Branch, mincol: int):
        col = max(0, mincol)
        while True:
            if col >= len(occ):
                occ.append([])
            if free(col, b.top_row, b.bottom_row):
                occ[col].append((b.top_row, b.bottom_row))
                b.column = col
                return

            col += 1

    place(branches[primary], 0)
    assigned = {primary}
    remaining = [bid for bid in branches if bid != primary]
    progress = True
    while remaining and progress:
        progress = False
        still = []
        for bid in remaining:
            b = branches[bid]
            if b.basis_branch is None:
                place(b, 0)
                assigned.add(bid)
                progress = True
            elif b.basis_branch in assigned:
                place(b, branches[b.basis_branch].column + 1)
                assigned.add(bid)
                progress = True
            else:
                still.append(bid)
        remaining = still
    for bid in remaining:  # leftovers (cycles / odd data)
        place(branches[bid], 0)


def build_layout(commits: List[Commit], head_sha: Optional[str] = None):
    """Return (branches, branch_of, row_of). Columns assigned.

    col0 is the trunk that contains the (oldest) root commit — NOT HEAD's branch.
    Because the layout no longer depends on `head_sha`, lanes stay put when you
    switch branches (first-publish stable layout). `head_sha` is kept only so the
    caller can mark the HEAD row.
    """
    if not commits:
        return {}, {}, {}
    main_tip = _find_main_tip(commits)
    index, branch_of, branches = _decompose(commits, primary_tip=main_tip)
    row_of, _ = _annotate(commits, index, branch_of, branches)
    if main_tip is not None:
        primary = branch_of[main_tip]
    else:  # no main/master ref: fall back to the chain holding the oldest root
        roots = [c.sha for c in commits if not c.parents]
        primary = branch_of[roots[-1] if roots else commits[-1].sha]
    _assign_columns(branches, primary)
    return branches, branch_of, row_of


# ── render ───────────────────────────────────────────────────────────
# Solid = on a remote (already pushed); dashed/hollow = local-only (not pushed).
NODE_HOLLOW = "○"
MERGE_HOLLOW = "◇"
DVLINE = "╎"
G = {"node": NODE, "merge": MERGE, "vline": VLINE,
     "hnode": NODE_HOLLOW, "hmerge": MERGE_HOLLOW, "dvline": DVLINE}


def _node_string(lanes, col, is_merge, node_remote, width, g, *, colors=False):
    cells = [" "] * (2 * width - 1)
    color = [-1] * (2 * width - 1)  # per-cell colour = owning lane's column
    for x, dashed in lanes:
        cells[2 * x] = g["dvline"] if dashed else g["vline"]
        color[2 * x] = x
    if is_merge:
        cells[2 * col] = g["merge"] if node_remote else g["hmerge"]
    else:
        cells[2 * col] = g["node"] if node_remote else g["hnode"]
    color[2 * col] = col
    s = "".join(cells).rstrip()
    return (s, color) if colors else s


def _conn_string(both, moves, width, g, *, colors=False):
    mask = [0] * (2 * width - 1)
    color = [-1] * (2 * width - 1)
    dashed_at = set()
    for cc, dashed in both:
        mask[2 * cc] |= _U | _D  # lane passing straight through
        color[2 * cc] = cc       # straight lane → its own column's colour
        if dashed:
            dashed_at.add(2 * cc)
    for f, t in moves:
        lo, hi = (f, t) if f < t else (t, f)
        mask[2 * lo] |= _R
        mask[2 * hi] |= _L
        for i in range(2 * lo + 1, 2 * hi):
            mask[i] |= _L | _R  # horizontal run (crosses lanes as ┼)
        mask[2 * f] |= _U  # from-side links up (to the node/branch above)
        mask[2 * t] |= _D  # to-side links down (into the lane below)
        for i in range(2 * lo, 2 * hi + 1):  # whole edge → ONE colour (its source
            color[i] = f                      # lane f), so it reads as a single line
    out = []
    for i, m in enumerate(mask):
        gl = _GLYPH.get(m, " ")
        if gl == VLINE and i in dashed_at:  # only pure straight lanes go dashed
            gl = g["dvline"]
        out.append(gl)
    s = "".join(out).rstrip()
    return (s, color) if colors else s


def _specs(commits: List[Commit], head_sha: Optional[str], remote_set):
    """Per-output-line specs: ('n'|'c', hashable_key, commit|None). The key fully
    determines the rendered string, so it doubles as a cache key."""
    branches, branch_of, row_of = build_layout(commits, head_sha)
    if not commits:
        return [], 1
    width = max(b.column for b in branches.values()) + 1
    n = len(commits)

    # rows above a branch's first on-remote commit are local-only (dashed)
    boundary = {}
    for bid, b in branches.items():
        on_rem = [row_of[s] for s in b.commits if s in remote_set]
        boundary[bid] = min(on_rem) if on_rem else 10 ** 9

    def lanes_at(r):
        return {b.column: (r < boundary[b.id])
                for b in branches.values() if b.top_row <= r <= b.bottom_row}

    specs = []
    for r in range(n):
        c = commits[r]
        col = branches[branch_of[c.sha]].column
        node_remote = c.sha in remote_set
        lanes = frozenset(lanes_at(r).items())
        specs.append(("n", (lanes, col, c.is_merge, node_remote, width), c))
        if r + 1 >= n:
            continue
        moves = []
        for p in c.parents[1:]:  # merge edges (head spawn or mid-branch)
            if p in branch_of:
                tcol = branches[branch_of[p]].column
                if tcol != col:
                    moves.append((col, tcol))
        for b in branches.values():  # converge at fork points
            if b.fork_branch is not None and b.bottom_row == r:
                fcol = branches[b.fork_branch].column
                if b.column != fcol:
                    moves.append((b.column, fcol))
        if not moves:
            continue
        d_r = lanes_at(r)
        both_cols = set(d_r) & set(lanes_at(r + 1))
        both = frozenset((cc, d_r[cc]) for cc in both_cols)
        specs.append(("c", (both, frozenset(moves), width), None))
    return specs, width


def render_graph(commits: List[Commit], *, head_sha: Optional[str] = None,
                 remote_set=None, node: str = NODE, merge: str = MERGE,
                 vline: str = VLINE, cache: Optional[dict] = None
                 ) -> List[Tuple[str, Optional[Commit]]]:
    """Render the branch-tree graph. Commits reachable from a remote (`remote_set`)
    draw solid (● │); local-only commits draw hollow/dashed (○ ╎). `cache` memoises
    per-row strings by structural key."""
    if remote_set is None:
        remote_set = {c.sha for c in commits}  # everything solid
    g = dict(G, node=node, merge=merge, vline=vline)
    specs, _ = _specs(commits, head_sha, remote_set)
    out = []
    for kind, key, c in specs:
        ck = (kind, key)
        if cache is not None and ck in cache:
            s, color = cache[ck]
        else:
            if kind == "n":
                lanes, col, ismerge, nrem, w = key
                s, color = _node_string(lanes, col, ismerge, nrem, w, g, colors=True)
            else:
                both, m, w = key
                s, color = _conn_string(both, m, w, g, colors=True)
            if cache is not None:
                cache[ck] = (s, color)
        out.append((s, color, c))
    return out


class GraphCache:
    """Holds the rendered graph across reloads. A full re-render is skipped when
    the (head, remote-set, commit-parents) signature is unchanged; otherwise rows
    are rebuilt but unchanged rows are reused from the per-row cache."""

    def __init__(self):
        self._sig = None
        self._lines: List[Tuple[str, Optional[Commit]]] = []
        self._rows: dict = {}

    def render(self, commits: List[Commit], head_sha: Optional[str] = None,
               remote_set=None):
        sig = (head_sha, frozenset(remote_set or ()),
               tuple((c.sha, tuple(c.parents)) for c in commits))
        if sig == self._sig:
            return self._lines
        self._lines = render_graph(commits, head_sha=head_sha,
                                   remote_set=remote_set, cache=self._rows)
        self._sig = sig
        return self._lines
