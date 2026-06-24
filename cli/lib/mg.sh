# shellcheck shell=bash
# mg.sh — `gitkit mg`: merge the current branch INTO a chosen target branch.

gk_cmd_mg() {
  gk_need_repo
  local src; src="$(gk_current_branch)" || gk_die "detached HEAD，無法 mg"
  gk_info "來源分支（將被合併進目標）: $src"

  gk_pick_branch "選擇要合併到的目標分支" || { gk_warn "已取消"; return 1; }
  local kind="$GK_BR_KIND" ref="$GK_BR_REF" remote="$GK_BR_REMOTE" target

  if [ "$kind" = "R" ]; then
    target="${ref#*/}"
    if gk_git show-ref --verify -q "refs/heads/$target"; then
      gk_info "切換到本地 $target 並更新…"
      gk_git checkout "$target" || return 1
      gk_git fetch "$remote" "$target" \
        && gk_git merge --ff-only "$remote/$target" 2>/dev/null \
        || gk_warn "無法 fast-forward $target（將以現況合併）"
    else
      gk_info "目標僅在遠端，取回到本地並切換…"
      gk_git fetch "$remote" "$target" || return 1
      gk_git checkout -b "$target" "$remote/$target" || return 1
    fi
  else
    target="$ref"
    gk_git checkout "$target" || return 1
  fi

  [ "$target" = "$src" ] && gk_die "目標與來源相同，無需合併"

  if gk_git merge --no-edit "$src"; then
    gk_ok "已將 $src 合併進 $target（無衝突）"
  else
    if gk_git diff --name-only --diff-filter=U | grep -q .; then
      gk_resolve_conflicts || { gk_warn "mg 已中止"; return 2; }
      gk_finish_merge
      gk_ok "已將 $src 合併進 $target（衝突已解決）"
    else
      gk_die "合併失敗（非衝突錯誤）"
    fi
  fi
  gk_info "完成，可執行 gitkit push 推送 $target"
}
