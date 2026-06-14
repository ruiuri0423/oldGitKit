# gitkit — GitBackend 指令對照表(git 1.8.3.1 相容)

> 目標部署環境的系統 git 是 **1.8.3.1(2013)**。本文件是 P0 後端的實作藍圖:
> 每個 BU 動作 ↔ 實際要送出的 1.8 相容 git 指令字串,以及對應的概念說明。
> 開發機可能是新版 git(2.x),但**所有指令必須在 1.8.3.1 跑得動**。

---

## 0. 環境約束摘要

| 代號 | 約束 | 對後端的影響 |
|---|---|---|
| E1 | CentOS 7 / glibc 2.17 | 僅影響套件 wheel,不影響 git 指令 |
| E2 | 系統 git **1.8.3.1** | **本文件存在的原因**;conda 內可另裝新版 git 並存 |
| E3 | 離線 air-gap | 無關 git 指令 |
| E4 | 無 root | git 一律用使用者權限 / 指定 cwd |
| E5 | py3.9 | 無關 git 指令 |

**設計原則**:`GitBackend` 啟動時跑一次 `git --version`,建立 capability table;
指令由「版本感知的 builder」產生,未來偵測到新版 git 時自動走更佳路徑。

---

## 1. 概念詞彙表(SVN → git 認知對映)

### 三個區域(git 比 SVN 多一層)

| 區域 | 是什麼 | SVN 對應 |
|---|---|---|
| **工作區 Working tree** | 硬碟上實際編輯的檔案 | working copy |
| **索引 Index / 暫存區 Staging** | 「下一次 commit 要包含什麼」的快照;`git add` 把工作區複製進此處 | **SVN 無,全新概念** |
| **版本庫 HEAD / Repository** | 已 commit 的歷史;`HEAD` = 目前分支最後一筆 commit | repository(SVN 為中央式) |

檔案流動:工作區 ──(add)──▶ index ──(commit)──▶ 版本庫

### porcelain 狀態碼 `XY <path>`

- **X 欄 = index ↔ HEAD**(已 staged 的狀態)
- **Y 欄 = 工作區 ↔ index**(尚未 staged 的狀態)

| 碼 | 意義 | 落在哪個面板 |
|---|---|---|
| `??` | 未追蹤 | Untracked |
| ` M` | 已改、未 stage | Modified |
| `M ` | 已 stage | Staged |
| `MM` | stage 後又再改 | Staged + Modified(同檔兩態) |
| `A ` | 新增、已 stage | Staged |
| `D ` / ` D` | 刪除(已/未 stage) | Staged / Modified |
| `R ` | 改名(已 stage) | Staged |
| `UU` | 衝突(both modified) | 衝突狀態,需引導 |

### detached HEAD(脫離分支)

`HEAD` 正常指向一個**分支名**(會跟著 commit 前進)。當 checkout 一個**特定 commit sha 或 tag** 時,
`HEAD` 直接指向 commit、**不在任何分支上** → detached。此時的 commit 不屬於任何分支,切走可能遺失。
SVN 無此概念。**UI 偵測到要提示使用者開分支。**

### ahead / behind

相對 upstream(遠端追蹤分支)「本地領先幾筆 / 落後幾筆」。
數字反映**上次 `fetch` 的已知狀態**,要先 fetch 才準。

---

## 2. 安全邊界(寫死在 BU 層)

### ✅ 開放(安全、可逆或低風險)

- `add` / `reset <file>`(三階段進出)、`commit`
- **`checkout -- <file>`(≈ svn revert)**:僅限指定單/多檔,**禁止 `checkout -- .`**,需 dry-run + 確認
- `branch <new>`、`checkout <branch>`、`merge`(ff 或產生 merge commit;衝突即停)
- `fetch`、`pull --ff-only`、`push`(普通推送)
- `archive`(≈ svn export)

### ⛔ 封鎖(易毀 repo / 不可逆 / 概念過進階)

- `reset --hard`、`clean -fd`、`checkout -- .`(無差別丟棄)
- `push --force` / `--force-with-lease`、`branch -D`
- `rebase`、`cherry-pick`、`commit --amend`、`filter-branch`

