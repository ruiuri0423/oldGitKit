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
    gk_info "無文件變更"
    return 0
  fi

  if [ ${#addable[@]} -gt 0 ]; then
    if gk_menu_multi "選擇要 commit 的檔案" "${labels[@]}"; then
      local sel=()
      for i in "${GK_PICK_IDXS[@]}"; do sel+=("${addable[$i]}"); done
      gk_git add -- "${sel[@]}" && gk_ok "已 add ${#sel[@]} 個檔案"
    else
      gk_warn "未選擇新檔案"
    fi
  fi

  # Commit whatever is staged.
  if gk_git diff --cached --quiet; then
    gk_warn "沒有已暫存的變更，略過 commit"
  else
    printf 'Commit 訊息: ' >&2
    local msg; IFS= read -r msg || msg=""
    if [ -z "$msg" ]; then
      gk_warn "訊息為空，取消 commit"
    else
      gk_git commit -m "$msg" && gk_ok "已 commit"
    fi
  fi

  # Optionally fetch/pull + integrate a (possibly different) branch.
  if gk_confirm "是否要 fetch/pull 整合其他分支?" y; then
    if gk_pick_branch "選擇要 fetch/pull 整合的分支"; then
      gk_integrate "$GK_BR_KIND" "$GK_BR_REF" "$GK_BR_REMOTE"
    else
      gk_warn "略過整合"
    fi
  fi
}
