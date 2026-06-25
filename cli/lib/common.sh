# shellcheck shell=bash
# common.sh — shared helpers for the gitkit bash CLI.
#
# Zero external dependencies: numbered-menu selection (read), colour output,
# git status/branch collection, and the shared conflict-resolution loop.
# Targets git 1.8.3.1 / bash 4.2 (CentOS 7). Plain everyday git commands only:
# no `git -C`, no porcelain v2, no exotic plumbing.
#
# Sections below: colours/logging · git wrapper · selection menus · repo state ·
# status (svn-like) · branch picker · conflict resolution · integrate/sync.

GK_VERSION="1.0"

# ── colours (only when stderr is a tty) ───────────────────────────────
if [ -t 2 ]; then
  # cyan (not blue) for info: dark blue is unreadable on a black terminal.
  GK_C_RED=$'\033[31m'; GK_C_GRN=$'\033[32m'; GK_C_YEL=$'\033[33m'
  GK_C_CYN=$'\033[36m'; GK_C_DIM=$'\033[2m'; GK_C_OFF=$'\033[0m'
else
  GK_C_RED=; GK_C_GRN=; GK_C_YEL=; GK_C_CYN=; GK_C_DIM=; GK_C_OFF=
fi

gk_info() { printf '%s%s%s\n' "$GK_C_CYN" "$*" "$GK_C_OFF" >&2; }
gk_ok()   { printf '%s* %s%s\n' "$GK_C_GRN" "$*" "$GK_C_OFF" >&2; }
gk_warn() { printf '%s! %s%s\n' "$GK_C_YEL" "$*" "$GK_C_OFF" >&2; }
gk_err()  { printf '%sx %s%s\n' "$GK_C_RED" "$*" "$GK_C_OFF" >&2; }
gk_die()  { gk_err "$*"; exit 1; }

# All git calls go through this so unicode paths stay literal (no octal escapes).
gk_git() { git -c core.quotepath=false "$@"; }

gk_need_repo() {
  gk_git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || gk_die "not inside a git working tree"
}

# ── selection menus (read from stdin; prompts go to stderr) ────────────
# gk_menu PROMPT ITEM...  → sets GK_PICK (value) + GK_PICK_IDX (0-based)
gk_menu() {
  local prompt="$1"; shift
  local items=("$@") i
  GK_PICK=""; GK_PICK_IDX=-1
  [ ${#items[@]} -eq 0 ] && return 1
  for i in "${!items[@]}"; do
    printf '  %s%2d%s) %s\n' "$GK_C_DIM" $((i + 1)) "$GK_C_OFF" "${items[$i]}" >&2
  done
  printf '%s [1-%d / Enter=cancel]: ' "$prompt" "${#items[@]}" >&2
  local line; IFS= read -r line || line=""
  [ -z "$line" ] && return 1
  case "$line" in *[!0-9]*) gk_warn "invalid input: $line"; return 1;; esac
  if [ "$line" -ge 1 ] && [ "$line" -le "${#items[@]}" ]; then
    GK_PICK="${items[$((line - 1))]}"; GK_PICK_IDX=$((line - 1)); return 0
  fi
  gk_warn "out of range: $line"; return 1
}

# gk_menu_multi PROMPT ITEM...  → sets GK_PICKS (values) + GK_PICK_IDXS (0-based)
# input: space-separated numbers, "a" = all, Enter = cancel.
gk_menu_multi() {
  local prompt="$1"; shift
  local items=("$@") i tok
  GK_PICKS=(); GK_PICK_IDXS=()
  [ ${#items[@]} -eq 0 ] && return 1
  for i in "${!items[@]}"; do
    printf '  %s%2d%s) %s\n' "$GK_C_DIM" $((i + 1)) "$GK_C_OFF" "${items[$i]}" >&2
  done
  printf '%s [space-separated numbers / a=all / Enter=cancel]: ' "$prompt" >&2
  local line; IFS= read -r line || line=""
  [ -z "$line" ] && return 1
  if [ "$line" = "a" ] || [ "$line" = "A" ]; then
    for i in "${!items[@]}"; do GK_PICKS+=("${items[$i]}"); GK_PICK_IDXS+=("$i"); done
    return 0
  fi
  for tok in $line; do
    case "$tok" in *[!0-9]*) gk_warn "skipping invalid input: $tok"; continue;; esac
    if [ "$tok" -ge 1 ] && [ "$tok" -le "${#items[@]}" ]; then
      GK_PICKS+=("${items[$((tok - 1))]}"); GK_PICK_IDXS+=("$((tok - 1))")
    else
      gk_warn "out of range: $tok"
    fi
  done
  [ ${#GK_PICK_IDXS[@]} -eq 0 ] && return 1
  return 0
}

# gk_confirm PROMPT [default y|n]  → exit 0 = yes
gk_confirm() {
  local prompt="$1" def="${2:-n}" line hint="[y/N]"
  [ "$def" = "y" ] && hint="[Y/n]"
  printf '%s %s ' "$prompt" "$hint" >&2
  IFS= read -r line || line=""
  [ -z "$line" ] && line="$def"
  case "$line" in y|Y|yes|YES) return 0;; *) return 1;; esac
}

# ── repo state helpers ────────────────────────────────────────────────
gk_current_branch() {
  local b; b="$(gk_git symbolic-ref --short -q HEAD)" || return 1
  printf '%s\n' "$b"
}

gk_default_remote() {
  local r; r="$(gk_git remote | head -n1)"
  printf '%s\n' "${r:-origin}"
}

# Map a porcelain XY pair to a single svn-like status letter.
#   ? untracked · M modified · A added · D deleted · R renamed · C conflict
gk_svn_code() {
  case "$1$2" in
    "??")                         echo "?";;
    DD|AU|UD|UA|DU|AA|UU)         echo "C";;          # unmerged / conflict
    *) if   [ "$1" != " " ] && [ "$1" != "?" ]; then echo "$1"
       elif [ "$2" != " " ];                     then echo "$2"
       else echo " "; fi;;
  esac
}

