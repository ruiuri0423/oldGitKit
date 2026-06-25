# shellcheck shell=bash
# reset.sh — `gitkit reset`: unstage files, or reset the branch to a commit.

gk_cmd_reset() {
  gk_need_repo
  gk_menu "Select reset type" \
    "Unstage files (reset HEAD -- files, keep working tree)" \
    "Reset branch to a commit" \
    || { gk_warn "cancelled"; return 1; }
  case "$GK_PICK_IDX" in
    0) gk_reset_unstage;;
    1) gk_reset_commit;;
  esac
}

gk_reset_unstage() {
  gk_collect_status
  if [ ${#GK_S[@]} -eq 0 ]; then
    gk_info "no staged files"
    return 0
  fi
  gk_build_menu "S"
  gk_menu_multi "Select files to unstage" "${GK_MENU_LABELS[@]}" || { gk_warn "cancelled"; return 1; }
  local sel=() i
  for i in "${GK_PICK_IDXS[@]}"; do sel+=("${GK_MENU_PATHS[$i]}"); done
  gk_git reset -q HEAD -- "${sel[@]}" \
    && gk_ok "unstaged ${#sel[@]} file(s)"
}

gk_reset_commit() {
  local lines
  mapfile -t lines < <(gk_git log --oneline -n 20)
  if [ ${#lines[@]} -eq 0 ]; then
    gk_info "no commits"
    return 0
  fi
  gk_menu "Select the commit to reset to" "${lines[@]}" || { gk_warn "cancelled"; return 1; }
  local sha="${GK_PICK%% *}"

  gk_menu "Select reset mode" \
    "soft  (keep index + working tree)" \
    "mixed (default: keep working tree, clear index)" \
    "hard  (discard ALL uncommitted changes! dangerous)" \
    || { gk_warn "cancelled"; return 1; }
  local mode
  case "$GK_PICK_IDX" in
    0) mode="--soft";; 1) mode="--mixed";; 2) mode="--hard";;
  esac

  if [ "$mode" = "--hard" ]; then
    gk_confirm "Confirm hard reset to $sha? This discards uncommitted changes" n \
      || { gk_warn "cancelled"; return 1; }
  fi
  gk_git reset "$mode" "$sha" && gk_ok "reset ($mode) to $sha"
}
