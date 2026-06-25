# shellcheck shell=bash
# st.sh — `gitkit st`: thin wrapper over `git status`.
#
#   gitkit st        → git status
#   gitkit st -uq    → git status -uno   (modified/staged only, hide untracked)

gk_cmd_st() {
  gk_need_repo
  case "${1:-}" in
    "")    gk_git status;;
    -uq)   gk_git status -uno;;     # only list modified (suppress untracked)
    *)     gk_git status "$@";;     # pass through any other git status args
  esac
}