# The single svn-like status printer used everywhere (st, up, ci, ...).
# Each line is "<COL1><CODE>\t<path>":
#   COL1 = 'S' when the file is staged, else a space
#   CODE = svn-like change letter (M A D R ? C)
# Modes:
#   gk_print_status            working tree, all changes
#   gk_print_status no         working tree, hide untracked (st -uq)
#   gk_print_status <A> <B>    files changed between commits A and B (COL1 blank)
gk_print_status() {
  if [ $# -ge 1 ] && [ "$1" != "no" ]; then          # commit-range mode
    gk_git diff --name-status "$1" "${2:-HEAD}" \
      | awk -F'\t' 'NF>=2 { print " " substr($1,1,1) "\t" $NF }'
    return
  fi
  local hide_untracked="${1:-}" line x y path code staged
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    x="${line:0:1}"; y="${line:1:1}"; path="${line:3}"
    case "$path" in *" -> "*) path="${path##* -> }";; esac
    code="$(gk_svn_code "$x" "$y")"
    [ "$code" = "?" ] && [ "$hide_untracked" = "no" ] && continue
    staged=" "; case "$x" in M|A|D|R|C) staged="S";; esac
    printf '%s%s\t%s\n' "$staged" "$code" "$path"
  done < <(gk_git status --porcelain)
}

# Show, svn-like, the files a merge/pull changed since commit $1 (no-op if none).
gk_show_merged() {
  local out; out="$(gk_print_status "$1" "$(gk_git rev-parse HEAD)")"
  [ -n "$out" ] && { gk_info "changed files:"; printf '%s\n' "$out"; }
}

# Fills GK_U (untracked) and parallel GK_M/GK_Mc (modified-unstaged + code),
# GK_S/GK_Sc (staged + code). A file can appear in both GK_M and GK_S.
gk_collect_status() {
  GK_U=(); GK_M=(); GK_Mc=(); GK_S=(); GK_Sc=()
  local line x y path
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    x="${line:0:1}"; y="${line:1:1}"; path="${line:3}"
    if [ "$x" = "?" ]; then GK_U+=("$path"); continue; fi
    case "$path" in *" -> "*) path="${path##* -> }";; esac   # rename: keep new name
    case "$y" in M|D) GK_M+=("$path"); GK_Mc+=("$y");; esac
    case "$x" in M|A|D|R|C) GK_S+=("$path"); GK_Sc+=("$x");; esac
  done < <(gk_git status --porcelain)
}

