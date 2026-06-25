#!/usr/bin/env bash
# Test harness for the gitkit bash CLI. Builds throwaway repos and drives the
# commands by piping menu answers on stdin. Run: bash cli/tests/run.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/.."
GITKIT="$CLI/gitkit"
# Subshell-safe tallies: ok/bad append to a shared file (RESULTS is inherited).
RESULTS="$(mktemp)"; export RESULTS

ok()   { printf '  ✓ %s\n' "$1"; echo P >> "$RESULTS"; }
bad()  { printf '  ✗ %s\n' "$1"; echo F >> "$RESULTS"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want=[$3] got=[$2])"; fi; }

newrepo() {
  local d; d="$(mktemp -d)"
  ( cd "$d" && { git init -q -b main . 2>/dev/null || git init -q .; } \
    && git config user.email t@t && git config user.name t \
    && git config commit.gpgsign false && git config core.autocrlf false \
  ) >/dev/null 2>&1
  printf '%s\n' "$d"
}

echo "== gk_keep_side (conflict-marker resolver) =="
(
  . "$CLI/lib/common.sh"
  f="$(mktemp)"
  printf 'top\n<<<<<<< HEAD\nMINE\n=======\nTHEIRS\n>>>>>>> other\nbottom\n' > "$f"
  cp "$f" "$f.b"
  gk_keep_side ours "$f"
  check "ours keeps MINE+context" "$(tr '\n' ',' < "$f")" "top,MINE,bottom,"
  cp "$f.b" "$f"
  gk_keep_side theirs "$f"
  check "theirs keeps THEIRS+context" "$(tr '\n' ',' < "$f")" "top,THEIRS,bottom,"
  # diff3 style with base section
  printf '<<<<<<< HEAD\nMINE\n||||||| base\nBASE\n=======\nTHEIRS\n>>>>>>> x\n' > "$f"
  gk_keep_side ours "$f"
  check "diff3 ours drops base+theirs" "$(tr '\n' ',' < "$f")" "MINE,"
)

echo "== gk_collect_status =="
(
  . "$CLI/lib/common.sh"
  d="$(newrepo)"; cd "$d"
  echo a > tracked.txt; git add tracked.txt; git commit -qm init
  echo new > untracked.txt           # U
  echo b >> tracked.txt              # M (unstaged)
  echo c > staged.txt; git add staged.txt   # S
  gk_collect_status
  check "untracked detected" "${GK_U[*]}" "untracked.txt"
  check "modified detected"  "${GK_M[*]}" "tracked.txt"
  check "staged detected"    "${GK_S[*]}" "staged.txt"
)

echo "== gitkit ci (commit selected + push to chosen branch) =="
(
  d="$(newrepo)"; cd "$d"
  rem="$(mktemp -d)"; ( cd "$rem" && git init -q --bare . )
  echo a > a.txt; git add a.txt; git commit -qm init
  git remote add origin "$rem"; git push -q -u origin main 2>/dev/null
  echo edit >> a.txt          # modified tracked -> the only addable item
  # add file 1; msg; branch list local main(1) remote origin/main(2) -> push to 2;
  # clean tree after commit (no stash); merge up-to-date; push confirm y
  printf '1\nedit a\n2\ny\n' | "$GITKIT" ci >/dev/null 2>&1
  check "commit message" "$(git log -1 --format=%s)" "edit a"
  check "pushed to origin/main" "$(git ls-remote "$rem" refs/heads/main | cut -f1)" "$(git rev-parse HEAD)"
)

echo "== gitkit ci (stash leftover edit, restore after merge) =="
(
  d="$(newrepo)"; cd "$d"
  rem="$(mktemp -d)"; ( cd "$rem" && git init -q --bare . )
  printf 'a\n' > a.txt; printf 'b\n' > b.txt; git add a.txt b.txt; git commit -qm init
  git remote add origin "$rem"; git push -q -u origin main 2>/dev/null
  printf 'a2\n' >> a.txt       # will commit (file 1)
  printf 'b2\n' >> b.txt       # leftover -> stashed, then popped back
  # paths sorted: a.txt(1) b.txt(2); select only 1; msg; push branch 2; push y
  printf '1\nedit a\n2\ny\n' | "$GITKIT" ci >/dev/null 2>&1
  check "leftover b.txt restored" "$(tail -n1 b.txt)" "b2"
  check "a.txt committed" "$(git log -1 --format=%s)" "edit a"
  check "no stash left" "$(git stash list | wc -l | tr -d ' ')" "0"
)

