# shellcheck shell=bash
# up.sh — `gitkit up`: update the current branch from its upstream (no commit,
# no push). The sync-only part of ci:
#   stash leftover edits -> fetch + merge upstream -> stash pop (conflict loop).

gk_cmd_up() {
  gk_need_repo
  local br up remote
  br="$(gk_current_branch)" || gk_die "detached HEAD, cannot up"
  up="$(gk_git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null)" \
    || gk_die "no upstream configured for '$br' (use gitkit ci to pick a branch)"
  remote="${up%%/*}"

  gk_info "updating $br from $up ..."
  gk_pull_with_stash "R" "$up" "$remote"
  case $? in
    0) gk_ok "$br is up to date with $up";;
    2) gk_warn "up aborted";;
    *) gk_err "up failed";;
  esac
}
