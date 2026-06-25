# shellcheck shell=bash
# diff.sh — `gitkit diff`: open changes in git's configured difftool.
#
#   gitkit diff                 pick from U/M/S, working tree vs index
#   gitkit diff -uq             same, but only list modified files
#   gitkit diff <commit> [path]        working tree vs <commit>  (svn diff -r)
#   gitkit diff <commitA> <commitB> [path]   <commitA> vs <commitB>
# A path that exists on disk is treated as a path; otherwise an arg that
# resolves to a commit is a commit. Up to two commits, one path.

gk_cmd_diff() {
  gk_need_repo

  local uq=0 path="" a
  local commits=()
  for a in "$@"; do
    case "$a" in
      -uq) uq=1; continue;;
    esac
    if [ -e "$a" ]; then
      path="$a"
    elif [ ${#commits[@]} -lt 2 ] && gk_git rev-parse --verify -q "$a^{commit}" >/dev/null 2>&1; then
      commits+=("$a")
    else
      path="$a"
    fi
  done

  # Commit mode: diff against a commit (or between two), optional path.
  if [ ${#commits[@]} -ge 1 ]; then
    gk_info "difftool ${commits[*]}${path:+  -- $path}"
    if [ ${#commits[@]} -eq 2 ]; then
      if [ -n "$path" ]; then gk_git difftool "${commits[0]}" "${commits[1]}" -- "$path"
      else                    gk_git difftool "${commits[0]}" "${commits[1]}"; fi
    else
      if [ -n "$path" ]; then gk_git difftool "${commits[0]}" -- "$path"
      else                    gk_git difftool "${commits[0]}"; fi
    fi
    return
  fi

  # Menu mode over the working tree.
  gk_collect_status
  local labels=() kinds=() paths=() i
  for i in "${!GK_S[@]}"; do
    [ "$uq" -eq 1 ] && continue
    labels+=("$(gk_lbl "${GK_Sc[$i]}" "${GK_S[$i]}")"); kinds+=("S"); paths+=("${GK_S[$i]}")
  done
  for i in "${!GK_M[@]}"; do
    labels+=("$(gk_lbl "${GK_Mc[$i]}" "${GK_M[$i]}")"); kinds+=("M"); paths+=("${GK_M[$i]}")
  done
  for i in "${!GK_U[@]}"; do
    [ "$uq" -eq 1 ] && continue
    labels+=("$(gk_lbl "?" "${GK_U[$i]}")"); kinds+=("U"); paths+=("${GK_U[$i]}")
  done

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
