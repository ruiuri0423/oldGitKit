# gitkit — Code Review 導讀(TUI 渲染與後端溝通流程)

本文件給 reviewer 一條最短路徑,理解 gitkit 的分層、泳道渲染、分支建立、以及
fetch/pull/push 的流程控制。所有 `file:line` 以撰寫當下的 commit 為準,函式名稱才是穩定錨點。

---

## 0. 主要目標與分層

**產品目標**：讓 SVN 團隊「看見」git 在做什麼——三階段索引、本地 vs 遠端、分支拓樸、
detached HEAD、merge / 衝突——而不是背指令。
**部署限制**：CentOS 7 + git **1.8.3.1**（2013）、離線、無 root。每條 git 指令都必須 1.8-safe。

```
 Textual UI   (ui/app.py)          鍵位 / 面板 / Modal / 泳道渲染；只呼叫 Flow
      │  只呼叫 Flow 方法
 Flow / BU    (core/flow.py)       安全寫入邊界、dry-run 預覽、git stderr → 中文
      │  只依賴 ABC
 GitBackend ABC (backend/base.py)
      │  唯一實作
 CliGitBackend  (backend/cli_git.py)   唯一碰 subprocess / 解析 git 文字的層
      │
    git 1.8.3.1
```

**Review 判準**（守住分層）：
- UI 不該出現 `subprocess`、git 原始文字、或英文 stderr。
- Flow 不該做字串排版；它的回傳是「短狀態字串」或丟 `FlowError`（已翻成中文）。
- Backend 不該有 UI 概念；每個 git 文字格式只在這裡解析。

**資料流**：UI 的 `_load()`（`ui/app.py`）每次 reload 一次性向 Backend 取 status / log /
branches / remote_reachable，算好泳道後重建面板。換 git（或改用 libgit2）只需重寫一個
Backend，上層不動。

---

## 1. 泳道（lane）做法 — `graph/lanes.py`

核心：**first-publish 穩定泳道**——col0 永遠是「含 root 的主幹」，不是 HEAD 的分支。

### 1.1 欄位分配 — `build_layout()`

```python
main_tip = _find_main_tip(commits)                       # main/master 的 tip
index, branch_of, branches = _decompose(commits, primary_tip=main_tip)
...
primary = branch_of[main_tip] if main_tip else branch_of[root]
_assign_columns(branches, primary)                       # primary → col0
```

> **為什麼**：早期用 HEAD 當 col0，切分支整張圖會跳。改成「含 root 的 first-parent 主幹固定在
> col0」後，切分支泳道不動。`head_sha` 只用來「標記 HEAD 那列」，**不參與欄位分配**。
> 鎖在 `tests/test_lanes.py::test_trunk_is_root_chain_regardless_of_head`。

### 1.2 實心/虛線 = 是否已推到遠端

- 遠端可達（`CliGitBackend.remote_reachable()` = `git rev-list --remotes`）→ 實心 `●`/`│`。
- 本地未推 → 空心/虛線 `○`/`╎`。
- 邊界由 `_specs()` 計算：一條分支上「第一個在遠端的 commit」以上的列才虛線。
- 連接線用 bitmask（`_U/_D/_L/_R`）拼 box-drawing：`├ ┤ ┬ ┴ ┼ ╭ ╮ ╰ ╯`，見
  `_node_string()` / `_conn_string()`。

ASCII 示意（local2_test_2 與 origin/main 同源分歧）：

```
● 9ce9c0f  HEAD  main  local2_test_2     ← 實心：已在某遠端 ref
│ ● 9bbc394  origin/main
├─╯
● 81348a7  base
```

### 1.3 兩層快取 — `GraphCache`

```python
sig = (head_sha, frozenset(remote_set),
       tuple((c.sha, tuple(c.parents)) for c in commits))
if sig == self._sig: return self._lines              # 整體命中
self._lines = render_graph(..., cache=self._rows)    # 否則逐列重算、未變列重用
```

