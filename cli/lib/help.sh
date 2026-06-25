# shellcheck shell=bash
# help.sh — `gitkit help [command]` and `gitkit <command> -h`.
#
# gk_usage      one-screen overview (the default / `gitkit help`)
# gk_help TOPIC detailed help for one command (st/ci/up/diff/reset/exp/log)
#               or the "conflicts" topic.
# All help goes to stderr, matching the colour guard in common.sh.

# One-screen overview.
gk_usage() {
  local c="$GK_C_CYN" y="$GK_C_YEL" d="$GK_C_DIM" o="$GK_C_OFF"
  cat >&2 <<EOF
${y}gitkit${o} — simplified, svn-style git  ${d}(v${GK_VERSION})${o}

${d}Usage:${o} gitkit <command> [args]
       gitkit help [command]      ${d}# detailed help (or: gitkit <command> -h)${o}

${y}Daily flow${o}
  ${c}st${o}  [-uq]               status, svn-like   ${d}(col1 S = staged; -uq hides untracked)${o}
  ${c}ci${o}  [path...]           stage -> commit -> sync -> push
  ${c}up${o}                      pull the current branch from its upstream
  ${c}log${o} [limit] [path]      history with changed paths   ${d}(= svn log -v)${o}

${y}Inspect / fix${o}
  ${c}diff${o} [opts]             open changes in your difftool
  ${c}reset${o}                   unstage files, or reset to a commit
  ${c}exp${o}  <path> [dest]      export a folder without .git

${y}Conflicts${o} ${d}(during ci / up)${o}  tf mf tc mc e r a    ${d}-> gitkit help conflicts${o}

${d}Docs:  cli/docs/slides.html  ·  cli/docs/svn-to-git.md${o}
EOF
}

# Detailed help dispatcher.
gk_help() {
  case "${1:-}" in
    st)                 _gk_help_st;;
    ci)                 _gk_help_ci;;
    up)                 _gk_help_up;;
    diff)               _gk_help_diff;;
    reset)              _gk_help_reset;;
    exp)                _gk_help_exp;;
    log)                _gk_help_log;;
    conflict|conflicts) _gk_help_conflicts;;
    ""|help|all)        gk_usage;;
    *) gk_err "no help topic '$1'"; gk_usage; return 2;;
  esac
}

# Title line for a detailed help block.
_gk_title() { printf '%s%s%s\n' "$GK_C_YEL" "$1" "$GK_C_OFF" >&2; }

_gk_help_st() {
  _gk_title "gitkit st [-uq] — svn-like status"
  cat >&2 <<'EOF'

  One line per change:  <COL1><CODE><tab><path>
    COL1   S = the file is staged (else a space)
    CODE   M modified · A added · D deleted · R renamed · ? untracked · C conflict

  Examples
    gitkit st            SA src/new.v   SM top/pos.v    M top/alu.v    ? scratch.log
    gitkit st -uq        same, but hide untracked (?)
    gitkit st -s         any other flag is passed straight to `git status`

  Underlying: git status --porcelain  (the same printer shows what up/ci merged)
EOF
}

_gk_help_ci() {
  _gk_title "gitkit ci [path...] — commit & publish in one flow"
  cat >&2 <<'EOF'

  With no args you pick files from a menu. With paths you stage exactly those
  (svn-like) and skip the menu. Then:

    1. git add <chosen paths>
    2. git commit -m "<message you type>"
    3. pick a branch (local or remote) to integrate with AND push to
    4. git stash      (any leftover, unselected edits — keeps the merge clean)
    5. git fetch <remote> ; git merge <branch>     (conflicts -> see "conflicts")
    6. git stash pop                               (conflicts -> see "conflicts")
    7. git push <remote> HEAD:<branch>

  With no changes it asks whether to sync & push anyway.

  Examples
    gitkit ci                 pick files interactively, then commit + push
    gitkit ci src/app.v       commit just this file (skip the menu)
    gitkit ci src/ docs/      stage these paths, then commit + push
EOF
}

