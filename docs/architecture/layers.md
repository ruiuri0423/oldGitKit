# gitkit — 分層架構與 GitBackend 介面合約

> 本文件把前面兩份藍圖(指令對照表、DAG 演算法)收口成程式介面。
> 對應 git 1.8.3.1 相容環境。相關:docs/backend/git-1.8-command-map.md、docs/ui/tree-dag-rendering.md

---

## 0. 核心決策

**Backend = 統一語意介面(ABC)+ 自己負責解析。**

- 解析屬於 backend,因為**文字格式是「版本/實作」相關**的。
- 換底層 git(或換 libgit2)→ 只改對應 backend 實作,**上層完全不動**。
- ABC 是「語意合約」,不是「文字管道」:method 回 **dataclass**,不是原始 stdout。

```
  backend 邊界 = git 文字的「最後一哩」
  ─────────────────────────────────────────────
  邊界以下:髒、版本相關、subprocess + 解析   ← 換 git 只改這裡
  邊界以上:全是 dataclass,版本無關          ← 永遠不動
```

---

## 1. 四層職責

```
┌─ TUI (Textual) ───────────── 畫面 + 按鍵。只認「動作」與顯示用 dataclass,
│                               完全不碰 git 字串。 按 c → flow.commit(...)
│        ↓ 呼叫語意化動作 / 收 dataclass
├─ Flow interface (BU) ─────── 多步驟流程編排 + 安全邊界 + 錯誤翻人話 + 狀態。
│                               commit() = 取 staged 預覽→確認→執行→刷新
│        ↓ 呼叫單一語意操作(ABC method)
├─ GitBackend (ABC + 實作) ── 統一語意合約。實作內部:版本感知組指令、設 cwd、
│   └ 解析 helpers(版本相關)   關 pager/顏色、跑 subprocess、**解析文字 → dataclass**
│        ↓
└─ subprocess → 系統 PATH 的 git 1.8.3.1

  ※ 純運算 helpers(DAG 泳道、ahead/behind 排版、格式化)= 版本無關,
    放在 backend 之上,吃 dataclass,由 Flow/TUI 共用。
```

**一句話分工**:TUI 管「看」、Flow 管「怎麼一步步安全地做」、Backend 管「叫 git 並翻成 dataclass」。

**鐵律**:
- **只有 GitBackend 實作 import subprocess**,上層全部不准 → Flow/純 helpers 可離線單測。
- **安全邊界 + dry-run 預覽放 Flow**(換前端也守得住);Backend 只忠實執行。
- **解析在 Backend 內部**(版本相關);**DAG/格式化在上層**(版本無關)。

---

## 2. 資料模型(dataclass)

> py3.9:檔案開頭加 `from __future__ import annotations`,才能用 `list[...]` / `X | None` 標註。

```python
from dataclasses import dataclass, field

# 三階段檔案狀態(對應 porcelain X/Y)
@dataclass
class FileEntry:
    path: str
    index_status: str        # X 欄:index↔HEAD,如 'M' 'A' 'D' 'R' ' '
    worktree_status: str     # Y 欄:工作區↔index,如 'M' 'D' '?' ' '
    orig_path: str | None = None   # 改名前路徑(R 時)
    @property
    def category(self) -> str:     # 'untracked' | 'modified' | 'staged'
        ...

# Tree 節點(DAG 排版的輸入)
@dataclass
class Commit:
    sha: str
    short_sha: str
    parents: list[str]       # 多個 = merge;空 = root
    refs: list[str]          # 裝飾:分支/tag 名(已從 %d 剝掉括號)
    author: str
    date: str                # YYYY-MM-DD
    subject: str

# 右欄分支面板
@dataclass
class BranchInfo:
    name: str
    upstream: str | None     # 如 'origin/main';None = 無 upstream
    ahead: int = 0
    behind: int = 0
    is_current: bool = False
    upstream_gone: bool = False   # [gone]

@dataclass
class Remote:
    name: str                # 'origin'
    url: str

# diff 清單頁
@dataclass
class DiffFile:
    path: str
    added: int
    removed: int
    status: str              # 'M' 'A' 'D' 'R'

# repo 整體狀態(一次刷新給 TUI)
@dataclass
class RepoState:
    root: str
    current_branch: str | None    # None = detached
    detached: bool
    head_sha: str
    files: list[FileEntry] = field(default_factory=list)

@dataclass
class Capabilities:
    version: tuple[int, int, int]    # (1, 8, 3)
    has_dash_C: bool
    has_switch_restore: bool
    has_porcelain_v2: bool
    # …由 `git --version` 推導,決定 builder 走哪條路
```

---

## 3. GitBackend ABC(語意合約)

