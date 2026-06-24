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
    gk_info "no changes to compare"
    return 0
  fi

  gk_menu_multi "Select files to diff (opened in sequence)" "${labels[@]}" \
    || { gk_warn "cancelled"; return 1; }

  local k p
  for i in "${GK_PICK_IDXS[@]}"; do
    k="${kinds[$i]}"; p="${paths[$i]}"
    case "$k" in
      S) gk_info "diff (staged):  $p"; gk_git difftool --cached -- "$p";;
      M) gk_info "diff (working): $p"; gk_git difftool -- "$p";;
      U) gk_warn "untracked file has no previous version, skipping: $p";;
    esac
  done
}
