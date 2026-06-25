# shellcheck shell=bash
# st.sh — `gitkit st`: svn-like status, the same shape as `gitkit log`.
#
#   gitkit st        → every change as "<CODE>\t<path>"
#   gitkit st -uq    → same, but hide untracked (modified/staged only)
#   gitkit st <other flags> → passed through to `git status`

gk_cmd_st() {
  gk_need_repo
  case "${1:-}" in
    "")    gk_print_status;;
    -uq)   gk_print_status no;;     # hide untracked
    *)     gk_git status "$@";;     # any other flags -> raw git status
  esac
}
