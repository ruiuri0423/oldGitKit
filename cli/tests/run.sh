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

echo "== gitkit ci (happy path: add+commit, skip integrate) =="
(
  d="$(newrepo)"; cd "$d"
  echo a > a.txt; git add a.txt; git commit -qm init
  echo x > f1.txt          # untracked -> only addable item
  before="$(git rev-list --count HEAD)"
  printf '1\nadd f1\nn\n' | "$GITKIT" ci >/dev/null 2>&1
  after="$(git rev-list --count HEAD)"
  check "commit count grew" "$after" "$((before + 1))"
  check "f1 tracked now" "$(git ls-files f1.txt)" "f1.txt"
  check "commit message" "$(git log -1 --format=%s)" "add f1"
)

echo "== gitkit ci (no changes) =="
(
  d="$(newrepo)"; cd "$d"
  echo a > a.txt; git add a.txt; git commit -qm init
  out="$(printf '' | "$GITKIT" ci 2>&1)"
  case "$out" in *無文件變更*) ok "reports 無文件變更";; *) bad "no-change notice (got: $out)";; esac
)

echo "== gitkit ci integrate with conflict, resolved via tc (theirs) =="
(
  d="$(newrepo)"; cd "$d"
  printf 'line1\nshared\nline3\n' > c.txt; git add c.txt; git commit -qm base
  git checkout -q -b other
  printf 'line1\nTHEIRS\nline3\n' > c.txt; git commit -qam theirs
  git checkout -q main
  printf 'line1\nMINE\nline3\n' > c.txt    # uncommitted -> ci will add+commit it
  # ci flow: add file 1; msg "mine"; integrate? y; branches main(1) other(2) -> 2;
  #          conflict on c.txt -> tc (take theirs)
  printf '1\nmine\ny\n2\ntc\n' | "$GITKIT" ci >/dev/null 2>&1
  markers="$(grep -c '<<<<<<<' c.txt || true)"
  check "no conflict markers left" "$markers" "0"
  check "took THEIRS side" "$(sed -n 2p c.txt)" "THEIRS"
  merged="$(git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1 && echo pending || echo done)"
  check "merge finalised" "$merged" "done"
)

echo "== gitkit push guard (no new commit) =="
(
  d="$(newrepo)"; cd "$d"
  rem="$(mktemp -d)"; ( cd "$rem" && git init -q --bare . )
  echo a > a.txt; git add a.txt; git commit -qm init
  git remote add origin "$rem"; git push -q -u origin main 2>/dev/null
  out="$(printf '' | "$GITKIT" push 2>&1)"
  case "$out" in *請先執行\ gitkit\ ci*) ok "blocks empty push";; *) bad "empty-push guard (got: $out)";; esac
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

echo "== gitkit mg (merge current branch into local target, no conflict) =="
(
  d="$(newrepo)"; cd "$d"
  echo a > a.txt; git add a.txt; git commit -qm init
  git checkout -q -b feature
  echo f > f.txt; git add f.txt; git commit -qm feat
  # on feature; locals sorted: feature(1) main(2) -> target main = 2
  printf '2\n' | "$GITKIT" mg >/dev/null 2>&1
  check "switched to target main" "$(git symbolic-ref --short HEAD)" "main"
  check "feature merged into main" "$(git ls-files f.txt)" "f.txt"
)

echo "== gitkit push (new branch, decline mg, push direct) =="
(
  d="$(newrepo)"; cd "$d"
  rem="$(mktemp -d)"; ( cd "$rem" && git init -q --bare . )
  echo a > a.txt; git add a.txt; git commit -qm init
  git remote add origin "$rem"; git push -q -u origin main 2>/dev/null
  git checkout -q -b newfeat
  echo n > n.txt; git add n.txt; git commit -qm nf
  # no upstream -> mg? n ; push direct? y
  printf 'n\ny\n' | "$GITKIT" push >/dev/null 2>&1
  pushed="$(git ls-remote "$rem" refs/heads/newfeat | wc -l)"
  check "new branch pushed to remote" "$pushed" "1"
)

echo
PASS="$(grep -c P "$RESULTS" || true)"; FAIL="$(grep -c F "$RESULTS" || true)"
rm -f "$RESULTS"
echo "==== $PASS passed, $FAIL failed ===="
[ "${FAIL:-0}" -eq 0 ]
