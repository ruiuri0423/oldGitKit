# shellcheck shell=bash
# log.sh — `gitkit log`: history with changed paths, optionally for one file.
#
#   gitkit log [path] [limit]
#
# The git equivalent of `svn log -v` (-v = list changed paths): each commit is
# shown with the files it touched (A/M/D). An optional <path> filters to that
# file's history; <limit> caps the number of commits (default 20).

gk_cmd_log() {
  gk_need_repo
  local path="${1:-}" limit="${2:-20}"
  case "$limit" in ""|*[!0-9]*) limit=20;; esac

  # one readable line per commit, then its changed paths (svn log -v style)
  local fmt='tformat:%C(yellow)%h%Creset  %ad  %C(cyan)%an%Creset  %s'
  if [ -n "$path" ]; then
    gk_git log --name-status --date=short -n "$limit" --pretty="$fmt" -- "$path"
  else
    gk_git log --name-status --date=short -n "$limit" --pretty="$fmt"
  fi
}