### 三道防護

1. **能力白名單**:BU 只暴露 enum 化動作,UI 叫不到危險指令(後端根本沒那 method)
2. **Dry-run 預覽**:寫入前先算「將發生什麼」(push 算送幾筆、merge 試算是否 ff、revert 列將丟棄的改動)
3. **失敗即停 + 人話解釋**:攔截原始 git stderr,翻成「現在發生什麼、下一步怎麼做」

---

## 3. 共用呼叫規則(每次都套用)

```
git --no-pager -c color.ui=false <子指令> …
環境變數:GIT_PAGER=cat;設定 cwd=<repo 路徑>(不要用 git -C,1.8.3.1 無此旗標)
解析輸出盡量加 -z(NUL 分隔),避免空白/中文檔名解析錯
```

- **不用 `git -C`**(1.8.5 才有)→ subprocess `cwd=`
- **不用 pager / 顏色** → 乾淨純文字、避免卡住
- **`-z`** → `status --porcelain -z`、`diff --name-status -z`

---

## 4. BU 動作 ↔ 1.8 指令對照表

### 4.1 讀取 / 狀態(唯讀,P0/P1)

| BU 動作 | 1.8 指令 | 說明 / 地雷 |
|---|---|---|
| repo 根目錄 | `git rev-parse --show-toplevel` | — |
| 是否在 repo | `git rev-parse --is-inside-work-tree` | — |
| git 版本偵測 | `git --version` | 建 capability table |
| 目前分支 | `git rev-parse --abbrev-ref HEAD` | detached 時回字面 `HEAD` |
| 偵測 detached | `git symbolic-ref --short HEAD` | detached 時**報錯** → 據此判定 |
| 檔案狀態(三階段) | `git status --porcelain -z` | **不用 `--porcelain=v2`**(2.11);X/Y 雙欄 |
| commit 清單(Tree) | `git log --all --format='%H%x09%P%x09%an%x09%ad%x09%d%x09%s' --date=short` | **`%d` 不用 `%D`**(2.13);`%P` 拿 parents 自繪 DAG |
| 只要標頭不要 diff | `git show -s <sha>` | **`-s` 不用 `--no-patch`**(1.8.4) |

### 4.2 三階段 staging(P2)

| BU 動作 | 1.8 指令 | 說明 |
|---|---|---|
| stage 檔案 | `git add -- <file>…` | 工作區 → index |
| stage 全部 | `git add -A` | — |
| unstage 檔案 | `git reset HEAD -- <file>…` | index → 取消(不用 `restore --staged`,2.23) |
| **revert 單檔(svn revert)** | `git checkout -- <file>…` | 丟棄工作區改動;**禁 `. `**;需確認 |

### 4.3 commit(P2)

| BU 動作 | 1.8 指令 | 說明 |
|---|---|---|
| 提交 | `git commit -m "<msg>"` | 只動本地版本庫 |
| 提交前預覽 | `git diff --cached --name-status -z` | 顯示這次要 commit 的內容 |

### 4.4 分支 / 合併(P3)

| BU 動作 | 1.8 指令 | 說明 |
|---|---|---|
| 列本地分支 | `git for-each-ref refs/heads/` | 見 §5 完整格式(含 upstream/ahead-behind) |
| 列遠端分支 | `git for-each-ref refs/remotes/` | clone 後只有預設分支是本地;其餘在此。過濾 `*/HEAD` 符號指標(`%(symref)` 是 2.8+,改用「短名無 `/`」判斷)。`%(objectname:short)` 是 2.11+,改用 `%(objectname)` 自截 |
| 開新分支 | `git branch <name>` | 不切過去 |
| 開並切換 | `git checkout -b <name>` | 不用 `switch`(2.23) |
| 切換分支 | `git checkout <name>` | — |
| 合併 | `git merge <name>` | 衝突即停,進衝突引導 |
| merge 試算是否 ff | `git merge-base --is-ancestor <name> HEAD` | dry-run 判斷 |

### 4.5 遠端 fetch/pull/push(P3)