> 逐列快取 `_rows` 的 key 是 `(kind, key)` 且 `key` 含 `(lanes, col, ...)` 完整結構，
> 所以「字串是結構的純函數」，重用永遠正確（追渲染 bug 時，這層因此被排除）。

### 1.4 渲染殘影修正（await-clear）— `_repopulate()`（`ui/app.py`）

**症狀**：fast-forward merge 後 `main` 標籤不前進，按 `r` 也不動，只能重開。

**根因**：`ListView.clear()` 回傳 `AwaitRemove`、**延後**刪除子節點（clear 後 `len` 仍是舊值）。
原本 `_load` clear 後同步 append，新列疊在未刪舊列上，真實終端畫出殘影。

**修法**：Tree、三個檔案面板、difflist 全部改走 `_repopulate()`，在 worker 裡
`await listview.clear()` 再 append；`exclusive` 群組讓連續 reload 不交錯。

```python
async def rebuild():
    await listview.clear()          # 等舊節點真的移除
    for it in items:
        listview.append(it)
    if select_first and items:
        listview.index = 0
self.run_worker(rebuild(), group=f"refill-{listview.id}", exclusive=True, exit_on_error=False)
```

---

## 2. 分支建立 — UI → Flow → Backend 走一遍

### 2.1 建立並切換（按 `b`）

`action_branch` → `InputModal` → `_after_branch`（`ui/app.py`）：

```python
self.be.cmdlog.clear()
try:
    self.flow.create_branch(name)   # git branch <name>
    self.flow.checkout(name)        # git checkout <name>   ← 建立後直接切過去
except FlowError as e:
    self._record(...); self._set_status(f"⚠ {e}"); return
cmds = [c for c in self.be.cmdlog if c and c[0] in WRITE_SUBCMDS]
self._record(cmds, f"已建立並切換到 {name}", False)
self.action_reload(); self._flash_command(cmds)
```

- Flow `create_branch` / `checkout` 走 `_do()` 包安全邊界 + 中文翻譯。
- Backend `create_branch` = `git branch name`、`checkout` = `git checkout name`
  （**刻意不用 `git switch`**，2.23+，1.8 沒有）。

**三條觀察點**：
1. 寫入前 `cmdlog.clear()`，事後挑 `WRITE_SUBCMDS` 記進 `o` 指令紀錄。
2. `_flash_command` 在 Info 列閃 6 秒顯示真正執行的 git。
3. 失敗時 UI 只拿到中文 `FlowError`，看不到 stderr。

### 2.2 切換既有分支（按 `l`，Branches 懸浮視窗）

`action_branches` → `BranchesModal` → `_after_branch_pick`：選 remote 分支會去掉 `origin/`
前綴 → 建本地 tracking 分支，**不進 detached**。

---

## 3. fetch / pull / push 流程控制 — staleness guard

### 3.1 狀態分類（唯讀，放 Flow）— `upstream_state()`

```python
info = next((b for b in self.be.branches() if b.name == name), None)
if info is None or info.upstream is None or info.upstream_gone: kind = "none"
elif info.behind == 0:  kind = "ahead" if info.ahead else "current"
elif info.ahead == 0:   kind = "behind"      # 純落後 → 可 fast-forward
else:                   kind = "diverged"    # 兩邊都動 → 需真合併
```

> 資料源頭是 Backend `branches()`：`for-each-ref ... %(upstream:track)` 一次拿到每個分支的
> ahead/behind。8 個分類案例鎖在 `tests/test_staleness.py`。

### 3.2 UI 攔截器 — `_guard()`

- `current / ahead / none` → 直接 `on_proceed()`，不打擾。
- `behind` / `diverged` → 跳 `StalenessModal`，動作由各操作客製（`extra`）：

