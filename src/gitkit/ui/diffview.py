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


def render_side_by_side(diff_text: str, width: int = 120, max_rows: int = 2000) -> Text:
    """Render a unified diff as side-by-side Text (no_wrap → never bleeds onto the
    next line; long lines clip per side). Truncation caps the OUTPUT rows (keeping
    the diff parseable, so it stays side-by-side) rather than cutting the input."""
    if PatchSet is None or not diff_text.strip():
        return Text(diff_text or "(no diff)", no_wrap=True)

    # parse the WHOLE diff (cheap — ~50ms even for 10k lines; the caller renders
    # this off the UI thread) so we never cut mid-hunk and break parsing; the cap
    # is applied to the OUTPUT rows instead, which keeps it side-by-side.
    idx = diff_text.find("diff --git")
    body = diff_text[idx:] if idx != -1 else diff_text
    try:
        patch = PatchSet(body)
    except Exception:
        return Text(diff_text, no_wrap=True)   # unparseable → raw, but no-wrap
    if len(patch) == 0:
        return Text(diff_text or "(no diff)", no_wrap=True)

    half = max(20, (width - len(DIVIDER)) // 2)
    full_w = 2 * half + len(DIVIDER)
    out = Text(no_wrap=True)
    n = 0          # output rows emitted so far
    done = False   # hit the row cap → stop

    def emit(left, right):
        nonlocal n, done
        if n >= max_rows:
            done = True
            return
        _row(out, left, right, half)
        n += 1

    for pf in patch:
        if done:
            break
        if pf.is_binary_file:
            out.append(f"╭ {pf.path}  (binary)\n", style="bold")
            n += 1
            continue
        out.append(f"╭ {pf.path}\n", style="bold")
        n += 1
        for hunk in pf:
            if done:
                break
            hdr = f"  @@ -{hunk.source_start} +{hunk.target_start} @@"
            out.append(hdr[:full_w].ljust(full_w), style="grey74 on grey27")
            out.append("\n")
            n += 1
            rem, add = [], []  # (lineno, text)

            def flush():
                for i in range(max(len(rem), len(add))):
                    if done:
                        break
                    left = (rem[i][0], "-", rem[i][1], REM_BG) if i < len(rem) else None
                    right = (add[i][0], "+", add[i][1], ADD_BG) if i < len(add) else None
                    emit(left, right)
                rem.clear()
                add.clear()

            for line in hunk:
                if done:
                    break
                if line.is_context:
                    flush()
                    v = line.value.rstrip("\n")
                    emit((line.source_line_no, " ", v, ""),
                         (line.target_line_no, " ", v, ""))
                elif line.is_removed:
                    rem.append((line.source_line_no, line.value.rstrip("\n")))
                elif line.is_added:
                    add.append((line.target_line_no, line.value.rstrip("\n")))
            flush()
    if done:
        out.append("  … 已截斷(檔案過大,只顯示前段)\n", style="bold yellow")
    return out
