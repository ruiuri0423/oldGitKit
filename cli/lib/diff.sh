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

  # Direct file mode: `gitkit diff <file>` — no commit, no menu.
  if [ -n "$path" ]; then
    gk_info "difftool -- $path"
    gk_git difftool -- "$path"
    return
  fi

  # Menu mode over the working tree. -uq lists modified files only.
  gk_collect_status
  if [ "$uq" -eq 1 ]; then gk_build_menu "M"; else gk_build_menu "S M U"; fi

  if [ ${#GK_MENU_LABELS[@]} -eq 0 ]; then
    gk_info "no changes to compare"
    return 0
  fi

  gk_menu_multi "Select files to diff (opened in sequence)" "${GK_MENU_LABELS[@]}" \
    || { gk_warn "cancelled"; return 1; }

  local k p
  for i in "${GK_PICK_IDXS[@]}"; do
    k="${GK_MENU_KINDS[$i]}"; p="${GK_MENU_PATHS[$i]}"
    case "$k" in
      S) gk_info "diff (staged):  $p"; gk_git difftool --cached -- "$p";;
      M) gk_info "diff (working): $p"; gk_git difftool -- "$p";;
      U) gk_warn "untracked file has no previous version, skipping: $p";;
    esac
  done
}
