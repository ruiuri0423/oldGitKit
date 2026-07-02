# gitkit (bash CLI)

A simplified, **pure-bash** git workflow helper — no Python, no TUI, no lane
graph / INFO diff. It wraps the handful of commands a day-to-day SVN→git
migration actually needs, behind numbered-menu prompts.

Targets **git 1.8.3.1 / bash 4.2 on CentOS 7** (air-gapped, no root): zero
external dependencies, plain everyday git commands only (no `git -C`, no
porcelain v2, no exotic plumbing).

**Slides:** [`docs/slides.html`](docs/slides.html) (and a print of it in
[`docs/slides.pdf`](docs/slides.pdf)) walk through each command and the exact
native git flow behind it. Open the HTML in a browser (arrow keys to navigate)
or read the PDF.

**SVN users:** [`docs/svn-to-git.md`](docs/svn-to-git.md) maps common SVN
commands (including `svn log -v -r -l <file>`) to git and the `gitkit` shortcuts.

## Install

```sh
# put cli/ somewhere stable, then expose `gitkit` on PATH:
ln -s /path/to/cli/gitkit ~/bin/gitkit     # or: export PATH="$PATH:/path/to/cli"
```

`gitkit` resolves its own `lib/` relative to the (symlink-followed) script, so
the symlink works from anywhere.

## Commands

Run `gitkit help` for a one-screen overview, `gitkit help <command>` (or
`gitkit <command> -h`) for detailed help with examples, and `gitkit help
conflicts` for the conflict options. `gitkit --version` prints the version.

| command | flow |
|---------|------|
| `gitkit st`    | svn-like status: one `<S?><CODE>\t<path>` line per change — column 1 is `S` when the file is staged (else a space), then the change letter (`M A D R ? C`). `gitkit st -uq` hides untracked. Other flags pass through to `git status`. The same printer is reused by `up`/`ci` to show what a merge changed. |
| `gitkit add`   | start tracking new (untracked) files — the `svn add` counterpart. With no args you pick untracked files from a menu; `gitkit add <path>...` adds exactly those. `ci` only commits already-tracked changes, so a brand-new file must be `add`ed first (then a later `ci` picks it up). Underlying: `git add -- <paths>`. |
| `gitkit ci`    | the one combined flow — see below. Commits **already-tracked** changes only (svn-like); untracked files are never staged here (use `gitkit add`). `push` and `mg` are folded into it. `gitkit ci <path>...` stages the tracked changes under those paths (`git add -u`) and skips the file menu. |
| `gitkit up`    | update the current branch from its upstream (no commit/push): `git stash` leftover edits → `fetch` + `merge` upstream → `git stash pop`, with the same conflict handling as `ci`. Errors if the branch has no upstream. |
| `gitkit diff`  | pick U/M/S files and open each in git's configured `difftool` (untracked skipped). `gitkit diff <file>` diffs that file directly (no commit, no menu). `gitkit diff -uq` lists modified only; `gitkit diff -y` skips the tool's launch prompt. `gitkit diff <commit> [path]` diffs the working tree against a commit; `gitkit diff <commitA> <commitB> [path]` diffs two commits. |
| `gitkit reset` | unstage files (`reset HEAD -- files`), or reset the branch to a commit (`--soft`/`--mixed`/`--hard`; hard asks for confirmation). |
| `gitkit exp`   | export a tracked folder/file **without** `.git`: `gitkit exp <path> [dest]`. The folder's contents land **flat** in `<dest>` (not nested under `<path>`), via `git archive HEAD:<path>` → `unzip` (a single file is written with `git show`). When `<dest>` is omitted it is built from `<path>` by appending `_exp` (e.g. `src/app` → `src/app_exp`). Folders require `unzip`. |
| `gitkit log`   | history with changed paths (the `svn log -v` equivalent): `gitkit log [limit] [path]` → `git log --name-status`, with a `----` separator after each commit and no colour. A numeric arg is the limit (default 20), a non-numeric arg is the path; either order works. |

### `gitkit ci`

A single commit → sync → push flow. It stages **already-tracked** changes only
(svn-like) — untracked files never appear here; run `gitkit add <path>` first to
start tracking them (then they are committed too, since they're already staged):

1. show tracked (M) files, pick which to `add` (`git add -u`);
2. `commit` with a message you type;
3. pick a branch (local or remote) — the one to sync with **and push to**;
4. if any modified files are left over, `git stash` them so the merge is clean;
5. `fetch` + `merge` the chosen branch into the current branch (conflict loop);
6. `git stash pop` to restore the leftover edits (conflict loop);
7. after confirmation, `git push <remote> HEAD:<chosen branch>`.

With no local changes it reports "No changes to commit" and asks whether to
sync & push anyway. Conflicts from either the merge (step 5) or the stash pop
(step 6) go through the same resolution options below.

## Conflict resolution

When `ci` produces conflicts (from the merge or the stash pop), choose one
action for **all** conflicted files:

| option | meaning | underlying git |
|--------|---------|----------------|
| `tf` | their full — replace the whole file with their version | `git checkout --theirs` |
| `mf` | mine full — keep the whole file as our version        | `git checkout --ours` |
| `tc` | their conflict — take their side only inside conflict blocks, keep auto-merged parts | conflict-marker parse (awk) |
| `mc` | mine conflict — take our side only inside conflict blocks  | conflict-marker parse (awk) |
| `e`  | edit — open git's configured mergetool in sequence | `git mergetool` |
| `r`  | resolved — mark resolved and finish (files still containing markers are blocked) | `git add` |
| `a`  | abort — drop the whole merge | `git merge --abort` |

`tc`/`mc` support the diff3 conflict style (the `|||||||` base section is
dropped).

## Selection UI

Zero-dependency: a numbered list is printed; type the number(s). Multi-select
takes space-separated numbers (e.g. `1 3 5`), `a` selects all, and a bare Enter
cancels.

## diff / merge tool config

`diff` and the conflict `e` option use git's standard configuration, so set:

```sh
git config --global diff.tool  <tool>
git config --global merge.tool <tool>
```

## Tests

```sh
bash cli/tests/run.sh
```

Builds throwaway repos and pipes menu answers to exercise `ci` (commit/push,
stash restore, merge conflict), `up` (fast-forward pull, stash restore, no
upstream), `exp` (flat folder export, default dest, single file), `reset`,
`st`, and the conflict parser (36 checks). The interactive `e`/difftool paths
are out of scope for the automated tests.

## git commands used

Everyday porcelain plus a few standard read-only idioms, all available in git
1.8.3.1:

```
add / add -u  commit  push  fetch  merge  checkout  reset  diff  difftool  mergetool
stash / stash pop / stash drop / stash list   archive --format=zip
status / status -uno / status --porcelain   branch / branch -r / branch --list
log --oneline   cat-file -t   show   (exp: detect tree/blob, write a file)
remote   symbolic-ref   rev-parse   rev-list --count
```
