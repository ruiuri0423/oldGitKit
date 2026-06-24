# shellcheck shell=bash
# reset.sh — `gitkit reset`: unstage files, or reset the branch to a commit.

gk_cmd_reset() {
  gk_need_repo
  gk_menu "選擇 reset 種類" \
    "取消暫存檔案 (unstage，保留工作目錄變更)" \
    "重置分支到某個 commit" \
    || { gk_warn "已取消"; return 1; }
  case "$GK_PICK_IDX" in
    0) gk_reset_unstage;;
    1) gk_reset_commit;;
  esac
}

gk_reset_unstage() {
  gk_collect_status
  if [ ${#GK_S[@]} -eq 0 ]; then
    gk_info "沒有已暫存的檔案"
    return 0
  fi
  gk_menu_multi "選擇要取消暫存的檔案" "${GK_S[@]}" || { gk_warn "已取消"; return 1; }
  gk_git reset -q HEAD -- "${GK_PICKS[@]}" \
    && gk_ok "已取消暫存 ${#GK_PICKS[@]} 個檔案"
}

gk_reset_commit() {
  local lines
  mapfile -t lines < <(gk_git log --oneline -n 20)
  if [ ${#lines[@]} -eq 0 ]; then
    gk_info "沒有 commit"
    return 0
  fi
  gk_menu "選擇要重置到的 commit" "${lines[@]}" || { gk_warn "已取消"; return 1; }
  local sha="${GK_PICK%% *}"

  gk_menu "選擇 reset 模式" \
    "soft  (保留索引 + 工作目錄)" \
    "mixed (預設：保留工作目錄，清索引)" \
    "hard  (丟棄所有未提交變更！危險)" \
    || { gk_warn "已取消"; return 1; }
  local mode
  case "$GK_PICK_IDX" in
    0) mode="--soft";; 1) mode="--mixed";; 2) mode="--hard";;
  esac

  if [ "$mode" = "--hard" ]; then
    gk_confirm "確定 hard reset 到 $sha？這會丟棄未提交變更" n \
      || { gk_warn "已取消"; return 1; }
  fi
  gk_git reset "$mode" "$sha" && gk_ok "已 reset ($mode) 到 $sha"
}