# Build an svn-like menu label: "<CODE><tab><path>".
gk_lbl() { printf '%s\t%s' "$1" "$2"; }

# Build a file-selection menu from collected status (call gk_collect_status
# first). $1 = categories in display order, a subset of "S M U".
# Fills parallel arrays: GK_MENU_LABELS (svn-like), GK_MENU_PATHS, GK_MENU_KINDS.
gk_build_menu() {
  GK_MENU_LABELS=(); GK_MENU_PATHS=(); GK_MENU_KINDS=()
  local cat i
  for cat in $1; do
    case "$cat" in
      S) for i in "${!GK_S[@]}"; do
           GK_MENU_LABELS+=("$(gk_lbl "${GK_Sc[$i]}" "${GK_S[$i]}")")
           GK_MENU_PATHS+=("${GK_S[$i]}"); GK_MENU_KINDS+=("S")
         done;;
      M) for i in "${!GK_M[@]}"; do
           GK_MENU_LABELS+=("$(gk_lbl "${GK_Mc[$i]}" "${GK_M[$i]}")")
           GK_MENU_PATHS+=("${GK_M[$i]}"); GK_MENU_KINDS+=("M")
         done;;
      U) for i in "${!GK_U[@]}"; do
           GK_MENU_LABELS+=("$(gk_lbl "?" "${GK_U[$i]}")")
           GK_MENU_PATHS+=("${GK_U[$i]}"); GK_MENU_KINDS+=("U")
         done;;
    esac
  done
}

# gk_pick_branch PROMPT  → sets GK_BR_KIND (L|R), GK_BR_REF, GK_BR_REMOTE
gk_pick_branch() {
  local prompt="$1" line name rem
  local labels=() kinds=() refs=() remotes=()
  # local branches: `git branch` lines are "* name" / "  name"; skip detached.
  while IFS= read -r line; do
    name="${line:2}"
    [ -z "$name" ] && continue
    case "$name" in "("*) continue;; esac            # "(HEAD detached at ...)"
    labels+=("local   $name"); kinds+=("L"); refs+=("$name"); remotes+=("")
  done < <(gk_git branch)
  # remote branches: skip "origin/HEAD -> origin/main" symbolic lines.
  while IFS= read -r line; do
    name="${line:2}"
    [ -z "$name" ] && continue
    case "$name" in *" -> "*) continue;; */HEAD) continue;; esac
    rem="${name%%/*}"
    labels+=("remote  $name"); kinds+=("R"); refs+=("$name"); remotes+=("$rem")
  done < <(gk_git branch -r)
  [ ${#labels[@]} -eq 0 ] && { gk_warn "no branches to choose from"; return 1; }
  gk_menu "$prompt" "${labels[@]}" || return 1
  GK_BR_KIND="${kinds[$GK_PICK_IDX]}"
  GK_BR_REF="${refs[$GK_PICK_IDX]}"
  GK_BR_REMOTE="${remotes[$GK_PICK_IDX]}"
  return 0
}

# ── conflict resolution (shared by ci / mg) ───────────────────────────
# gk_keep_side ours|theirs FILE  → rewrite FILE keeping one side of every
# conflict block; auto-merged regions are preserved. Handles diff3 markers.
gk_keep_side() {
  local side="$1" file="$2" tmp
  tmp="$(mktemp)" || return 1
  awk -v side="$side" '
    /^<<<<<<< /      { c=1; keep=(side=="ours")?1:0; next }
    /^\|\|\|\|\|\|\| / { if (c) keep=0; next }            # base section: drop
    /^=======$/      { if (c) keep=(side=="theirs")?1:0; next }
    /^>>>>>>> /      { c=0; keep=1; next }
    { if (c && !keep) next; print }
  ' "$file" > "$tmp" && mv "$tmp" "$file"
}

gk_has_conflict_markers() { grep -qE '^(<<<<<<< |=======$|>>>>>>> )' "$1" 2>/dev/null; }

# Loop until no unmerged paths remain (or abort). Returns 2 on abort.
# CONTEXT (default "merge") decides what "abort" undoes:
#   merge → git merge --abort ;  stash → git reset --hard HEAD (stash preserved)
gk_resolve_conflicts() {
  local ctx="${1:-merge}" conf f ans
  while :; do
    mapfile -t conf < <(gk_git diff --name-only --diff-filter=U)
    [ ${#conf[@]} -eq 0 ] && break
    gk_warn "conflicted files (${#conf[@]}):"
    printf '   - %s\n' "${conf[@]}" >&2
    printf '%soptions%s tf=their full  mf=mine full  tc=their conflict  mc=mine conflict  e=edit  r=resolved  a=abort\n' \
      "$GK_C_DIM" "$GK_C_OFF" >&2
    printf 'choose: ' >&2
    IFS= read -r ans || ans="a"
    case "$ans" in
      tf) for f in "${conf[@]}"; do gk_git checkout --theirs -- "$f" && gk_git add -- "$f"; done;;
      mf) for f in "${conf[@]}"; do gk_git checkout --ours   -- "$f" && gk_git add -- "$f"; done;;
      tc) for f in "${conf[@]}"; do gk_keep_side theirs "$f" && gk_git add -- "$f"; done;;
      mc) for f in "${conf[@]}"; do gk_keep_side ours   "$f" && gk_git add -- "$f"; done;;
      e)  gk_git mergetool;;                          # opens configured merge.tool
      r)  for f in "${conf[@]}"; do
            if gk_has_conflict_markers "$f"; then
              gk_warn "$f still has conflict markers, not marking resolved"; continue
            fi
            gk_git add -- "$f"
          done;;
      a)  if [ "$ctx" = "stash" ]; then
            gk_git reset --hard HEAD >/dev/null 2>&1
            gk_warn "stash pop aborted (changes kept in 'git stash list')"
          else
            gk_git merge --abort 2>/dev/null; gk_warn "merge aborted"
          fi
          return 2;;
      *)  gk_warn "invalid option: $ans";;
    esac
  done
  return 0
}

