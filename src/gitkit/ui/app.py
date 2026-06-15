"""gitkit Textual dashboard (read-only + display polish, F1).

Layout follows the mockup: left = three staging panels (Untracked/Modified/
Staged), center = commit Tree (the v3 lane graph), right = Remote/Local
branches, bottom = Diff (a changed-file list that expands to a single-file diff).
Tab cycles panels; the focused panel is highlighted. `?` help, `S` settings.

Run:  python -m gitkit <repo-path>
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Header, Input, Label, ListItem, ListView, Static

from gitkit.backend.base import BackendError
from gitkit.core.flow import Flow, FlowError
from gitkit.backend.cli_git import CliGitBackend
from gitkit.graph.lanes import GraphCache
from gitkit.ui.diffview import render_side_by_side

SHA_STYLE = "yellow"
REF_STYLE = "green"
MERGE_STYLE = "magenta"
USER_STYLE = "blue"
HEAD_STYLE = "bold black on bright_yellow"
LANE_COLORS = ["bright_cyan", "bright_magenta", "bright_green", "bright_yellow",
               "bright_blue", "bright_red", "cyan", "magenta"]
# git subcommands worth flashing in the header after a write action
WRITE_SUBCMDS = {"add", "reset", "checkout", "commit", "branch", "merge",
                 "pull", "push", "fetch", "stash", "archive", "rm", "revert"}

# Info-panel timing: only fetch a row's details after the cursor dwells this long,
# so fast scrolling doesn't fire/abort a subprocess per row.
INFO_DEBOUNCE = 0.25  # seconds the cursor must rest before fetching Info


def _fmt_cmd(args) -> str:
    parts = ["git"]
    for a in args:
        parts.append(f'"{a}"' if (" " in a or a == "") else a)
    return " ".join(parts)


class _Cmd:
    """A queued git operation + how to present it. A single consumer runs these
    serially, so e.g. rapid stage/unstage just pile up and process in order.
      modal=True       → blocking ProgressModal (can't background)
      cancellable=True → Esc aborts (kills the git tree); only the network ops
    """
    def __init__(self, coro, label, *, modal, cancellable, after=None):
        self.coro = coro          # () -> awaitable[str]; returns the status msg
        self.label = label
        self.modal = modal
        self.cancellable = cancellable
        self.after = after        # optional callable(msg) after success

HELP_TEXT = """[b]gitkit — keys[/b]

  [b]navigate[/b]
  ↑/↓ , j/k    move within a panel
  Tab          next panel (incl. the Info file-list)
  Enter        show a file's diff (in the Info file-list)
  Esc          jump back to the Tree

  [b]stage / commit[/b]  (focus a file panel)
  Space        stage (Untracked/Modified) · unstage (Staged)
  d            discard a Modified file (≈ svn revert, confirms)
  c            commit the staged files (asks for a message)

  [b]branch / remote[/b]
  l            branches popup — pick a Local/Remote branch to checkout
  b            new branch (creates and switches to it)
  m            merge the current branch into a chosen branch
  f / p / P    fetch / pull --ff-only / push
               (merge/push/pull warn first if the branch is behind /
                diverged from its upstream — fetch & update before)
  v            revert the selected commit (safe: inverse commit, no rewrite)
  x            resolve merge/revert conflicts (ours / theirs / manual, then commit)

  [b]other[/b]
  o            command log (recent git commands + results)
  r reload · ? help · s settings · q quit"""


def _refill_listview(screen, lv: "ListView", items, first) -> None:
    """Rebuild a ListView's items, AWAITING clear() before appending + setting the
    index. ListView.clear() defers child removal, so a synchronous `lv.index = …`
    lands against the stale (old) rows — the filter highlight then jumps to the
    wrong option. Doing it in a worker after the clear fixes that."""
    async def rebuild():
        await lv.clear()
        for it in items:
            lv.append(it)
        if first is not None and 0 <= first < len(items):
            lv.index = first

    screen.run_worker(rebuild(), exclusive=True, group=f"opts-{id(lv)}")


def _append_graph(text: Text, graph: str) -> None:
    """Append a graph string, colouring each cell by its lane column."""
    for i, ch in enumerate(graph):
        if ch == " ":
            text.append(" ")
        else:
            text.append(ch, style=LANE_COLORS[(i // 2) % len(LANE_COLORS)])


def _render_conflict(text: str, *, limit: int = 500) -> Text:
    """Colour a conflicted file: markers as bands, our side green, their side
    blue, so the <<<<<<< / ======= / >>>>>>> structure reads at a glance."""
    t = Text()
    lines = text.splitlines()
    clipped = len(lines) > limit
    state = "normal"  # normal | ours | theirs
    for line in lines[:limit]:
        if line.startswith("<<<<<<<"):
            t.append("◤ 我方 (ours) " + line[7:] + "\n", style="bold black on #d7af00")
            state = "ours"
        elif line.startswith("======="):
            t.append("─ 分隔 ─\n", style="bold black on #808080")
            state = "theirs"
        elif line.startswith(">>>>>>>"):
            t.append("◥ 對方 (theirs)" + line[7:] + "\n", style="bold black on #0087af")
            state = "normal"
        elif state == "ours":
            t.append(line + "\n", style="on #173d22")
        elif state == "theirs":
            t.append(line + "\n", style="on #0b2a4a")
        else:
            t.append(line + "\n")
    if clipped:
        t.append(f"… (+{len(lines) - limit} 行,已截斷)\n", style="dim")
    return t


class CommitItem(ListItem):
    """A Tree row that remembers which commit it stands for."""

    def __init__(self, commit, renderable: Text):
        super().__init__(Static(renderable))
        self.commit = commit
        self.sha = commit.sha


class VirtualItem(ListItem):
    """The synthetic '◌ N staged' row at the top of the Tree."""

    def __init__(self, renderable: Text):
        super().__init__(Static(renderable))


class FileItem(ListItem):
    """A row in one of the left staging panels. When highlighted it shows the
    applicable action keys as inline keycap chips."""

    CHIPS = {
        "untracked": (("␣", "stage"),),
        "modified": (("␣", "stage"), ("d", "discard")),
        "staged": (("␣", "unstage"), ("c", "commit")),
    }
    CHIP = "bold black on grey70"

    def __init__(self, path: str, kind: str):
        self.path = path
        self.kind = kind  # untracked | modified | staged
        self._label = Label(Text(path, no_wrap=True, overflow="ellipsis"))
        super().__init__(self._label)

    def set_chips(self, on: bool) -> None:
        t = Text(self.path, no_wrap=True, overflow="ellipsis")
        if on:
            t.append("   ")
            for key, _label in self.CHIPS.get(self.kind, ()):
                t.append(f" {key} ", style=self.CHIP)
                t.append(" ")
        self._label.update(t)


class _SectionItem(ListItem):
    """A non-selectable 'Local' / 'Remote' header inside the Branches popup."""

    def __init__(self, title: str):
        super().__init__(Label(Text(title, style="bold")))
        self.disabled = True


class _BranchOpt(ListItem):
    """A selectable branch row inside the Branches popup. Carries its kind so the
    app knows whether to checkout the name directly (local) or strip the remote
    prefix into a tracking branch (remote)."""

    def __init__(self, kind: str, name: str, display: str):
        super().__init__(Label(Text(display, no_wrap=True, overflow="ellipsis")))
        self.kind = kind   # local | remote
        self.branch = name


class DiffFileItem(ListItem):
    """A row in the Diff file-list. Expands to a single-file diff on Enter."""

    def __init__(self, renderable: Text, *, sha: Optional[str], path: str, staged: bool):
        super().__init__(Label(renderable))
        self.sha = sha          # commit sha, or None for a working-tree file
        self.path = path
        self.staged = staged


def _commit_items(lines, width) -> List[CommitItem]:
    items: List[CommitItem] = []
    cur: Optional[Text] = None
    cur_commit = None

    def flush():
        if cur is not None and cur_commit is not None:
            items.append(CommitItem(cur_commit, cur))

    for graph, c in lines:
        if c is not None:
            flush()
            # Tree rows carry only SHA + branch names; subject lives in Info
            # row: graph  sha  [HEAD] [b1] [b2]  user  subject
            cur = Text(no_wrap=True, overflow="ellipsis")
            _append_graph(cur, graph.ljust(width))
            cur.append("  ")
            cur.append(c.short_sha, style=MERGE_STYLE if c.is_merge else SHA_STYLE)
            if "HEAD" in c.refs:
                cur.append(" ")
                cur.append(" HEAD ", style=HEAD_STYLE)
            for r in c.refs:
                if r != "HEAD":
                    cur.append(" ")
                    cur.append(r, style=REF_STYLE)
            cur.append("  ")
            cur.append(c.author, style=USER_STYLE)
            cur.append("  ")
            cur.append(c.subject)
            cur_commit = c
        else:
            if cur is not None:
                cur.append("\n")
                _append_graph(cur, graph)
    flush()
    return items


class HelpScreen(ModalScreen):
    BINDINGS = [("escape,question_mark", "close", "close")]

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="modalbox")

    def action_close(self) -> None:
        self.dismiss()


class SettingsScreen(ModalScreen):
    BINDINGS = [("escape,s", "close", "close"), ("a", "toggle_all", "toggle all-refs")]

    def compose(self) -> ComposeResult:
        yield Static(self._text(), id="modalbox")

    def _text(self) -> str:
        app = self.app
        # \[ escapes the bracket so the key hint isn't parsed as Textual markup
        return (f"[b]Settings[/b]\n\n"
                f"repo        : {app.repo}\n"
                f"trunk (col0): {app._trunk_label}\n"
                f"show all refs: [b]{'on' if app._all_refs else 'off'}[/b]  (git log --all)\n\n"
                f"\\[a] toggle all-refs     \\[Esc] close")

    def action_toggle_all(self) -> None:
        self.app._all_refs = not self.app._all_refs
        self.app.action_reload()
        self.query_one("#modalbox", Static).update(self._text())

    def action_close(self) -> None:
        self.dismiss()


class InputModal(ModalScreen):
    """Prompt for one line of text. Dismisses with the value, or None on cancel."""

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, prompt: str, placeholder: str = ""):
        super().__init__()
        self._prompt = prompt
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Label(self._prompt)
            yield Input(placeholder=self._placeholder, id="inp")
            yield Label(Text("[Enter] OK    [Esc] cancel"), classes="dim")

    def on_mount(self) -> None:
        self.query_one("#inp", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _OptItem(ListItem):
    def __init__(self, value: str):
        super().__init__(Label(Text(value, no_wrap=True, overflow="ellipsis")))
        self.value = value


class SelectModal(ModalScreen):
    """A filterable picker: type to filter, Enter/click to choose. Dismisses with
    the chosen value, or None on cancel."""

    BINDINGS = [("escape", "cancel", "cancel"), ("down", "to_list", "list"),
                ("slash", "to_filter", "filter")]

    def __init__(self, prompt: str, options):
        super().__init__()
        self._prompt = prompt
        self._options = list(options)

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Label(self._prompt)
            yield Input(placeholder="/ filter…", id="filter")
            yield ListView(id="opts")
            yield Label(Text("[Enter] select   [↓] list   [/] filter   [Esc] cancel"),
                        classes="dim")

    def on_mount(self) -> None:
        self._rebuild("")
        self.query_one("#filter", Input).focus()

    def _rebuild(self, q: str) -> None:
        ql = q.lower()
        items = [_OptItem(o) for o in self._options if ql in o.lower()]
        _refill_listview(self, self.query_one("#opts", ListView),
                         items, 0 if items else None)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lv = self.query_one("#opts", ListView)
        if isinstance(lv.highlighted_child, _OptItem):
            self.dismiss(lv.highlighted_child.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, _OptItem):
            self.dismiss(event.item.value)

    def action_to_list(self) -> None:
        self.query_one("#opts", ListView).focus()

    def action_to_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class BranchesModal(ModalScreen):
    """A floating window listing Local and Remote branches (filterable). Lives in
    a popup so long branch names get the full width and the Tree underneath isn't
    squeezed. Selecting a branch dismisses with (kind, name); Esc → None."""

    BINDINGS = [("escape,l", "cancel", "cancel"), ("down", "to_list", "list"),
                ("slash", "to_filter", "filter")]

    def __init__(self, local, remote):
        super().__init__()
        self._local = list(local)    # (name, display)
        self._remote = list(remote)  # (name, display)

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Label("切換分支 — 選一個 branch 按 Enter checkout")
            yield Input(placeholder="/ filter…", id="filter")
            yield ListView(id="opts")
            yield Label(Text("[Enter] checkout   [↓] list   [/] filter   [Esc] cancel"),
                        classes="dim")

    def on_mount(self) -> None:
        self._rebuild("")
        self.query_one("#filter", Input).focus()

    def _rebuild(self, q: str) -> None:
        ql = q.lower()
        items = []
        first = None  # index of the first real branch (skip the section header)

        def section(title, rows, kind):
            nonlocal first
            rows = [(n, d) for (n, d) in rows if ql in n.lower()]
            if not rows:
                return
            items.append(_SectionItem(title))
            for n, d in rows:
                items.append(_BranchOpt(kind, n, d))
                if first is None:
                    first = len(items) - 1

        section("Local", self._local, "local")
        section("Remote", self._remote, "remote")
        if first is None:
            items.append(ListItem(Label(Text("— 沒有符合的分支 —", style="dim"))))
        _refill_listview(self, self.query_one("#opts", ListView), items, first)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lv = self.query_one("#opts", ListView)
        opt = lv.highlighted_child
        if not isinstance(opt, _BranchOpt):  # highlight may sit on a section header
            opt = next((c for c in lv.children if isinstance(c, _BranchOpt)), None)
        if isinstance(opt, _BranchOpt):
            self.dismiss((opt.kind, opt.branch))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, _BranchOpt):
            self.dismiss((event.item.kind, event.item.branch))

    def action_to_list(self) -> None:
        self.query_one("#opts", ListView).focus()

    def action_to_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen):
    """Yes/No confirmation. Dismisses with True / False."""

    BINDINGS = [("y", "yes", "yes"), ("n,escape", "no", "no")]

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Static(self._message + "\n\n\\[y] yes     \\[n] no", id="modalbox")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class ProgressModal(ModalScreen):
    """Shown while an operation runs (blocking). The app dismisses it when the op
    finishes. If `cancellable` (network ops), Esc dismisses with 'cancel' so the
    caller can abort (which kills the git process tree); otherwise (tree-modifying
    local ops) it just shows '執行中' and swallows keys."""

    def __init__(self, message: str, cancellable: bool = True):
        super().__init__()
        self._message = message
        self._cancellable = cancellable

    def compose(self) -> ComposeResult:
        body = Text()
        body.append("⟳ ", style="bold yellow")
        body.append(self._message + "\n\n", style="bold")
        body.append("執行中,請稍候…", style="dim")
        if self._cancellable:
            body.append("    ")
            body.append(" Esc ", style="bold black on grey70")
            body.append(" 取消", style="dim")
        yield Static(body, id="modalbox")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()  # a blocking modal swallows everything
        if self._cancellable and event.key == "escape":
            self.dismiss("cancel")


class CredentialModal(ModalScreen):
    """git asked for a username / password / passphrase — collect it in the TUI
    and hand it back to git (secret fields are masked). Dismisses with the typed
    value, or '' on Esc."""

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, prompt: str, secret: bool):
        super().__init__()
        self._prompt = prompt
        self._secret = secret

    def compose(self) -> ComposeResult:
        with Vertical(id="modalbox"):
            yield Label("🔐 " + (self._prompt or "git 需要認證資訊:"))
            yield Input(password=self._secret, id="cred")
            yield Label(Text("[Enter] 送出    [Esc] 取消"), classes="dim")

    def on_mount(self) -> None:
        self.query_one("#cred", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss("")


class StalenessModal(ModalScreen):
    """Warns that a branch is behind / diverged from its upstream before a write
    op (merge / push / pull). Built dynamically: `actions` is a list of
    (key, token, label); pressing a key dismisses with that token, Esc → None.
    The app decides what each token does (proceed / update / fetch / integrate)."""

    def __init__(self, *, title: str, lines, actions):
        super().__init__()
        self._title = title
        self._lines = list(lines)
        self._actions = list(actions)  # (key, token, label)

    def compose(self) -> ComposeResult:
        body = Text()
        body.append(self._title + "\n\n", style="bold yellow")
        for ln in self._lines:
            body.append(ln + "\n", style="grey85")
        body.append("\n")
        for key, _tok, label in self._actions:
            body.append(f" {key} ", style="bold black on grey70")
            body.append(f" {label}    ", style="dim")
        body.append(" Esc ", style="bold black on grey70")
        body.append(" 取消", style="dim")
        yield Static(body, id="modalbox")

    def on_key(self, event: events.Key) -> None:
        # a modal swallows every key so none leaks to the app's bindings (e.g. q=quit)
        event.stop(); event.prevent_default()
        if event.key == "escape":
            self.dismiss(None)
            return
        for key, tok, _ in self._actions:
            if event.key == key:
                self.dismiss(tok)
                return


class _ConflictItem(ListItem):
    """A conflicted file row in the ConflictModal."""

    def __init__(self, path: str):
        super().__init__(Label(Text(path, no_wrap=True, overflow="ellipsis")))
        self.path = path


class ConflictModal(ModalScreen):
    """Guides the user through a mid-merge conflict: pick a file, read its
    <<< / === / >>> structure, then take 我方(ours) / 對方(theirs) / 已手動編輯;
    finally 完成合併 or 放棄合併. Talks to Flow directly and refreshes in place;
    dismisses with ('done', msg) when the merge ends or ('later', None) on Esc."""

    def __init__(self, flow, *, on_change=None):
        super().__init__()
        self.flow = flow
        self._on_change = on_change  # called after each repo-mutating step
        self._note = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="conflictbox"):
            yield Static("", id="chead")
            with Horizontal(id="crow"):
                yield ListView(id="clist")
                with VerticalScroll(id="cview"):
                    yield Static("", id="ctext")
            yield Static("", id="cfoot")

    def on_mount(self) -> None:
        verb = "revert" if self.flow.pending_op() == "revert" else "合併"
        self.query_one("#conflictbox").border_title = f"解決{verb}衝突"
        self._refresh()
        self.query_one("#clist", ListView).focus()

    def _refresh(self) -> None:
        paths = self.flow.conflicts()
        lv = self.query_one("#clist", ListView)
        keep = lv.highlighted_child.path if isinstance(
            lv.highlighted_child, _ConflictItem) else None
        lv.clear()
        for p in paths:
            lv.append(_ConflictItem(p))

        is_revert = self.flow.pending_op() == "revert"
        verb = "revert" if is_revert else "合併"
        ours = "目前內容" if is_revert else "目前分支"
        head = Text()
        head.append(f"{verb}衝突", style="bold")
        head.append(f"   我方(ours)= {ours}    對方(theirs)= "
                    f"{self.flow.incoming_label()}\n", style="dim")
        if paths:
            head.append(f"還有 {len(paths)} 個檔案有衝突 — 逐一處理", style="yellow")
        else:
            head.append(f"✓ 全部已解決 — 按 c 完成{verb}", style="bold green")
        self.query_one("#chead", Static).update(head)

        foot = Text()
        for k, lbl in [("o", "採用我方"), ("t", "採用對方"), ("e", "已手動編輯"),
                       ("c", f"完成{verb}"), ("a", f"放棄{verb}"), ("Esc", "稍後")]:
            foot.append(f" {k} ", style="bold black on grey70")
            foot.append(f" {lbl}   ", style="dim")
        if self._note:
            foot.append("\n")
            foot.append(self._note, style="yellow")
        self.query_one("#cfoot", Static).update(foot)

        if paths:  # drive off `paths`, not lv.children (stale right after clear())
            idx = paths.index(keep) if keep in paths else 0
            lv.index = idx
            self._show(paths[idx])
        else:
            self.query_one("#ctext", Static).update(
                Text("沒有衝突檔了。按 c 完成合併,或 a 放棄。", style="green"))

    def _show(self, path) -> None:
        if path:
            self.query_one("#ctext", Static).update(
                _render_conflict(self.flow.conflict_text(path)))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, _ConflictItem):
            self._show(event.item.path)

    def _current(self):
        it = self.query_one("#clist", ListView).highlighted_child
        return it.path if isinstance(it, _ConflictItem) else None

    def _step(self, fn, *args) -> None:
        try:
            self._note = fn(*args)
        except FlowError as e:
            self._note = f"⚠ {e}"
        if self._on_change:
            self._on_change()
        self._refresh()

    def on_key(self, event: events.Key) -> None:
        k = event.key
        if k in ("up", "down", "tab", "shift+tab", "home", "end",
                 "pageup", "pagedown"):
            return  # let the file list / diff scroll navigate
        event.stop(); event.prevent_default()
        p = self._current()
        if k == "o" and p:
            self._step(self.flow.resolve_ours, p)
        elif k == "t" and p:
            self._step(self.flow.resolve_theirs, p)
        elif k == "e" and p:
            self._step(self.flow.mark_resolved, [p])
        elif k == "c":
            self._finish(self.flow.complete)
        elif k == "a":
            self._finish(self.flow.abort)
        elif k == "escape":
            self.dismiss(("later", None))

    def _finish(self, fn) -> None:
        try:
            msg = fn()
        except FlowError as e:
            self._note = f"⚠ {e}"
            self._refresh()
            return
        self.dismiss(("done", msg))


class OutputModal(ModalScreen):
    """A read-only popup listing recent write commands and their results.
    Newest first: each entry is the git command(s) that ran (yellow) followed
    by the outcome — green on success, red on error."""

    BINDINGS = [("escape,o", "close", "close")]

    def __init__(self, history):
        super().__init__()
        self._history = history

    def compose(self) -> ComposeResult:
        body = Text()
        if not self._history:
            body.append("還沒有執行任何指令。\n\n", style="dim")
            body.append("(stage / commit / branch / merge / push … 之後\n"
                        "都會記錄在這裡)", style="dim")
        else:
            for i, (cmd, result, is_error) in enumerate(self._history):
                if i:
                    body.append("\n")
                body.append("$ ", style="bold yellow")
                body.append(cmd + "\n", style="yellow")
                mark = "⚠ " if is_error else "✓ "
                body.append(mark + result + "\n",
                            style="red" if is_error else "green")
        with VerticalScroll(id="modalbox"):
            yield Static(body)

    def action_close(self) -> None:
        self.dismiss()


class GitkitApp(App):
    CSS = """
    /* tree row (main) and Info box share vertically 3:2 so the tree isn't
       squeezed by a fixed-height Info box on short terminals */
    #main { height: 3fr; }
    #left { width: 30; }
    #center { width: 1fr; }
    #left ListView { border: round $primary; height: 1fr; }
    #tree { border: round $primary; height: 1fr; }
    CommitItem.head-flash { background: $warning 30%; }
    #infobox { height: 2fr; min-height: 8; border: round $primary; }
    #infohdr { height: 1; color: $accent; }
    #inforow { height: 1fr; }
    #difflist { width: 38; border-right: solid $primary; }
    #diffview { width: 1fr; }
    #difftext { width: auto; }
    #untracked:focus, #modified:focus, #staged:focus, #tree:focus { border: round $accent; }
    /* the diff file-list only has a right separator; on focus just recolour it,
       don't add a full box (that inset jump felt jarring) */
    #difflist:focus { border-right: solid $accent; }
    #infobox:focus-within { border: round $accent; }
    #statusbar { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    ModalScreen { align: center middle; }
    /* floating windows size to their content, clamped to the viewport */
    #modalbox { width: auto; min-width: 40; max-width: 90%;
                height: auto; max-height: 90%; padding: 1 2;
                border: round $accent; background: $panel; }
    #modalbox Input { margin: 1 0; width: 1fr; }
    #modalbox #opts { height: auto; max-height: 12; border: round $primary; }
    #conflictbox { width: 90%; height: 85%; padding: 1 2;
                   border: round $accent; background: $panel; }
    #chead { height: 2; }
    #crow { height: 1fr; }
    #conflictbox #clist { width: 32; border-right: solid $primary; }
    #conflictbox #clist:focus { border: round $accent; }
    #cview { width: 1fr; }
    #ctext { width: auto; }
    #cfoot { height: 3; color: $text-muted; }
    .dim { color: $text-muted; }
    """

    BINDINGS = [
        ("q", "quit", "quit"),
        ("r", "reload", "reload"),
        ("question_mark", "help", "help"),
        ("s", "settings", "settings"),
        ("escape", "diff_back", "back"),
        ("space", "stage_toggle", "stage/unstage"),
        ("d", "discard", "discard"),
        ("c", "commit", "commit"),
        ("b", "branch", "branch"),
        ("l", "branches", "branches"),
        ("m", "merge", "merge"),
        ("v", "revert", "revert"),
        ("x", "conflicts", "resolve conflicts"),
        ("f", "fetch", "fetch"),
        ("p", "pull", "pull"),
        ("P", "push", "push"),
        ("o", "output", "command log"),
    ]

    def __init__(self, repo: str):
        super().__init__()
        self.be = CliGitBackend(root=repo)
        self.flow = Flow(self.be)  # write actions go through here (F3)
        self.repo = repo
        self._gcache = GraphCache()
        self._all_refs = True
        self._trunk_label = "HEAD"
        self._normal_subtitle = ""
        self._cmd_timer = None
        self._info_ctx = ""
        self._tree_click_chain = None
        self._local_names = set()
        self._remote_names = set()
        self._local_branches = []
        self._remote_branches = []
        self._head_item = None
        self._cmdhistory = []  # (cmd_str, result, is_error) of recent write actions
        self._cmd_queue = None  # asyncio.Queue of _Cmd (created in on_mount)
        self._cmd_task = None  # the currently-running command's task (Esc cancels)
        self._progress = None  # ProgressModal shown during a modal op
        self._askpass_srv = None  # local server that relays git auth prompts → TUI
        self._askpass_token = None
        self._status_msg = "Ready"  # persistent status (Info 'loading…' overlays it)
        self._info_timer = None  # debounce timer for the Info panel
        self._info_worker = None  # in-flight Info fetch (cancelled when moving away)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield ListView(id="untracked")
                yield ListView(id="modified")
                yield ListView(id="staged")
            with Vertical(id="center"):
                yield ListView(id="tree")
        with Vertical(id="infobox"):
            yield Static("", id="infohdr")
            with Horizontal(id="inforow"):
                yield ListView(id="difflist")
                with VerticalScroll(id="diffview"):
                    yield Static("", id="difftext")
        yield Static("", id="statusbar")

    def on_mount(self) -> None:
        self.title = "gitkit"
        # the default Header icon is ⭘ (U+2B58) which renders as garbage in the
        # CentOS 7 console font — use a plain ASCII brand instead.
        self.query_one(Header).icon = "git"
        titles = {"untracked": "Untracked", "modified": "Modified", "staged": "Staged",
                  "tree": "Tree"}
        for wid, title in titles.items():
            self.query_one(f"#{wid}", ListView).border_title = title
        self.query_one("#infobox").border_title = "Info"
        self.query_one("#tree", ListView).focus()
        self.set_interval(0.7, self._blink_head)  # flash the HEAD row
        self._cmd_queue = asyncio.Queue()        # serial git-command queue
        self.run_worker(self._cmd_consumer())    # the single consumer
        self.run_worker(self._start_askpass())  # git auth prompts → TUI popup
        # initial tree load is a foreground op (ProgressModal), like the other
        # tree-modifying commands; _exec_cmd does the actual load via _reload_now
        async def _initial():
            return "Ready"
        self._enqueue(_initial, "載入 repo", modal=True, cancellable=False)

    # ── git credential prompts → TUI (GIT_ASKPASS relay) ─────────
    async def _start_askpass(self) -> None:
        import secrets
        self._askpass_token = secrets.token_hex(16)
        try:
            self._askpass_srv = await asyncio.start_server(
                self._askpass_handler, "127.0.0.1", 0)
            port = self._askpass_srv.sockets[0].getsockname()[1]
            self.be.set_askpass(f"127.0.0.1:{port}", self._askpass_token)
        except Exception:
            self._askpass_srv = None  # fall back to GIT_TERMINAL_PROMPT=0 (fail fast)

    async def _askpass_handler(self, reader, writer) -> None:
        try:
            token = (await reader.readline()).decode("utf-8", "replace").rstrip("\n")
            prompt = (await reader.readline()).decode("utf-8", "replace").rstrip("\n")
            if token != self._askpass_token:
                return
            answer = await self._ask_credential(prompt)
            writer.write(((answer or "") + "\n").encode("utf-8"))
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _ask_credential(self, prompt: str) -> str:
        low = prompt.lower()
        secret = "password" in low or "passphrase" in low
        result = await self.push_screen_wait(CredentialModal(prompt, secret))
        return result or ""

    def _blink_head(self) -> None:
        item = self._head_item
        if item is not None and item.is_mounted:
            item.toggle_class("head-flash")

    # ── status bar ───────────────────────────────────────────────
    def _set_status(self, msg: str) -> None:
        """Set the persistent status (action results, Ready, 執行中…)."""
        self._status_msg = msg
        self._render_status(msg)

    def _render_status(self, msg: str) -> None:
        # build with Text so the [Tab] / [Enter] brackets aren't eaten as markup
        t = Text()
        t.append(msg, style="bold")
        t.append("     ")
        for key, label in [("Tab", "panel"), ("Enter", "expand"), ("l", "branches"),
                           ("o", "log"), ("?", "help"), ("q", "quit")]:
            t.append(f"[{key}]", style="yellow")
            t.append(f" {label}  ", style="dim")
        self.query_one("#statusbar", Static).update(t)

    # ── data loading ─────────────────────────────────────────────
    # The git reads (_fetch_load_data) run off the event loop via asyncio.to_thread
    # so a slow workstation never freezes the UI; only the widget update
    # (_apply_load_data) runs on the main thread.
    def action_reload(self) -> None:  # the `r` key
        async def run():
            return "已重新整理"
        self._enqueue(run, "重新整理", modal=True, cancellable=False)

    def _begin_reload(self, ready: bool = False) -> None:
        """Refresh the whole view in the background (non-blocking)."""
        self.run_worker(self._reload_async(ready), exclusive=True, group="reload")

    async def _reload_async(self, ready: bool) -> None:
        try:
            data = await asyncio.to_thread(self._fetch_load_data)
        except BackendError as e:
            self._set_status(f"git error: {e}")
            return
        self._apply_load_data(data)
        if ready:
            self._set_status("Ready")

    def _load(self) -> None:
        self._apply_load_data(self._fetch_load_data())

    def _fetch_load_data(self) -> dict:
        """All git reads for a refresh — pure, runs in a worker thread."""
        be = self.be
        branch = be.current_branch()
        detached = branch is None
        files = be.status()
        return {
            "branch": branch,
            "detached": detached,
            "head_short": be.repo_state().head_sha[:7] if detached else "",
            "op": be.pending_op(),
            "files": files,
            "unmerged": bool(be.unmerged_paths()),
            "local_branches": be.branches(),
            "remote_branches": be.remote_branches(),
            "commits": be.log(limit=80, all_refs=self._all_refs),
            "remote_set": be.remote_reachable(),
        }

    def _apply_load_data(self, d: dict) -> None:
        """Apply fetched data to widgets — runs on the main thread."""
        self._trunk_label = d["branch"] or f"(detached @ {d['head_short']})"
        conflicting = d["op"] is not None and d["unmerged"]
        op_zh = {"revert": "revert", "cherry-pick": "cherry-pick"}.get(d["op"], "合併")
        self._normal_subtitle = f"{self.repo}  ·  {self._trunk_label}" + (
            "   ⚠ DETACHED HEAD" if d["detached"] else "") + (
            f"   ⚠ {op_zh}進行中(按 x 解決衝突)" if conflicting else "")
        self.sub_title = self._normal_subtitle

        files = d["files"]
        untracked = [f.path for f in files if f.is_untracked]
        modified = [f.path for f in files if f.is_unstaged]
        staged = [f.path for f in files if f.is_staged]
        self._fill_files("#untracked", untracked, "untracked")
        self._fill_files("#modified", modified, "modified")
        self._fill_files("#staged", staged, "staged")

        self._local_branches = d["local_branches"]
        self._remote_branches = d["remote_branches"]
        self._local_names = {b.name for b in self._local_branches}
        self._remote_names = {b.name for b in self._remote_branches}

        commits = d["commits"]
        head_sha = next((c.sha for c in commits if "HEAD" in c.refs), None)
        lines = self._gcache.render(commits, head_sha, d["remote_set"])
        width = max((len(g) for g, _ in lines), default=1)
        items = []
        if staged:  # the mockup's synthetic "◌ N staged" node above HEAD
            v = Text("◌ ", style="bright_yellow")
            v.append(f"{len(staged)} staged — press c to commit", style="italic yellow")
            items.append(VirtualItem(v))
        items.extend(_commit_items(lines, width))
        self._head_item = next(
            (it for it in items
             if isinstance(it, CommitItem) and "HEAD" in it.commit.refs), None)
        self._repopulate(self.query_one("#tree", ListView), items, select_first=True)

    def _repopulate(self, listview: ListView, items, *, select_first=False) -> None:
        """Rebuild a ListView, awaiting clear() before appending. ListView.clear()
        defers child removal (returns AwaitRemove), so appending synchronously
        stacks the new rows on top of the not-yet-removed old ones — which a real
        terminal paints as stale, un-moving rows (e.g. a 'main' label that won't
        advance after a fast-forward merge) until the app is reopened. Awaiting the
        removal in a per-panel worker keeps the rebuild clean; `exclusive` cancels
        an in-flight rebuild of the same panel without touching the others."""
        async def rebuild():
            await listview.clear()
            for it in items:
                listview.append(it)
            if select_first and items:
                listview.index = 0

        self.run_worker(rebuild(), group=f"refill-{listview.id}",
                        exclusive=True, exit_on_error=False)

    def _fill_files(self, selector: str, paths: List[str], kind: str) -> None:
        lv = self.query_one(selector, ListView)
        items = ([FileItem(p, kind) for p in paths] if paths
                 else [ListItem(Label(Text("—", style="dim")))])
        self._repopulate(lv, items)

    # ── Info panel (header + file-list + message/diff) ───────────
    def _set_ctx(self, text: str) -> None:
        self._info_ctx = text
        self.query_one("#infohdr", Static).update(Text(text))

    def _info_msg(self, text: str) -> None:
        lines = text.splitlines()
        if len(lines) > 600:
            lines = lines[:600] + [f"… (+{len(lines) - 600} more)"]
        self.query_one("#difftext", Static).update(Text("\n".join(lines)))

    def _info_diff(self, diff_text: str) -> None:
        w = max(40, self.size.width - self.query_one("#difflist").size.width - 8)
        self.query_one("#difftext", Static).update(render_side_by_side(diff_text, w))

    def _fill_difflist(self, rows) -> None:
        rows = list(rows)
        items = ([DiffFileItem(r, sha=sha, path=path, staged=staged)
                  for r, sha, path, staged in rows] if rows
                 else [ListItem(Label(Text("—", style="dim")))])
        self._repopulate(self.query_one("#difflist", ListView), items)

    @staticmethod
    def _file_row(f, sha, staged):
        t = Text(f" {f.path}", no_wrap=True, overflow="ellipsis")
        t.append(f"  +{f.added} ", style="green")
        t.append(f"-{f.removed}", style="red")
        return (t, sha, f.path, staged)

    # ── debounced + interruptible Info fetch ─────────────────────
    def _schedule_info(self, header, fetch) -> None:
        """Update the Info panel with a dwell delay. `header()` runs now (instant,
        no git). The stale diff is cleared and the bottom-left flips to 'loading…'
        right away, so the user is never left staring at the previous row. `fetch`
        (an async coroutine) only runs after the cursor rests INFO_DEBOUNCE seconds,
        and is hard-aborted the moment the cursor moves on — _cancel_info cancels
        the worker, which KILLS the in-flight git process (see _text_async)."""
        header()
        self._fill_difflist([])          # drop the previous row's file list
        self._info_msg("載入中…")
        self._info_loading()
        self._cancel_info()
        self._info_timer = self.set_timer(INFO_DEBOUNCE, lambda: self._fire_info(fetch))

    def _busy(self) -> bool:
        return self._cmd_task is not None and not self._cmd_task.done()

    def _info_loading(self) -> None:
        # show 'Loading...' as a TRANSIENT overlay — it doesn't overwrite the
        # persistent status (a write result / 執行中… / Ready), and never clobbers
        # an in-progress command's line.
        if not self._busy():
            self._render_status("Loading...")

    def _info_idle(self) -> None:
        # fetch done → restore whatever the persistent status was
        if not self._busy():
            self._render_status(self._status_msg)

    def _fire_info(self, fetch) -> None:
        self._info_worker = self.run_worker(
            self._info_guard(fetch), exclusive=True, group="info")

    async def _info_guard(self, fetch) -> None:
        try:
            await fetch()
            self._info_idle()
        except asyncio.CancelledError:
            raise  # superseded by a newer row — that fetch owns the status now
        except BackendError as e:
            self._info_msg(f"git error: {e}\n{getattr(e, 'stderr', '')}")
            self._info_idle()

    def _info_commit(self, c) -> None:
        glyph = "◆" if c.is_merge else "●"
        refs = f"  ({', '.join(c.refs)})" if c.refs else ""

        async def fetch():
            files = await self.be.commit_files_async(c.sha)
            msg = await self.be.commit_message_async(c.sha)
            self._fill_difflist([self._file_row(f, c.sha, False) for f in files])
            self._info_msg(msg)

        self._schedule_info(
            lambda: self._set_ctx(f"{glyph} {c.short_sha}  {c.subject}{refs}"), fetch)

    def _info_staged(self) -> None:
        async def fetch():
            files = await asyncio.to_thread(self.be.diff_files, staged=True)
            self._fill_difflist([self._file_row(f, None, True) for f in files])
            self._info_msg("")

        self._schedule_info(
            lambda: self._set_ctx("◌ staged changes — press c to commit"), fetch)

    def _info_file(self, path: str, kind: str) -> None:
        if kind == "untracked":  # no subprocess → immediate
            self._cancel_info()
            self._set_ctx(f"{kind}: {path}")
            self._fill_difflist([])
            self._info_msg(f"(untracked) {path}\n\n新檔案,尚未進入 index。")
            self._info_idle()
            return

        async def fetch():
            text = await self.be.file_diff_async(path, staged=(kind == "staged"))
            self._fill_difflist([])
            self._info_diff(text)

        self._schedule_info(lambda: self._set_ctx(f"{kind}: {path}"), fetch)

    def _cancel_info(self) -> None:
        if self._info_timer is not None:
            self._info_timer.stop()
        if self._info_worker is not None:
            self._info_worker.cancel()  # → CancelledError → _text_async kills git
            self._info_worker = None

    def action_diff_back(self) -> None:
        self.query_one("#tree", ListView).focus()

    # ── events ───────────────────────────────────────────────────
    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv_id = event.list_view.id
        item = event.item
        if lv_id == "tree":
            if isinstance(item, CommitItem):
                self._info_commit(item.commit)
            elif isinstance(item, VirtualItem):
                self._info_staged()
        elif lv_id in ("untracked", "modified", "staged") and isinstance(item, FileItem):
            self._info_file(item.path, item.kind)
            self._decorate_files(item)

    def _decorate_files(self, current) -> None:
        for sel in ("#untracked", "#modified", "#staged"):
            for child in self.query_one(sel, ListView).children:
                if isinstance(child, FileItem):
                    child.set_chips(child is current)

    def on_click(self, event: events.Click) -> None:
        # remember whether a click landed in the Tree, and if it was a double-click
        self._tree_click_chain = None
        w = event.widget
        while w is not None:
            if isinstance(w, DiffFileItem):
                # single click on an Info file opens its diff immediately — no
                # focus-then-select two-step (which felt like a double-click)
                self._open_diff_item(w)
                return
            if getattr(w, "id", None) == "tree":
                self._tree_click_chain = event.chain
                break
            w = getattr(w, "parent", None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = event.list_view.id
        if lv == "tree" and isinstance(event.item, CommitItem):
            chain = self._tree_click_chain
            self._tree_click_chain = None
            if chain == 1:  # single mouse click: just browse, don't switch HEAD
                return
            self._switch_to_commit(event.item.commit)  # Enter or double-click
            return
        if lv == "difflist" and isinstance(event.item, DiffFileItem):
            self._open_diff_item(event.item)

    def _switch_to_commit(self, commit) -> None:
        local = [r for r in commit.refs if r in self._local_names]
        remote = [r for r in commit.refs if r in self._remote_names]
        if local:  # prefer a local branch tip → stays on a branch, no detach
            target = ("main" if "main" in local else
                      "master" if "master" in local else local[0])
            self._run_flow(self.flow.checkout, target)
        elif remote:  # remote tip → create a local tracking branch
            bare = "/".join(remote[0].split("/")[1:]) or remote[0]
            self._run_flow(self.flow.checkout, bare)
        else:  # mid-history commit → detached, with a notice
            sha = commit.sha
            self.push_screen(
                ConfirmModal(f"切換 HEAD 到 commit {sha[:7]}?\n"
                             f"這會進入 detached HEAD;要在此繼續開發請先建立分支。"),
                lambda ok: self._run_flow(self.flow.checkout, sha) if ok else None)

    def _open_diff_item(self, it) -> None:
        try:
            if it.sha is not None:
                text = self.be.commit_file_diff(it.sha, it.path)
            else:
                text = self.be.file_diff(it.path, staged=it.staged)
            self._info_diff(text)
        except BackendError as e:
            self._info_msg(f"git error: {e}\n{e.stderr}")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen())

    def action_output(self) -> None:
        self.push_screen(OutputModal(self._cmdhistory))

    def action_conflicts(self) -> None:
        if not self._open_conflict_resolver():
            self._set_status("沒有進行中的合併衝突")

    def _open_conflict_resolver(self) -> bool:
        """Open the conflict guide if a merge or revert is in progress with
        unresolved files. Returns True if it opened (or is already open)."""
        try:
            conflicted = (self.be.pending_op() is not None
                          and bool(self.be.unmerged_paths()))
        except BackendError:
            conflicted = False
        if not conflicted or isinstance(self.screen, ConflictModal):
            return conflicted
        self.push_screen(ConflictModal(self.flow, on_change=self._light_reload),
                         self._after_conflict)
        return True

    def _light_reload(self) -> None:
        # refresh the main view under the modal after each resolve step
        self._begin_reload()

    def _after_conflict(self, result) -> None:
        self._begin_reload()
        if not result:
            return
        kind, msg = result
        if msg:
            self._set_status(msg)
        elif kind == "later":
            self._set_status("⚠ 合併尚未完成 — 按 x 繼續解決衝突")

    def _record(self, cmds, result: str, is_error: bool) -> None:
        """Remember the git command(s) that ran and what came of them, so the
        `o` command-log popup can show a running history (newest first, cap 50)."""
        if not cmds:
            return
        shown = "  ;  ".join(_fmt_cmd(c) for c in cmds)
        self._cmdhistory.insert(0, (shown, result, is_error))
        del self._cmdhistory[50:]

    # ── write actions: a single command QUEUE, run serially ──────
    # Each op is classified into (modal?, cancellable?):
    #   network (fetch/pull/push/update_then_*) → modal + cancellable (Esc kills)
    #   tree-modifying local (merge/checkout/revert/commit/branch/integrate/reload)
    #       → modal, NOT cancellable ("執行中")
    #   staging (stage/unstage/discard) → no modal (backgroundable), queue up
    def _run_flow(self, fn, *args) -> None:
        self._enqueue(self._wrap(fn, args), self._flow_label(fn, args),
                      **self._classify(fn))

    @staticmethod
    def _wrap(fn, args):
        if asyncio.iscoroutinefunction(fn):
            return lambda: fn(*args)
        return lambda: asyncio.to_thread(fn, *args)

    @staticmethod
    def _classify(fn) -> dict:
        if asyncio.iscoroutinefunction(fn):              # network op
            return {"modal": True, "cancellable": True}
        if getattr(fn, "__name__", "") in ("stage", "unstage", "discard"):
            return {"modal": False, "cancellable": False}  # backgroundable, queue
        return {"modal": True, "cancellable": False}     # tree-modifying local

    def _enqueue(self, coro, label, *, modal, cancellable, after=None) -> None:
        self._cmd_queue.put_nowait(
            _Cmd(coro, label, modal=modal, cancellable=cancellable, after=after))

    async def _cmd_consumer(self) -> None:
        """The one consumer: runs queued commands one at a time."""
        while True:
            cmd = await self._cmd_queue.get()
            try:
                await self._exec_cmd(cmd)
            except Exception:
                pass
            finally:
                self._cmd_queue.task_done()

    async def _exec_cmd(self, cmd: "_Cmd") -> None:
        self.be.cmdlog.clear()
        if cmd.modal:
            self._progress = ProgressModal(f"正在 {cmd.label} …", cancellable=cmd.cancellable)
            self.push_screen(self._progress, self._on_progress_dismiss)
        else:
            self._set_status(f"執行中:{cmd.label}…")
        self._cmd_task = asyncio.ensure_future(cmd.coro())
        try:
            msg, err = await self._cmd_task, None
        except FlowError as e:
            msg, err = None, str(e)
        except asyncio.CancelledError:          # only reachable for cancellable cmds
            await self._reload_now()
            self._dismiss_progress()
            self._set_status("已中止操作(git 已停止)")
            return
        finally:
            self._cmd_task = None
        cmds = [c for c in self.be.cmdlog if c and c[0] in WRITE_SUBCMDS]
        if err is not None:
            self._record(cmds, err, True)
            self._flash_command(cmds)
            self._dismiss_progress()
            self._set_status(f"⚠ {err}")
            self._open_conflict_resolver()
            return
        self._record(cmds, msg, False)
        self._flash_command(cmds)
        await self._reload_now()       # tree shows the change BEFORE the modal closes
        self._dismiss_progress()
        self._set_status(msg)
        self._open_conflict_resolver()
        if cmd.after:
            cmd.after(msg)

    async def _reload_now(self) -> None:
        try:
            self._apply_load_data(await asyncio.to_thread(self._fetch_load_data))
        except BackendError:
            pass

    @staticmethod
    def _flow_label(fn, args) -> str:
        names = {"fetch": "fetch", "pull": "pull", "push": "push",
                 "update_then_merge": "更新並合併", "update_then_push": "更新後 push",
                 "merge_into": "merge", "integrate": "整合", "revert": "revert",
                 "checkout": "checkout", "commit": "commit",
                 "stage": "stage", "unstage": "unstage", "discard": "discard"}
        label = names.get(getattr(fn, "__name__", ""), "git 操作")
        target = args[0] if args else ""
        if isinstance(target, (list, tuple)):
            target = target[0] if target else ""
        return f"{label} {target}".strip()

    def _on_progress_dismiss(self, result) -> None:
        self._progress = None
        if result == "cancel" and self._cmd_task is not None and not self._cmd_task.done():
            self._cmd_task.cancel()  # → CancelledError → kill git tree

    def _dismiss_progress(self) -> None:
        p = self._progress
        self._progress = None  # clear first so the dismiss callback is a no-op
        if p is not None:
            try:
                p.dismiss(None)
            except Exception:
                pass

    def _flash_command(self, cmds) -> None:
        """Show the git command(s) that ran in the Info header (6s, then revert)."""
        if not cmds:
            return
        shown = "  ;  ".join(_fmt_cmd(c) for c in cmds)
        self.query_one("#infohdr", Static).update(Text(f"$ {shown}", style="bold yellow"))
        if self._cmd_timer is not None:
            self._cmd_timer.stop()
        self._cmd_timer = self.set_timer(6.0, self._restore_ctx)

    def _restore_ctx(self) -> None:
        self.query_one("#infohdr", Static).update(Text(self._info_ctx))

    @staticmethod
    def _highlighted(f):
        # a dynamically-filled ListView starts with no highlight; default to row 0
        if isinstance(f, ListView):
            if f.highlighted_child is None and len(f) > 0:
                f.index = 0
            return f.highlighted_child
        return None

    def _focused_file(self):
        item = self._highlighted(self.focused)
        return item if isinstance(item, FileItem) else None

    def _default_remote(self):
        remotes = self.be.remotes()
        return remotes[0].name if remotes else None

    def action_stage_toggle(self) -> None:
        item = self._focused_file()
        if item is None:
            self._set_status("⚠ 請在 Untracked/Modified/Staged 面板選一個檔案")
            return
        if item.kind == "staged":
            self._run_flow(self.flow.unstage, [item.path])
        else:  # untracked / modified
            self._run_flow(self.flow.stage, [item.path])

    def action_discard(self) -> None:
        item = self._focused_file()
        if item is None or item.kind != "modified":
            self._set_status("⚠ 請在 Modified 面板選一個檔案(discard 只丟工作區變更)")
            return
        path = item.path
        self.push_screen(
            ConfirmModal(f"丟棄 {path} 的工作區變更?(≈ svn revert,不可復原)"),
            lambda ok: self._run_flow(self.flow.discard, [path]) if ok else None)

    def action_commit(self) -> None:
        if not self.flow.commit_preview():
            self._set_status("⚠ 沒有已暫存的檔案,無法 commit")
            return
        if self.be.current_branch() is None:  # detached: make a branch first
            self.push_screen(
                InputModal("你在 detached HEAD — 先建立分支再 commit:", "feature/x"),
                self._branch_then_commit)
            return
        n = len(self.flow.commit_preview())
        self.push_screen(InputModal(f"Commit message  ({n} files staged):", "summary"),
                         self._after_commit)

    def _branch_then_commit(self, name) -> None:
        if not name:
            return

        async def run():
            await asyncio.to_thread(self.flow.create_branch, name)
            await asyncio.to_thread(self.flow.checkout, name)
            return f"已建立並切換到 {name},接著輸入 commit 訊息"

        def after(_msg):
            self.push_screen(InputModal("Commit message:", "summary"), self._after_commit)

        self._enqueue(run, f"建立分支 {name}", modal=True, cancellable=False, after=after)

    def _after_commit(self, message) -> None:
        if message:
            self._run_flow(self.flow.commit, message)

    def action_branches(self) -> None:
        """Open the Branches popup (Local + Remote); Enter checks one out.
        (ahead/behind isn't shown here — it's the costly per-branch query; the
        staleness guard surfaces it on the branch you actually push/pull/merge.)"""
        local = []
        for b in self._local_branches:
            mark = "● " if b.is_current else "  "
            up = f"  → {b.upstream}" if b.upstream else ""
            local.append((b.name, f"{mark}{b.name}{up}"))
        remote = [(b.name, f"  {b.name}") for b in self._remote_branches]
        if not local and not remote:
            self._set_status("⚠ 沒有任何分支")
            return
        self.push_screen(BranchesModal(local, remote), self._after_branch_pick)

    def _after_branch_pick(self, choice) -> None:
        if not choice:
            return
        kind, name = choice
        if kind == "remote":
            # checkout the bare name → git makes a local tracking branch (no detach)
            name = "/".join(name.split("/")[1:]) or name
        self._run_flow(self.flow.checkout, name)

    def action_branch(self) -> None:
        self.push_screen(InputModal("New branch name (建立並切換過去):", "feature/x"),
                         self._after_branch)

    def _after_branch(self, name) -> None:
        if not name:
            return

        async def run():
            await asyncio.to_thread(self.flow.create_branch, name)
            await asyncio.to_thread(self.flow.checkout, name)  # create AND switch
            return f"已建立並切換到 {name}"

        self._enqueue(run, f"建立分支 {name}", modal=True, cancellable=False)

    def action_revert(self) -> None:
        """Create an inverse commit undoing the selected Tree commit (safe — no
        history rewrite). A merge commit prompts for which parent (mainline) to keep."""
        it = self.query_one("#tree", ListView).highlighted_child
        if not isinstance(it, CommitItem):
            self._set_status("⚠ 請在 Tree 選一個 commit 再 revert")
            return
        if self.be.current_branch() is None:
            self._set_status("⚠ detached HEAD,無法 revert(會產生 commit,請先切到分支)")
            return
        c = it.commit
        if c.is_merge:  # merge commit → must pick a mainline parent
            opts = [f"{i}: 保留第 {i} 父系  {self.be.describe_commit(p)}"
                    for i, p in enumerate(c.parents, 1)]
            self.push_screen(
                SelectModal(f"revert merge {c.short_sha} — 選要保留哪個父系(mainline):",
                            opts),
                lambda choice: self._run_flow(self.flow.revert, c.sha,
                                              int(choice.split(":")[0])) if choice else None)
            return
        self.push_screen(
            ConfirmModal(f"建立一個反向 commit 撤銷\n{c.short_sha}: {c.subject}?\n"
                         f"(不改寫歷史,原 commit 仍保留)"),
            lambda ok: self._run_flow(self.flow.revert, c.sha) if ok else None)

    def action_merge(self) -> None:
        cur = self.be.current_branch()
        if cur is None:
            self._set_status("⚠ detached HEAD,無法 merge(請先切到分支)")
            return
        # merge the CURRENT branch INTO a chosen target (local branch)
        options = [b.name for b in self.be.branches() if b.name != cur]
        if not options:
            self._set_status("⚠ 沒有其他分支可合併")
            return
        self.push_screen(SelectModal(f"把目前分支 {cur} 合併進哪個分支?", options),
                         self._after_merge_pick)

    def _after_merge_pick(self, target) -> None:
        if not target:
            return
        cur = self.be.current_branch()
        st = self.flow.upstream_state(target)

        def do_merge():
            self.push_screen(
                ConfirmModal(f"把 {cur} 合併進 {target}?\n(會先切到 {target} 再合併)"),
                lambda ok: self._run_flow(self.flow.merge_into, target) if ok else None)

        extra = []
        if st.ff_updatable and st.remote:  # target merely behind → offer clean update
            extra.append(("u", f"先更新 {target} 再合併",
                          lambda: self._run_flow(self.flow.update_then_merge,
                                                 target, st.remote)))
        self._guard(target, verb="合併", on_proceed=do_merge, extra=extra,
                    again=lambda: self._after_merge_pick(target))

    # ── staleness guard (behind / diverged before merge/push/pull) ──
    def _guard(self, branch, *, verb, on_proceed, extra=None, again=None) -> None:
        """If `branch` is behind / diverged from its upstream, warn first; else run
        on_proceed() straight away. `extra` is a list of (key, label, callable)
        offering op-specific fixes (update / integrate). `again` re-runs this same
        entry point after a fetch so counts refresh."""
        st = self.flow.upstream_state(branch)
        if not st.needs_attention:
            on_proceed()
            return
        if st.kind == "behind":
            title = f"⚠ {branch} 落後遠端 {st.upstream} {st.behind} 個 commit"
            lines = [f"{branch} 還沒整合遠端的更新。",
                     f"建議先更新再{verb},否則之後 push 可能被拒。"]
        else:  # diverged
            title = f"⚠ {branch} 與 {st.upstream} 已分歧(領先 {st.ahead} / 落後 {st.behind})"
            lines = [f"{branch} 和遠端各走一條線,無法快轉。",
                     f"需要先整合遠端更新(可能有衝突)才能乾淨地{verb}。"]
        cbs = {}
        actions = []
        for key, label, cb in (extra or []):
            actions.append((key, key, label))
            cbs[key] = cb
        actions.append(("y", "proceed", f"仍直接{verb}"))
        actions.append(("f", "fetch", "fetch 重新檢查"))

        def handle(tok):
            if tok == "proceed":
                on_proceed()
            elif tok == "fetch":
                remote = st.remote or self._default_remote()
                if remote:
                    self._run_flow(self.flow.fetch, remote)
                (again or on_proceed)()  # re-evaluate with refreshed counts
            elif tok in cbs:
                cbs[tok]()

        self.push_screen(StalenessModal(title=title, lines=lines, actions=actions),
                         handle)

    def action_fetch(self) -> None:
        remote = self._default_remote()
        if not remote:
            self._set_status("⚠ 沒有設定 remote"); return
        self._run_flow(self.flow.fetch, remote)

    def action_pull(self) -> None:
        remote = self._default_remote()
        if not remote:
            self._set_status("⚠ 沒有設定 remote"); return
        branch = self.be.current_branch()
        if branch is None:
            self._set_status("⚠ detached HEAD,無法 pull(請先切換到一個分支)"); return
        st = self.flow.upstream_state(branch)
        if st.kind != "diverged":
            self._run_flow(self.flow.pull, remote)  # current/behind(ff)/ahead → ff-only is fine
            return
        # diverged: ff-only pull cannot proceed → offer a real merge-integrate
        title = f"⚠ {branch} 與 {st.upstream} 已分歧(領先 {st.ahead} / 落後 {st.behind})"
        lines = ["ff-only pull 無法快轉。",
                 "可用合併方式把遠端更新整合進來(可能有衝突)。"]
        actions = [("i", "integrate", "用合併整合遠端更新"),
                   ("f", "fetch", "fetch 重新檢查")]

        def handle(tok):
            if tok == "integrate":
                self._run_flow(self.flow.integrate, remote)
            elif tok == "fetch":
                self._run_flow(self.flow.fetch, remote)
                self.action_pull()

        self.push_screen(StalenessModal(title=title, lines=lines, actions=actions),
                         handle)

    def action_push(self) -> None:
        remote = self._default_remote()
        branch = self.be.current_branch()
        if not remote:
            self._set_status("⚠ 沒有設定 remote"); return
        if not branch:
            self._set_status("⚠ detached HEAD,無法 push"); return

        def do_push():
            n = self.flow.push_preview()
            self.push_screen(
                ConfirmModal(f"push {branch} → {remote}?(本地領先 {n} 筆)"),
                lambda ok: self._run_flow(self.flow.push, remote, branch) if ok else None)

        st = self.flow.upstream_state(branch)
        extra = []
        if st.ff_updatable:  # behind only → pull --ff then push
            extra.append(("u", f"先 pull {branch} 再 push",
                          lambda: self._run_flow(self.flow.update_then_push,
                                                 remote, branch)))
        elif st.kind == "diverged":  # local commits + behind → integrate first, then push
            extra.append(("i", "先整合遠端更新(之後再 push)",
                          lambda: self._run_flow(self.flow.integrate, remote)))
        self._guard(branch, verb="push", on_proceed=do_push, extra=extra,
                    again=self.action_push)


def run(repo: str) -> None:
    GitkitApp(repo).run()
