# gitkit (bash CLI)

A simplified, **pure-bash** git workflow helper — no Python, no TUI, no
泳道圖/INFO diff. It wraps the handful of commands a day-to-day SVN→git
migration actually needs, behind numbered-menu prompts.

Targets **git 1.8.3.1 / bash 4.2 on CentOS 7** (air-gapped, no root): zero
external dependencies, no `git -C`, no porcelain v2.

## Install

```sh
# put cli/ somewhere stable, then expose `gitkit` on PATH:
ln -s /path/to/cli/gitkit ~/bin/gitkit     # or: export PATH="$PATH:/path/to/cli"
```

`gitkit` resolves its own `lib/` relative to the (symlink-followed) script, so
the symlink works from anywhere.

## Commands

| command | flow |
|---------|------|
| `gitkit ci`    | 選 U/M 檔案 → `add` → 輸入訊息 `commit` → 選分支 `fetch`/merge 整合（含衝突處理）。無變更時提示「無文件變更」。 |
| `gitkit push`  | 有 upstream 但 0 領先 → 提示先 `ci`；新分支（無 upstream）→ 詢問是否先 `mg`，否則直接 `push -u`。 |
| `gitkit mg`    | 把**當前分支**合併進選定的目標分支；目標只在遠端時先 `fetch` + `checkout -b` 取回本地。完成後提示可 `push`。 |
| `gitkit diff`  | 選 U/M/S 檔案，依序用 git 設定的 `difftool` 開啟（未追蹤檔案略過）。 |
| `gitkit reset` | 取消暫存檔案（`reset HEAD -- files`），或把分支重置到某個 commit（`--soft`/`--mixed`/`--hard`，hard 需確認）。 |

## 衝突解決選項

當 `ci` / `mg` 整合產生衝突時，對**所有衝突檔案**選一種處理：

| 選項 | 意義 | 底層 |
|------|------|------|
| `tf` | their full — 整個檔案改用對方版本 | `git checkout --theirs` |
| `mf` | mine full — 整個檔案保留我方版本  | `git checkout --ours` |
| `tc` | their conflict — 只在衝突區塊取對方，保留自動合併的部分 | 衝突標記解析（awk） |
| `mc` | mine conflict — 只在衝突區塊取我方 | 衝突標記解析（awk） |
| `e`  | edit — 依序開啟 git 設定的 mergetool | `git mergetool` |
| `r`  | resolved — 標記為已解決並完成（仍含標記者會擋下） | `git add` |
| `a`  | abort — 放棄整個合併 | `git merge --abort` |

`tc`/`mc` 支援 diff3 衝突樣式（`|||||||` base 區段會被丟棄）。

## 選取介面

零依賴：列出帶編號的清單，輸入編號。多選以空格分隔（如 `1 3 5`），`a`
全選，直接 Enter 取消。

## diff / merge 工具設定

`diff` 與衝突 `e` 走 git 標準設定，請先設定：

```sh
git config --global diff.tool  <tool>
git config --global merge.tool <tool>
```

## 測試

```sh
bash cli/tests/run.sh
```

建構臨時 repo、以管線餵入選單答案來驗證 `ci`/`push`/`mg`/`reset` 與衝突解析
（互動式的 `e`/difftool 不在自動測試範圍）。
