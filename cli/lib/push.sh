# shellcheck shell=bash
# push.sh — `gitkit push`: guard against empty/new-branch pushes.

gk_cmd_push() {
  gk_need_repo
  local br; br="$(gk_current_branch)" || gk_die "detached HEAD，無法 push"

  if gk_has_upstream; then
    local ahead; ahead="$(gk_git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)"
    if [ "${ahead:-0}" -eq 0 ]; then
      gk_warn "沒有新的 commit 可推送，請先執行 gitkit ci"
      return 0
    fi
    gk_info "推送 $br（領先 upstream $ahead 個 commit）…"
    gk_git push
    return $?
  fi

  # No upstream → this is a new branch.
  gk_warn "分支 '$br' 尚未有對應的遠端（新分支）"
  if gk_confirm "是否先執行 gitkit mg 合併到其他分支?" n; then
    gk_cmd_mg
    gk_info "mg 完成，如需推送請再次執行 gitkit push"
    return 0
  fi

  local remote; remote="$(gk_default_remote)"
  if gk_confirm "直接推送新分支到 $remote/$br?" y; then
    gk_git push -u "$remote" "$br"
  else
    gk_warn "已取消推送"
  fi
}