| BU 動作 | 1.8 指令 | 說明 |
|---|---|---|
| 列遠端 | `git remote -v` | — |
| 抓取 | `git fetch <remote>` | 更新遠端追蹤分支(不動工作區) |
| 安全拉取 | `git pull --ff-only` | 非 ff 即停,避免意外 merge |
| 推送 | `git push <remote> <branch>` | 普通推送 |
| push 前算送幾筆 | `git rev-list --count @{u}..HEAD` | dry-run 預覽 |

### 4.6 匯出 / 其他

| BU 動作 | 1.8 指令 | 說明 |
|---|---|---|
| **匯出乾淨樹(svn export)** | `git archive --format=tar HEAD \| tar -x -C <dir>` | 不含 `.git` |
| 打包 zip | `git archive --format=zip -o out.zip HEAD` | — |
| 暫存(整包) | `git stash save "<msg>"` | 不用 `stash push`(2.13) |
| 還原暫存 | `git stash pop` | — |

### 4.7 diff(P1/P2)

| BU 動作 | 1.8 指令 | 說明 |
|---|---|---|
| 工作區 diff | `git diff --no-color` | Y 欄(未 staged) |
| 已 staged diff | `git diff --cached --no-color` | X 欄(已 staged) |
| 某 commit 的 diff | `git show --no-color <sha>` | 展開單檔用 |
| 變更檔清單 | `git diff --name-status -z [--cached]` | 清單頁 |

---

## 5. 撈 upstream 與 ahead/behind(右欄 Remote/Local 面板)

### 主力(一行撈齊)

```
git for-each-ref \
  --format='%(refname:short)%09%(upstream:short)%09%(upstream:track)' \
  refs/heads/
```

輸出(tab 分隔):

```
main      origin/main
feature   origin/feature   [ahead 2]
exp       origin/exp       [ahead 1, behind 3]
old       origin/old       [gone]
```

- `%(upstream:track)` → `[ahead N, behind M]` / `[ahead N]` / `[behind M]` / `[gone]`(upstream 被刪)/ 空(同步)
- `%(upstream:short)` / `%(upstream:track)` 自 1.7 起可用,1.8 穩用
- **數字反映上次 `fetch` 的已知狀態,要先 fetch 才準**

### 備援(逐分支明確計算)

```
git rev-list --left-right --count <branch>...<branch>@{u}
```

- 輸出 `<ahead>\t<behind>`(左 = branch 領先、右 = upstream 領先)
- 用於 `track` 解析不穩或要精確值時

---

## 6. 版本地雷快查表

| 想用的(現代) | 引入版本 | 1.8.3.1 替代 |
|---|---|---|
| `git -C <path>` | 1.8.5 | subprocess `cwd=` |
| `git switch` | 2.23 | `git checkout <branch>` |
| `git restore <file>` | 2.23 | `git checkout -- <file>` |
| `git restore --staged` | 2.23 | `git reset HEAD -- <file>` |
| `git worktree` | 2.5 | 不可用,設計勿依賴 |
| `branch --show-current` | 2.22 | `rev-parse --abbrev-ref HEAD` |
| `status --porcelain=v2` | 2.11 | `--porcelain`(v1) |
| `stash push` | 2.13 | `stash save` |
| `%(objectname:short)` | 2.11 | `%(objectname)` 自截 / `rev-parse --short` |
| `log --format=%D` | 2.13 | `%d`(自帶 ` (...)`,需自剝) |
| `show --no-patch` | 1.8.4 | `show -s` |
| `--ahead-behind` | 2.x | `rev-list --left-right --count` |

---

## 7. 衛生旗標速記

| 旗標 | 負責 | 為何 |
|---|---|---|
| `--no-pager` | 關閉 pager(less) | 避免 subprocess 卡住 |
| `-c color.ui=false` | 關閉 ANSI 顏色 | 避免汙染解析(逐次覆寫,不動全域 config) |
| `GIT_PAGER=cat` | 環境層保險 | 雙保險 |
| `-z` | NUL 分隔、不跳脫檔名 | 含空白/中文檔名才穩 |

---

_最後更新:2026-06-12 — 對應 P0 後端規劃_
