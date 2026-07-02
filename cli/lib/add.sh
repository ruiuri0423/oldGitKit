# shellcheck shell=bash
# add.sh — `gitkit add [path...]`: start tracking new (untracked) files.
#   The svn `svn add` counterpart: `ci` only commits already-tracked changes,
#   so a brand-new file must be `add`ed first before it can be committed.
#
#   With paths: `git add` exactly those (tracks new files + stages content).
#   No args:    pick from a menu of the untracked (?) files.

gk_cmd_add() {
  gk_need_repo
  local addpaths=("$@")            # explicit paths -> add directly, no menu

  if [ ${#addpaths[@]} -gt 0 ]; then
    gk_git add -- "${addpaths[@]}" && gk_ok "added ${#addpaths[@]} path(s)"
    return
  fi

  # No paths: offer the untracked files to pick from.
  gk_collect_status
  gk_build_menu "U"
  if [ ${#GK_MENU_PATHS[@]} -eq 0 ]; then
    gk_info "no untracked files to add"
    return 0
  fi
  if gk_menu_multi "Select untracked files to add" "${GK_MENU_LABELS[@]}"; then
    local sel=() i
    for i in "${GK_PICK_IDXS[@]}"; do sel+=("${GK_MENU_PATHS[$i]}"); done
    gk_git add -- "${sel[@]}" && gk_ok "added ${#sel[@]} file(s)"
  else
    gk_warn "no files selected"
  fi
}