_gk_help_up() {
  _gk_title "gitkit up — update the current branch from its upstream"
  cat >&2 <<'EOF'

  The sync-only part of ci (no commit, no push):

    1. git stash          (if the working tree has edits)
    2. git fetch <remote> ; git merge <upstream>   (conflicts -> see "conflicts")
    3. git stash pop                               (conflicts -> see "conflicts")

  Afterwards it prints, svn-like, the files the merge changed and the local
  edits restored from the stash (no git-format status dump). Errors if the
  branch has no upstream (use `gitkit ci` to pick a branch the first time).
EOF
}

_gk_help_diff() {
  _gk_title "gitkit diff [opts] — open changes in your difftool"
  cat >&2 <<'EOF'

    gitkit diff                    pick from U/M/S, working tree vs index
    gitkit diff -uq                same menu, but list modified files only
    gitkit diff -y                 don't prompt before launching the tool (-y)
    gitkit diff <file>             diff that file directly (no commit, no menu)
    gitkit diff <commit> [path]    working tree vs <commit>
    gitkit diff <commitA> <commitB> [path]    diff between two commits

  -uq and -y may be combined and appear in any order. An argument that exists
  on disk is a path; otherwise an argument that resolves to a commit is a
  commit. Set the tool first:
    git config diff.tool <tool>     (and merge.tool for conflict edits)
EOF
}

_gk_help_reset() {
  _gk_title "gitkit reset — unstage files, or reset the branch to a commit"
  cat >&2 <<'EOF'

  Pick one of:
    A. Unstage files   git reset HEAD -- <files>   (keeps your edits)
    B. Reset to commit pick from `git log --oneline`, then a mode:
         --soft   keep index + working tree
         --mixed  (default) keep working tree, clear index
         --hard   discard ALL uncommitted changes  (asks to confirm)
EOF
}

_gk_help_exp() {
  _gk_title "gitkit exp <path> [dest] — export a folder without .git"
  cat >&2 <<'EOF'

  Extracts a tracked folder/file; the contents land FLAT in <dest> (not nested
  under <path>). When <dest> is omitted it is <path> + "_exp".

    folder   git archive --format=zip HEAD:<path>  ->  unzip into <dest>
    file     git show HEAD:<path>  >  <dest>/<name>

  Examples
    gitkit exp src/gitkit               -> src/gitkit_exp/ (contents, no .git)
    gitkit exp verilog/top out_v        -> out_v/ holds top's contents

  Folders need `unzip`.
EOF
}

_gk_help_log() {
  _gk_title "gitkit log [limit] [path] — history with changed paths"
  cat >&2 <<'EOF'

  The git form of `svn log -v`: each commit plus the files it touched (A/M/D),
  a `----` separator after each, no colour. A numeric arg is the limit
  (default 20); a non-numeric arg is the path. Either order works.

    gitkit log                  last 20 commits, repo-wide
    gitkit log src/app.v        that file's history
    gitkit log 5 src/app.v      its 5 most recent commits

  Underlying: git log --name-status -n <limit> -- <path>
  Ranges (svn -r A:B): use raw `git log --name-status A..B -- <file>`.
EOF
}

_gk_help_conflicts() {
  _gk_title "Conflict resolution (during ci / up)"
  cat >&2 <<'EOF'

  When a merge OR a stash pop conflicts, choose ONE action for all files:

    tf   their full       whole file = their version   (git checkout --theirs)
    mf   mine full        whole file = our version      (git checkout --ours)
    tc   their conflict   take their side inside each conflict block only
    mc   mine conflict    take our side inside each conflict block only
    e    edit             open your mergetool           (git mergetool)
    r    resolved         mark resolved (blocked if conflict markers remain)
    a    abort            drop the merge / undo the stash pop

  tc/mc keep the auto-merged parts and support the diff3 style.
EOF
}
