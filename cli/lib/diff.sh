# shellcheck shell=bash
# diff.sh — `gitkit diff`: pick U/M/S files → open them in git's diff tool.

gk_cmd_diff() {
  gk_need_repo
  gk_collect_status

  local labels=() kinds=() paths=() f i
  for f in "${GK_S[@]}"; do labels+=("S  $f"); kinds+=("S"); paths+=("$f"); done
  for f in "${GK_M[@]}"; do labels+=("M  $f"); kinds+=("M"); paths+=("$f"); done
  for f in "${GK_U[@]}"; do labels+=("U  $f"); kinds+=("U"); paths+=("$f"); done

  if [ ${#labels[@]} -eq 0 ]; then
    gk_info "沒有可比較的變更"
    return 0
  fi

  gk_menu_multi "選擇要 diff 的檔案（依序開啟）" "${labels[@]}" \
    || { gk_warn "已取消"; return 1; }

  local k p
  for i in "${GK_PICK_IDXS[@]}"; do
    k="${kinds[$i]}"; p="${paths[$i]}"
    case "$k" in
      S) gk_info "diff (已暫存): $p"; gk_git difftool --staged -- "$p";;
      M) gk_info "diff (工作區):  $p"; gk_git difftool -- "$p";;
      U) gk_warn "未追蹤檔案無前一版本，略過: $p";;
    esac
  done
}
