# shellcheck shell=bash
# exp.sh — `gitkit exp`: export a tracked folder WITHOUT the .git metadata.
#
#   gitkit exp <path> [dest]
#
# <path> is the version-controlled folder/file to extract. <dest> receives the
# folder's CONTENTS directly (flat) — not nested under <path>. When <dest> is
# omitted it is built from <path> by appending "_exp" (e.g. src/app -> src/app_exp).
# Folders go through `git archive HEAD:<path>` + unzip; a single file is written
# straight out with `git show`.

gk_cmd_exp() {
  gk_need_repo

  local path="${1:-}" dest="${2:-}"
  if [ -z "$path" ]; then
    printf 'Tracked folder/file to export (path in HEAD): ' >&2
    IFS= read -r path || path=""
  fi
  [ -z "$path" ] && { gk_warn "no path given"; return 1; }
  path="${path%/}"                       # strip a trailing slash

  local typ
  typ="$(gk_git cat-file -t "HEAD:$path" 2>/dev/null)" || typ=""
  case "$typ" in
    tree|blob) : ;;
    *) gk_die "'$path' not found in HEAD";;
  esac
  if [ "$typ" = "tree" ]; then          # folders need unzip; single files don't
    command -v unzip >/dev/null 2>&1 || gk_die "unzip not found (required for exp)"
  fi

  # dest is derived from path unless given explicitly.
  if [ -z "$dest" ]; then
    local def="${path}_exp"
    printf 'Destination folder [%s]: ' "$def" >&2
    IFS= read -r dest || dest=""
    [ -z "$dest" ] && dest="$def"
  fi

  if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
    gk_confirm "'$dest' exists and is not empty; extract into it (may overwrite)?" n \
      || { gk_warn "cancelled"; return 1; }
  fi
  mkdir -p "$dest"

  if [ "$typ" = "blob" ]; then
    gk_info "exporting file HEAD:$path ..."
    gk_git show "HEAD:$path" > "$dest/$(basename "$path")" || { gk_err "export failed"; return 1; }
  else
    local zip; zip="$(mktemp)" || return 1
    gk_info "archiving HEAD:$path ..."
    if ! gk_git archive --format=zip -o "$zip" "HEAD:$path"; then
      gk_err "git archive failed"; rm -f "$zip"; return 1
    fi
    if ! unzip -o -q "$zip" -d "$dest"; then
      gk_err "unzip failed"; rm -f "$zip"; return 1
    fi
    rm -f "$zip"
  fi
  gk_ok "exported '$path' -> '$dest/' (no .git)"
}
