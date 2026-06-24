# shellcheck shell=bash
# mg.sh — `gitkit mg`: merge the current branch INTO a chosen target branch.

gk_cmd_mg() {
  gk_need_repo
  local src; src="$(gk_current_branch)" || gk_die "detached HEAD, cannot mg"
  gk_info "source branch (to be merged into target): $src"

  gk_pick_branch "Select the target branch to merge into" || { gk_warn "cancelled"; return 1; }
  local kind="$GK_BR_KIND" ref="$GK_BR_REF" remote="$GK_BR_REMOTE" target

  if [ "$kind" = "R" ]; then
    target="${ref#*/}"
    if [ -n "$(gk_git branch --list "$target")" ]; then
      gk_info "switching to local $target and updating ..."
      gk_git checkout "$target" || return 1
      gk_git fetch "$remote" "$target" \
        && gk_git merge --ff-only "$remote/$target" 2>/dev/null \
        || gk_warn "could not fast-forward $target (merging as-is)"
    else
      gk_info "target only on remote; fetching to local and switching ..."
      gk_git fetch "$remote" "$target" || return 1
      gk_git checkout -b "$target" "$remote/$target" || return 1
    fi
  else
    target="$ref"
    gk_git checkout "$target" || return 1
  fi

  [ "$target" = "$src" ] && gk_die "target and source are the same, nothing to merge"

  if gk_git merge --no-edit "$src"; then
    gk_ok "merged $src into $target (no conflicts)"
  else
    if gk_git diff --name-only --diff-filter=U | grep -q .; then
      gk_resolve_conflicts || { gk_warn "mg aborted"; return 2; }
      gk_finish_merge
      gk_ok "merged $src into $target (conflicts resolved)"
    else
      gk_die "merge failed (non-conflict error)"
    fi
  fi
  gk_info "done; run gitkit push to push $target"
}
