# shellcheck shell=bash
# push.sh — `gitkit push`: guard against empty/new-branch pushes.

gk_cmd_push() {
  gk_need_repo
  local br; br="$(gk_current_branch)" || gk_die "detached HEAD, cannot push"

  if gk_has_upstream; then
    local ahead; ahead="$(gk_git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)"
    if [ "${ahead:-0}" -eq 0 ]; then
      gk_warn "no new commits to push, run gitkit ci first"
      return 0
    fi
    gk_info "pushing $br ($ahead commit(s) ahead of upstream) ..."
    gk_git push
    return $?
  fi

  # No upstream → this is a new branch.
  gk_warn "branch '$br' has no upstream yet (new branch)"
  if gk_confirm "Run gitkit mg to merge into another branch first?" n; then
    gk_cmd_mg
    gk_info "mg done; run gitkit push again to push"
    return 0
  fi

  local remote; remote="$(gk_default_remote)"
  if gk_confirm "Push new branch directly to $remote/$br?" y; then
    gk_git push -u "$remote" "$br"
  else
    gk_warn "push cancelled"
  fi
}
