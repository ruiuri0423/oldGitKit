# shellcheck shell=bash
# exp.sh — `gitkit exp`: export a tracked folder WITHOUT the .git metadata.
#
#   gitkit exp <path> [dest]
#
# Mirrors:  git archive -o tmp.zip HEAD -- <path>
#           unzip tmp.zip -d <dest>
#           rm tmp.zip
# <path> is the version-controlled folder/file to extract; <dest> is where the
# extracted copy lands (as <dest>/<path>/...). Requires `unzip`.

gk_cmd_exp() {
  gk_need_repo
  command -v unzip >/dev/null 2>&1 || gk_die "unzip not found (required for exp)"

  local path="${1:-}" dest="${2:-}"
  if [ -z "$path" ]; then
    printf 'Tracked folder/file to export (path in HEAD): ' >&2
    IFS= read -r path || path=""
  fi
  [ -z "$path" ] && { gk_warn "no path given"; return 1; }
  path="${path%/}"                       # strip a trailing slash

  if [ -z "$dest" ]; then
    local def; def="$(basename "$path")_exp"
    printf 'Destination folder [%s]: ' "$def" >&2
    IFS= read -r dest || dest=""
    [ -z "$dest" ] && dest="$def"
  fi

  if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
    gk_confirm "'$dest' exists and is not empty; extract into it (may overwrite)?" n \
      || { gk_warn "cancelled"; return 1; }
  fi

  local zip
  zip="$(mktemp)" || return 1
  gk_info "archiving HEAD:$path ..."
  if ! gk_git archive --format=zip -o "$zip" HEAD -- "$path"; then
    gk_err "git archive failed (is '$path' tracked in HEAD?)"
    rm -f "$zip"; return 1
  fi
  mkdir -p "$dest"
  if ! unzip -o -q "$zip" -d "$dest"; then
    gk_err "unzip failed"
    rm -f "$zip"; return 1
  fi
  rm -f "$zip"
  gk_ok "exported '$path' -> '$dest/$path' (no .git)"
}
