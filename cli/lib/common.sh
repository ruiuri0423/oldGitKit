# shellcheck shell=bash
# common.sh — shared helpers for the gitkit bash CLI.
#
# Zero external dependencies: numbered-menu selection (read), colour output,
# git status/branch collection, and the shared conflict-resolution loop.
# Targets git 1.8.3.1 / bash 4.2 (CentOS 7). No `git -C`, no porcelain v2.

# ── colours (only when stderr is a tty) ───────────────────────────────
if [ -t 2 ]; then
  GK_C_RED=$'\033[31m'; GK_C_GRN=$'\033[32m'; GK_C_YEL=$'\033[33m'
  GK_C_BLU=$'\033[34m'; GK_C_DIM=$'\033[2m'; GK_C_OFF=$'\033[0m'
else
  GK_C_RED=; GK_C_GRN=; GK_C_YEL=; GK_C_BLU=; GK_C_DIM=; GK_C_OFF=
fi

gk_info() { printf '%s%s%s\n' "$GK_C_BLU" "$*" "$GK_C_OFF" >&2; }
gk_ok()   { printf '%s✓ %s%s\n' "$GK_C_GRN" "$*" "$GK_C_OFF" >&2; }
gk_warn() { printf '%s! %s%s\n' "$GK_C_YEL" "$*" "$GK_C_OFF" >&2; }
gk_err()  { printf '%s✗ %s%s\n' "$GK_C_RED" "$*" "$GK_C_OFF" >&2; }
gk_die()  { gk_err "$*"; exit 1; }

# All git reads go through this so unicode paths stay literal (no octal escapes).
gk_git() { git -c core.quotepath=false "$@"; }

gk_need_repo() {
  gk_git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || gk_die "目前不在 git 工作目錄內"
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
  printf '%s [1-%d / Enter=取消]: ' "$prompt" "${#items[@]}" >&2
  local line; IFS= read -r line || line=""
  [ -z "$line" ] && return 1
  case "$line" in *[!0-9]*) gk_warn "無效輸入: $line"; return 1;; esac
  if [ "$line" -ge 1 ] && [ "$line" -le "${#items[@]}" ]; then
    GK_PICK="${items[$((line - 1))]}"; GK_PICK_IDX=$((line - 1)); return 0
  fi
  gk_warn "超出範圍: $line"; return 1
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
  printf '%s [數字以空格分隔 / a=全部 / Enter=取消]: ' "$prompt" >&2
  local line; IFS= read -r line || line=""
  [ -z "$line" ] && return 1
  if [ "$line" = "a" ] || [ "$line" = "A" ]; then
    for i in "${!items[@]}"; do GK_PICKS+=("${items[$i]}"); GK_PICK_IDXS+=("$i"); done
    return 0
  fi
  for tok in $line; do
    case "$tok" in *[!0-9]*) gk_warn "略過無效輸入: $tok"; continue;; esac
    if [ "$tok" -ge 1 ] && [ "$tok" -le "${#items[@]}" ]; then
      GK_PICKS+=("${items[$((tok - 1))]}"); GK_PICK_IDXS+=("$((tok - 1))")
    else
      gk_warn "超出範圍: $tok"
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

gk_has_upstream() {
  gk_git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1
}

# Fills GK_U (untracked), GK_M (modified-unstaged), GK_S (staged).
# A file can appear in both GK_M and GK_S (partially staged).
gk_collect_status() {
  GK_U=(); GK_M=(); GK_S=()
  local line x y path
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    x="${line:0:1}"; y="${line:1:1}"; path="${line:3}"
    if [ "$x" = "?" ]; then GK_U+=("$path"); continue; fi
    case "$path" in *" -> "*) path="${path##* -> }";; esac   # rename: keep new name
    case "$y" in M|D) GK_M+=("$path");; esac
    case "$x" in M|A|D|R|C) GK_S+=("$path");; esac
  done < <(gk_git status --porcelain)
}

# gk_pick_branch PROMPT  → sets GK_BR_KIND (L|R), GK_BR_REF, GK_BR_REMOTE
gk_pick_branch() {
  local prompt="$1" r rem
  local labels=() kinds=() refs=() remotes=()
  while IFS= read -r r; do
    [ -z "$r" ] && continue
    labels+=("local   $r"); kinds+=("L"); refs+=("$r"); remotes+=("")
  done < <(gk_git for-each-ref --format='%(refname:short)' refs/heads)
  while IFS= read -r r; do
    [ -z "$r" ] && continue
    case "$r" in */HEAD) continue;; esac
    rem="${r%%/*}"
    labels+=("remote  $r"); kinds+=("R"); refs+=("$r"); remotes+=("$rem")
  done < <(gk_git for-each-ref --format='%(refname:short)' refs/remotes)
  [ ${#labels[@]} -eq 0 ] && { gk_warn "沒有可選分支"; return 1; }
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
gk_resolve_conflicts() {
  local conf f ans
  while :; do
    mapfile -t conf < <(gk_git diff --name-only --diff-filter=U)
    [ ${#conf[@]} -eq 0 ] && break
    gk_warn "衝突檔案 (${#conf[@]}):"
    printf '   - %s\n' "${conf[@]}" >&2
    printf '%s選項%s tf=their full  mf=mine full  tc=their conflict  mc=mine conflict  e=edit  r=resolved  a=abort\n' \
      "$GK_C_DIM" "$GK_C_OFF" >&2
    printf '請選擇: ' >&2
    IFS= read -r ans || ans="a"
    case "$ans" in
      tf) for f in "${conf[@]}"; do gk_git checkout --theirs -- "$f" && gk_git add -- "$f"; done;;
      mf) for f in "${conf[@]}"; do gk_git checkout --ours   -- "$f" && gk_git add -- "$f"; done;;
      tc) for f in "${conf[@]}"; do gk_keep_side theirs "$f" && gk_git add -- "$f"; done;;
      mc) for f in "${conf[@]}"; do gk_keep_side ours   "$f" && gk_git add -- "$f"; done;;
      e)  gk_git mergetool;;                          # opens configured merge.tool
      r)  for f in "${conf[@]}"; do
            if gk_has_conflict_markers "$f"; then
              gk_warn "$f 仍含衝突標記，未標記為已解決"; continue
            fi
            gk_git add -- "$f"
          done;;
      a)  gk_git merge --abort 2>/dev/null; gk_warn "已中止合併"; return 2;;
      *)  gk_warn "無效選項: $ans";;
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
  local kind="$1" ref="$2" remote="$3"
  if [ "$kind" = "R" ]; then
    gk_info "fetch $remote ${ref#*/} …"
    gk_git fetch "$remote" "${ref#*/}" || { gk_err "fetch 失敗"; return 1; }
  fi
  if gk_git merge --no-edit "$ref"; then
    gk_ok "整合 $ref 完成，無衝突"
    return 0
  fi
  if gk_git diff --name-only --diff-filter=U | grep -q .; then
    gk_resolve_conflicts || { return 2; }
    gk_finish_merge
    gk_ok "衝突已解決並完成合併"
  else
    gk_err "合併失敗（非衝突錯誤）"
    return 1
  fi
}
