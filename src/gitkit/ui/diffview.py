"""Render a unified diff as a side-by-side (old | new) Rich Text.

Uses the `unidiff` library to parse `git diff` / `git show` output into hunks,
then lays removed lines on the left (old / repository) and added lines on the
right (new / workspace), pairing a removed line with its added counterpart on
the same row when possible. Changed lines are shown with a background band
(red = removed, green = added); a dashed divider separates the two sides.
"""
from __future__ import annotations

from rich.text import Text

try:
    from unidiff import PatchSet
except ImportError:  # pragma: no cover
    PatchSet = None

DIVIDER = " ╎ "       # dashed vertical separator between old | new
LN_W = 4              # line-number gutter width per side
REM_BG = "on #4a1e1e"  # removed line background (dark red)
ADD_BG = "on #173d22"  # added line background (dark green)


def _emit_cell(out: Text, cell, half: int) -> None:
    """cell = (lineno|None, sign, text, bg) or None for a blank side."""
    if cell is None:
        out.append(" " * half)
        return
    lineno, sign, text, bg = cell
    gutter = (f"{lineno:>{LN_W}}" if lineno is not None else " " * LN_W) + " "
    avail = max(1, half - len(gutter))
    body = (sign + text)[:avail].ljust(avail)
    out.append(gutter, style=(f"grey42 {bg}".strip()))
    out.append(body, style=(bg or None))


def _row(out: Text, left, right, half: int) -> None:
    _emit_cell(out, left, half)
    out.append(DIVIDER, style="grey37")
    _emit_cell(out, right, half)
    out.append("\n")


def render_side_by_side(diff_text: str, width: int = 120) -> Text:
    if PatchSet is None or not diff_text.strip():
        return Text(diff_text or "(no diff)")

    # `git show <sha>` prefixes a commit header; start at the first file header
    idx = diff_text.find("diff --git")
    body = diff_text[idx:] if idx != -1 else diff_text
    try:
        patch = PatchSet(body)
    except Exception:
        return Text(diff_text)
    if len(patch) == 0:
        return Text(diff_text or "(no diff)")

    half = max(20, (width - len(DIVIDER)) // 2)
    out = Text(no_wrap=True)

    for pf in patch:
        if pf.is_binary_file:
            out.append(f"╭ {pf.path}  (binary)\n", style="bold")
            continue
        out.append(f"╭ {pf.path}\n", style="bold")
        full_w = 2 * half + len(DIVIDER)
        for hunk in pf:
            # hunk header: a gray band covering the whole row
            hdr = f"  @@ -{hunk.source_start} +{hunk.target_start} @@"
            out.append(hdr[:full_w].ljust(full_w), style="grey74 on grey27")
            out.append("\n")
            rem, add = [], []  # (lineno, text)

            def flush():
                for i in range(max(len(rem), len(add))):
                    left = (rem[i][0], "-", rem[i][1], REM_BG) if i < len(rem) else None
                    right = (add[i][0], "+", add[i][1], ADD_BG) if i < len(add) else None
                    _row(out, left, right, half)
                rem.clear()
                add.clear()

            for line in hunk:
                if line.is_context:
                    flush()
                    v = line.value.rstrip("\n")
                    _row(out, (line.source_line_no, " ", v, ""),
                              (line.target_line_no, " ", v, ""), half)
                elif line.is_removed:
                    rem.append((line.source_line_no, line.value.rstrip("\n")))
                elif line.is_added:
                    add.append((line.target_line_no, line.value.rstrip("\n")))
            flush()
    return out
