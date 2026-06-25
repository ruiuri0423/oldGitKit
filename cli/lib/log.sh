# shellcheck shell=bash
# log.sh — `gitkit log`: history with changed paths, optionally for one file.
#
#   gitkit log [limit] [path]
#
# The git equivalent of `svn log -v` (-v = list changed paths): each commit is
# shown with the files it touched (A/M/D), followed by a `----` separator.
# A numeric arg is the limit (default 20); a non-numeric arg is the path. Either
# order works, so `gitkit log 10 file`, `gitkit log file`, `gitkit log 10` all do
# the obvious thing. No colour.

gk_cmd_log() {
  gk_need_repo
  local limit=20 path="" a
  for a in "${1:-}" "${2:-}"; do
    [ -z "$a" ] && continue
    case "$a" in
      *[!0-9]*) path="$a";;     # has a non-digit -> path
      *)        limit="$a";;    # all digits     -> limit
    esac
  done

  local fmt='tformat:%h  %ad  %an  %s'
  {
    if [ -n "$path" ]; then
      gk_git log --no-color --name-status --date=short -n "$limit" --pretty="$fmt" -- "$path"
    else
      gk_git log --no-color --name-status --date=short -n "$limit" --pretty="$fmt"
    fi
  } | awk 'BEGIN{f=1}
    /^[0-9a-f]+  / { if(!f) print "\n----"; f=0 }
    { print }
    END { if(!f) print "\n----" }'
}