```python
from abc import ABC, abstractmethod

class GitBackend(ABC):

    # ── 能力 / repo 基本 ───────────────────────────
    @abstractmethod
    def capabilities(self) -> Capabilities: ...
    @abstractmethod
    def is_repo(self) -> bool: ...
    @abstractmethod
    def repo_root(self) -> str: ...

    # ── 讀取 / 狀態(唯讀) ─────────────────────────
    @abstractmethod
    def repo_state(self) -> RepoState: ...
    @abstractmethod
    def current_branch(self) -> str | None: ...   # None = detached
    @abstractmethod
    def is_detached(self) -> bool: ...
    @abstractmethod
    def status(self) -> list[FileEntry]: ...
    @abstractmethod
    def log(self, *, limit: int = 200, skip: int = 0,
            all_refs: bool = True) -> list[Commit]: ...
    @abstractmethod
    def branches(self) -> list[BranchInfo]: ...    # 含 upstream/ahead/behind
    @abstractmethod
    def remotes(self) -> list[Remote]: ...

    # ── diff ──────────────────────────────────────
    @abstractmethod
    def diff_files(self, *, staged: bool = False) -> list[DiffFile]: ...
    @abstractmethod
    def diff_text(self, *, staged: bool = False) -> str: ...   # 展開單檔用原始 patch
    @abstractmethod
    def show_text(self, sha: str) -> str: ...

    # ── 三階段 / commit(安全寫入) ─────────────────
    @abstractmethod
    def stage(self, paths: list[str]) -> None: ...
    @abstractmethod
    def unstage(self, paths: list[str]) -> None: ...
    @abstractmethod
    def discard(self, paths: list[str]) -> None: ...   # ≈ svn revert;禁 '.'(由 Flow 把關)
    @abstractmethod
    def commit(self, message: str) -> Commit: ...

    # ── 分支 / 合併 ───────────────────────────────
    @abstractmethod
    def create_branch(self, name: str) -> None: ...
    @abstractmethod
    def checkout(self, name: str) -> None: ...
    @abstractmethod
    def can_fast_forward(self, name: str) -> bool: ...  # merge dry-run
    @abstractmethod
    def merge(self, name: str) -> "MergeResult": ...    # 衝突→回 conflicts 清單

    # ── 遠端 ──────────────────────────────────────
    @abstractmethod
    def fetch(self, remote: str) -> None: ...
    @abstractmethod
    def pull_ff_only(self, remote: str) -> None: ...
    @abstractmethod
    def push_preview(self) -> int: ...                  # dry-run:將送幾筆
    @abstractmethod
    def push(self, remote: str, branch: str) -> None: ...

    # ── 匯出 / 暫存 ───────────────────────────────
    @abstractmethod
    def archive(self, dest_dir: str, ref: str = "HEAD") -> None: ...  # ≈ svn export
    @abstractmethod
    def stash_save(self, message: str = "") -> None: ...
    @abstractmethod
    def stash_pop(self) -> None: ...
```

對照 docs/backend/git-1.8-command-map.md 的「BU 動作」表:每個 method ↔ 一條 1.8 指令字串,
解析在實作內部(如 `CliGit18Backend`)用版本相關 helpers 完成。

---

## 4. 錯誤處理約定

- Backend 失敗 → **raise 自訂例外**(`BackendError` 子類),帶原始 stderr。
- **Flow 攔截例外 → 翻成人話**(「目前沒有已暫存的檔案…」)再給 TUI,不把英文 stderr 丟使用者。
- 衝突等「非錯誤但需引導」的狀態 → 用回傳值表達(如 `MergeResult.conflicts`),不用例外。

```python
class BackendError(Exception):
    def __init__(self, msg: str, *, argv: list[str], stderr: str): ...

class MergeResult:
    ok: bool
    fast_forward: bool
    conflicts: list[str]   # 衝突檔清單;非空 → Flow 進衝突引導
```

---

## 5. 資料流範例:按 c commit

```
① TUI        按 c → flow.commit(message="fix crossbar")
② Flow(BU)  a. files = backend.diff_files(staged=True)   ← 拿要 commit 的內容
             b. 顯示預覽,等使用者確認
             c. 安全邊界檢查(commit ✓)
             d. backend.commit(message)
             e. 失敗 → 捕 BackendError → 翻人話
             f. state = backend.repo_state(); commits = backend.log()
                → 回新 dataclass 給 TUI 重繪
③ Backend    commit(): argv=["git","--no-pager","-c","color.ui=false","commit","-m",msg]
             run(argv, cwd=self.root) → 解析 → 回 Commit
             (subprocess 只在這層)
```

---

## 6. 實作結構對映

```
src/gitkit/
├ core/
│  ├ models.py      # 本文件 §2 的 dataclass
│  └ flow.py        # Flow interface(BU):編排 + 安全邊界 + 錯誤翻譯
├ backend/
│  ├ base.py        # 本文件 §3 的 GitBackend ABC + §4 例外
│  ├ cli_git.py     # CliGit18Backend:subprocess + 版本相關解析 helpers
│  └ capabilities.py# git --version → Capabilities
├ graph/
│  └ lanes.py       # DAG 泳道演算法(吃 list[Commit],版本無關)
└ ui/               # Textual widgets
```

---

_最後更新:2026-06-13 — P0 後端動工前的介面定案_