| 操作 | 落後（可 ff） | 分歧 |
|---|---|---|
| **push** (`action_push`) | `[u]` 先 pull 再 push | `[i]` 先整合再 push |
| **pull** (`action_pull`) | 直接 ff（不擋） | `[i]` 用合併整合（ff-only 不可能） |
| **merge** (`_after_merge_pick`) | `[u]` 先更新目標再合併 | 提示需先整合 |

三者都附 `[y]` 仍直接 / `[f]` fetch 重新檢查 / `Esc`。`[f]` 依設計**不自動連線**——
使用者明確按下才 `flow.fetch`，再用刷新後的數字重判，把「先 fetch 再 merge」教給使用者。

### 3.3 修復動作仍在 Flow（安全邊界 + 衝突轉中文）

`update_then_merge` / `update_then_push` / `integrate`，底層仍是 1.8-safe 的
`pull --ff-only`、`merge`。`pull()` 還會比對 HEAD 前後給「已是最新 / 前進到 xxxxxxx」。

### 3.4 串到衝突解決

`integrate` / `merge` 撞衝突 → `_run_flow()` 偵測 `is_merging()` 自動開 `ConflictModal`
（ours / theirs / manual → complete / abort）。所以「分歧 → 整合 → 衝突 → 解決」整條在工具內走得完。

---

## 4. revert(安全回退）— `action_revert` / `flow.revert`

教學定位：`revert ≠ reset`。`git revert <sha>` 產生一個**反向 commit** 抵銷舊的，
**不改寫歷史**，可安全推共享分支。對照 SVN：SVN 的 `revert` ≈ git 的 `checkout -- file`，
git 的 `revert` 是另一回事。

- **作用範圍**：目標可以是 Tree 上**任一個 commit**（不限 HEAD）；落點永遠是目前 HEAD 之上的新 commit。
- **觸發**（按 `v`，`action_revert`）：
  - 一般單父 commit → `ConfirmModal` 預覽 →`git revert --no-edit <sha>`。
  - merge commit（◆，兩個父）→ 先跳 `SelectModal` 讓使用者選 mainline（保留哪個父系），
    再 `git revert -m <n> --no-edit <sha>`。
  - detached HEAD → 拒絕（會產生 commit）。
- **衝突**：revert 撞衝突會設 `REVERT_HEAD`（非 `MERGE_HEAD`）。因此把衝突偵測一般化成
  `Backend.pending_op()`（回 `merge`/`revert`/`cherry-pick`/None）；`_open_conflict_resolver`、
  `_load` 橫幅、`ConflictModal` 標題與 ours/theirs 文案都改讀 `pending_op`。`abort()` 依
  `pending_op` 分派 `merge --abort` / `revert --abort`；`complete()` 兩者都用 `commit --no-edit`。
- **空 revert 防呆**：若把衝突全解成「我方」→ 等於沒撤銷、`commit` 會報 nothing-to-commit；
  `complete()` 先用 `diff_files(staged=True)` 偵測並給中文提示，請改按「放棄 revert」。

> Review 重點：revert 與 #3 的衝突解決天然相接——同一個 `ConfirmModal`/`SelectModal`/
> `ConflictModal` 被複用，只是依 `pending_op` 換口吻。1.8-safe：`revert --no-edit/-m/--abort` 皆可。

## 建議的看圖順序

1. `docs/architecture/layers.md` → 先確認分層與資料流（`_load` 一次取數）。
2. `graph/lanes.py`：`build_layout` → `_decompose` → `_assign_columns` → `render_graph`。
3. `ui/app.py`：`_after_branch` / `_guard` / `action_push` → UI 如何只透過 Flow 動作、並記錄/閃示指令。
4. `core/flow.py`：`upstream_state` + `update_then_*` → 流程控制與安全邊界。

相關文件：`docs/architecture/layers.md`、`docs/backend/git-1.8-command-map.md`、
`docs/ui/tree-dag-rendering.md`。
