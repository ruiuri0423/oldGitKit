# SVN → git command map

A quick reference for the team moving from SVN to git. The last column shows the
`gitkit` shortcut where one exists. All git commands are valid on git 1.8.3.1.

## The command you asked about

```
svn log -v -r <REV1>:<REV2> -l <N> <file>
```

| svn flag | meaning | git equivalent |
|----------|---------|----------------|
| `log`            | show history            | `git log` |
| `-v`             | list the changed paths  | `--name-status` (or `--stat`) |
| `-r REV1:REV2`   | restrict to a revision range | `<REV1>..<REV2>` (a commit range) |
| `-l N`           | limit to N entries      | `-n N` |
| `<file>`         | only this path          | `-- <file>` |

So the whole thing maps to:

```sh
git log --name-status <REV1>..<REV2> -n <N> -- <file>
```

and the everyday "verbose history of one file" is simply:

```sh
git log --name-status -- <file>
#   shortcut:
gitkit log [limit] <file>      # default limit 20; either arg order works
```

> Note on revisions: SVN revisions are global integers; git has no `r123`. Use a
> commit range `A..B`, a tag/branch, `HEAD~5..HEAD`, or a date
> (`--since=2026-01-01`). `gitkit log` keeps it to `[path] [limit]`; for ranges
> use raw `git log` as above. `gitkit log` prints a `----` separator after each
> commit and uses no colour.

## Common SVN → git

| SVN | git | gitkit |
|-----|-----|--------|
| `svn checkout URL`          | `git clone URL`                                   | — |
| `svn update`               | `git pull` (= `fetch` + `merge`)                  | `gitkit up` |
| `svn status`               | `git status`                                      | `gitkit st` (svn-like, `S`=staged) |
| `svn status -q`            | `git status -uno` (hide untracked)                | `gitkit st -uq` |
| `svn add <f>`              | `git add <f>`                                     | `gitkit ci <f>` |
| `svn delete <f>`          | `git rm <f>`                                       | — |
| `svn commit <f> -m "msg"`  | `git commit -m "msg"` **+** `git push`            | `gitkit ci <f>` |
| `svn diff`                 | `git diff` / `git difftool`                       | `gitkit diff` |
| `svn diff -r REV <f>`      | `git diff REV -- <f>`                             | `gitkit diff REV <f>` |
| `svn diff -r A:B <f>`      | `git diff A..B -- <f>`                             | `gitkit diff A B <f>` |
| `svn log`                  | `git log`                                          | `gitkit log` |
| `svn log -v <f>`           | `git log --name-status -- <f>`                    | `gitkit log <f>` |
| `svn log -l N`             | `git log -n N`                                     | `gitkit log N <f>` |
| `svn revert <f>`           | `git checkout -- <f>` (discard working changes)   | — |
| `svn revert` (unstage)     | `git reset HEAD -- <f>`                            | `gitkit reset` |
| `svn cat -r REV <f>`       | `git show REV:<f>`                                 | — |
| `svn export URL DIR`       | `git archive --format=zip HEAD:<path>` + unzip    | `gitkit exp <path> [dest]` |
| `svn info`                 | `git remote -v` · `git status` · `git rev-parse HEAD` | — |
| `svn copy … (branch/tag)`  | `git branch <name>` / `git tag <name>`            | — |
| `svn switch <branch>`      | `git checkout <branch>`                           | — |
| `svn merge <branch>`       | `git merge <branch>`                              | `gitkit ci` (pick branch) |
| `svn resolve --accept …`   | edit + `git add <f>` (mark resolved)              | `gitkit ci` conflict loop |
| `svn blame <f>`            | `git blame <f>`                                   | — |
| `svn cleanup`              | (rarely needed) `git gc` / remove `.git/index.lock` | — |

## Mental-model differences

- **Commit ≠ publish.** `svn commit` sends to the server in one step; in git
  `git commit` is local and `git push` publishes. `gitkit ci` does both.
- **Revisions.** SVN `rNNN` is a global number; git uses commit SHAs (and
  ranges `A..B`), not sequential integers.
- **Whole-repo commits.** git commits the whole staged set as one snapshot;
  there is no per-file revision number like SVN.
- **Offline.** Almost everything in git (commit, log, diff, branch) is local and
  works with no server — only `fetch`/`pull`/`push` touch the network.