echo "== gitkit ci (merge conflict resolved via tc, push skipped) =="
(
  d="$(newrepo)"; cd "$d"
  printf 'line1\nshared\nline3\n' > c.txt; git add c.txt; git commit -qm base
  git checkout -q -b other
  printf 'line1\nTHEIRS\nline3\n' > c.txt; git commit -qam theirs
  git checkout -q main
  printf 'line1\nMINE\nline3\n' > c.txt    # uncommitted -> ci commits it
  # add 1; msg; push branch: local main(1) other(2) -> merge other = 2;
  # conflict -> tc (theirs); push confirm n (no remote configured)
  printf '1\nmine\n2\ntc\nn\n' | "$GITKIT" ci >/dev/null 2>&1
  check "no conflict markers left" "$(grep -c '<<<<<<<' c.txt || true)" "0"
  check "took THEIRS side" "$(sed -n 2p c.txt)" "THEIRS"
  check "merge finalised" "$(git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1 && echo pending || echo done)" "done"
)

echo "== gitkit ci (no changes, decline sync) =="
(
  d="$(newrepo)"; cd "$d"
  echo a > a.txt; git add a.txt; git commit -qm init
  out="$(printf 'n\n' | "$GITKIT" ci 2>&1)"
  case "$out" in *"No changes to commit"*) ok "reports No changes to commit";; *) bad "no-change notice (got: $out)";; esac
)

echo "== gitkit reset unstage =="
(
  d="$(newrepo)"; cd "$d"
  echo a > a.txt; git add a.txt; git commit -qm init
  echo b > b.txt; git add b.txt        # staged
  printf '1\n1\n' | "$GITKIT" reset >/dev/null 2>&1   # kind=1 unstage, pick file 1
  staged="$(git diff --cached --name-only)"
  check "b.txt unstaged" "$staged" ""
  check "b.txt still present" "$(cat b.txt)" "b"
)

echo "== gitkit reset to commit (mixed) =="
(
  d="$(newrepo)"; cd "$d"
  echo 1 > a.txt; git add a.txt; git commit -qm c1
  echo 2 >> a.txt; git commit -qam c2
  first="$(git rev-list --max-parents=0 HEAD)"
  # kind=2 commit-reset; pick commit #2 (the older c1) ; mode 2 = mixed
  printf '2\n2\n2\n' | "$GITKIT" reset >/dev/null 2>&1
  check "HEAD moved to c1" "$(git rev-parse HEAD)" "$first"
  check "working file kept (uncommitted)" "$(cat a.txt)" "$(printf '1\n2')"
)

echo "== gitkit st (full vs -uq modified-only) =="
(
  d="$(newrepo)"; cd "$d"
  echo a > a.txt; git add a.txt; git commit -qm init
  echo b >> a.txt          # modified tracked
  echo x > untr.txt        # untracked
  full="$("$GITKIT" st 2>&1)"
  modonly="$("$GITKIT" st -uq 2>&1)"
  case "$full"    in *untr.txt*) ok "st shows untracked";;     *) bad "st should show untracked";; esac
  case "$modonly" in *untr.txt*) bad "st -uq should hide untracked";; *) ok "st -uq hides untracked";; esac
  case "$modonly" in *a.txt*)    ok "st -uq still shows modified";; *) bad "st -uq should show modified";; esac
)

echo
PASS="$(grep -c P "$RESULTS" || true)"; FAIL="$(grep -c F "$RESULTS" || true)"
rm -f "$RESULTS"
echo "==== $PASS passed, $FAIL failed ===="
[ "${FAIL:-0}" -eq 0 ]
