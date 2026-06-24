# shellcheck shell=bash
# ci.sh — `gitkit ci`: pick files → add → commit → integrate a branch.

gk_cmd_ci() {
  gk_need_repo
  gk_collect_status

  # Files the user can still add = modified-unstaged + untracked.
  local addable=() labels=() f i
  for f in "${GK_M[@]}"; do addable+=("$f"); labels+=("M  $f"); done
  for f in "${GK_U[@]}"; do addable+=("$f"); labels+=("U  $f"); done

  if [ ${#addable[@]} -eq 0 ] && [ ${#GK_S[@]} -eq 0 ]; then
    gk_info "No changes"
    return 0
  fi

  if [ ${#addable[@]} -gt 0 ]; then
    if gk_menu_multi "Select files to commit" "${labels[@]}"; then
      local sel=()
      for i in "${GK_PICK_IDXS[@]}"; do sel+=("${addable[$i]}"); done
      gk_git add -- "${sel[@]}" && gk_ok "added ${#sel[@]} file(s)"
    else
      gk_warn "no new files selected"
    fi
  fi

  # Commit whatever is staged.
  if gk_git diff --cached --quiet; then
    gk_warn "nothing staged, skipping commit"
  else
    printf 'Commit message: ' >&2
    local msg; IFS= read -r msg || msg=""
    if [ -z "$msg" ]; then
      gk_warn "empty message, commit cancelled"
    else
      gk_git commit -m "$msg" && gk_ok "committed"
    fi
  fi

  # Optionally fetch/pull + integrate a (possibly different) branch.
  if gk_confirm "Fetch/pull and integrate another branch?" y; then
    if gk_pick_branch "Select a branch to fetch/pull and integrate"; then
      gk_integrate "$GK_BR_KIND" "$GK_BR_REF" "$GK_BR_REMOTE"
    else
      gk_warn "skipping integration"
    fi
  fi
}
