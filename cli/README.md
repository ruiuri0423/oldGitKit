# gitkit (bash CLI)

A simplified, **pure-bash** git workflow helper — no Python, no TUI, no lane
graph / INFO diff. It wraps the handful of commands a day-to-day SVN→git
migration actually needs, behind numbered-menu prompts.

Targets **git 1.8.3.1 / bash 4.2 on CentOS 7** (air-gapped, no root): zero
external dependencies, plain everyday git commands only (no `git -C`, no
porcelain v2, no exotic plumbing).

## Install

```sh
# put cli/ somewhere stable, then expose `gitkit` on PATH:
ln -s /path/to/cli/gitkit ~/bin/gitkit     # or: export PATH="$PATH:/path/to/cli"
```

`gitkit` resolves its own `lib/` relative to the (symlink-followed) script, so
the symlink works from anywhere.

## Commands

| command | flow |
|---------|------|
| `gitkit ci`    | pick U/M files → `add` → type a message → `commit` → pick a branch to `fetch` + merge-integrate (with conflict handling). Reports "No changes" when there is nothing to do. |
| `gitkit push`  | upstream exists but 0 ahead → tells you to `ci` first; new branch (no upstream) → asks whether to `mg` first, otherwise `push -u`. |
| `gitkit mg`    | merge the **current** branch into a chosen target; if the target is remote-only, `fetch` + `checkout -b` to bring it local first. Tells you to `push` afterwards. |
| `gitkit diff`  | pick U/M/S files and open each in git's configured `difftool` (untracked files are skipped). |
| `gitkit reset` | unstage files (`reset HEAD -- files`), or reset the branch to a commit (`--soft`/`--mixed`/`--hard`; hard asks for confirmation). |

## Conflict resolution

When `ci` / `mg` integration produces conflicts, choose one action for **all**
conflicted files:

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

Builds throwaway repos and pipes menu answers to exercise `ci`/`push`/`mg`/
`reset` and the conflict parser (21 checks). The interactive `e`/difftool paths
are out of scope for the automated tests.

## git commands used

Everyday porcelain plus a few standard read-only idioms, all available in git
1.8.3.1:

```
add  commit  push  fetch  merge  checkout  reset  diff  difftool  mergetool
status --porcelain   branch / branch -r / branch --list   log --oneline
remote   symbolic-ref   rev-parse   rev-list --count
```
