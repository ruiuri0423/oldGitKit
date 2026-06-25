# shellcheck shell=bash
# ci.sh — `gitkit ci [path...]`: the one combined flow.
#   pick U/M files (or take explicit paths) -> add -> commit -> pick a branch ->
#   stash leftover edits -> fetch + merge that branch -> stash pop -> push.
# Passing paths (svn-like) skips the file-selection menu and stages them directly.
# Standalone push/mg were folded into this; conflicts (merge or stash pop) go
# through the shared tf/mf/tc/mc/e/r/a loop.

gk_cmd_ci() {
  gk_need_repo
  local addpaths=("$@")            # explicit paths -> stage directly, no menu
  gk_collect_status

  # 1-3. Files the user can still add = modified-unstaged + untracked.
  local addable=() labels=() f i
  for i in "${!GK_M[@]}"; do addable+=("${GK_M[$i]}"); labels+=("$(gk_lbl "${GK_Mc[$i]}" "${GK_M[$i]}")"); done
  for f in "${GK_U[@]}"; do addable+=("$f"); labels+=("$(gk_lbl "?" "$f")"); done

  if [ ${#addpaths[@]} -gt 0 ]; then
    # svn-like: `gitkit ci <path/file>...` -> stage exactly those, skip selection.
    gk_git add -- "${addpaths[@]}" && gk_ok "added ${#addpaths[@]} path(s)"
  elif [ ${#addable[@]} -eq 0 ] && [ ${#GK_S[@]} -eq 0 ]; then
    gk_info "No changes to commit"
    gk_confirm "Sync & push anyway?" n || return 0
  else
    if [ ${#addable[@]} -gt 0 ]; then
      if gk_menu_multi "Select files to commit" "${labels[@]}"; then
        local sel=()
        for i in "${GK_PICK_IDXS[@]}"; do sel+=("${addable[$i]}"); done
        gk_git add -- "${sel[@]}" && gk_ok "added ${#sel[@]} file(s)"
      else
        gk_warn "no new files selected"
      fi
    fi
  fi

  # Commit whatever ended up staged.
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

  # 4-5. Pick the branch to integrate with and push to.
  gk_pick_branch "Select the branch to push to" || { gk_warn "cancelled"; return 1; }
  local kind="$GK_BR_KIND" ref="$GK_BR_REF" remote="$GK_BR_REMOTE"

  # 6-8. Stash leftover edits -> fetch + merge the chosen branch -> stash pop.
  gk_pull_with_stash "$kind" "$ref" "$remote" || return $?

  # 9. Push current HEAD to the chosen branch.
  local premote ptarget cur
  if [ "$kind" = "R" ]; then premote="$remote"; ptarget="${ref#*/}"
  else premote="$(gk_default_remote)"; ptarget="$ref"; fi
  cur="$(gk_current_branch)" || cur="HEAD"
  if gk_confirm "Push $cur -> $premote/$ptarget?" y; then
    gk_git push "$premote" "HEAD:$ptarget" && gk_ok "pushed to $premote/$ptarget"
  else
    gk_warn "push skipped"
  fi
}