# Finalise a merge commit if one is pending (MERGE_HEAD present).
gk_finish_merge() {
  if gk_git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
    gk_git commit --no-edit
  fi
}

# Merge REF into the current branch, running the conflict loop on failure.
# REF/KIND/REMOTE come from gk_pick_branch. Returns 2 if aborted.
gk_integrate() {
  local kind="$1" ref="$2" remote="$3" old
  if [ "$kind" = "R" ]; then
    # Fetch the whole remote (not `fetch $remote $branch`): on git 1.8.3.1 a
    # single-branch fetch only updates FETCH_HEAD, not refs/remotes/$remote/*,
    # so the `merge $remote/$branch` below would merge a stale ref.
    gk_info "fetch $remote ..."
    gk_git fetch "$remote" || { gk_err "fetch failed"; return 1; }
  fi
  old="$(gk_git rev-parse HEAD)"
  # --no-stat: suppress git's own diffstat; we print the changes svn-like below.
  if gk_git merge --no-edit --no-stat "$ref"; then
    gk_ok "integrated $ref, no conflicts"
    gk_show_merged "$old"
    return 0
  fi
  if gk_git diff --name-only --diff-filter=U | grep -q .; then
    gk_resolve_conflicts || { return 2; }
    gk_finish_merge
    gk_ok "conflicts resolved and merge completed"
    gk_show_merged "$old"
  else
    gk_err "merge failed (non-conflict error)"
    return 1
  fi
}

# Stash leftover (unselected) tracked edits, integrate REF, then restore the
# stash — resolving conflicts in either step. Shared by `ci` and `up`.
# Returns 0 = ok, 1 = error, 2 = aborted.
gk_pull_with_stash() {
  local kind="$1" ref="$2" remote="$3" stashed=0 rc
  if ! gk_git diff --quiet; then
    gk_info "stashing local modifications ..."
    gk_git stash && stashed=1
  fi

  gk_integrate "$kind" "$ref" "$remote"
  rc=$?
  if [ $rc -ne 0 ]; then
    [ $stashed -eq 1 ] && { gk_warn "restoring stashed changes ..."; gk_git stash pop || true; }
    return $rc
  fi

  if [ $stashed -eq 1 ]; then
    if gk_git stash pop; then
      gk_ok "restored local modifications"
    elif gk_git diff --name-only --diff-filter=U | grep -q .; then
      gk_resolve_conflicts stash || { gk_warn "aborted during stash pop"; return 2; }
      gk_git stash drop
      gk_ok "stash conflicts resolved"
    else
      gk_err "stash pop failed"
      return 1
    fi
  fi
  return 0
}
