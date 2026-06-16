# Tree 面板:從 commit 到分支圖的解析與渲染(v3)

本文件完整說明 gitkit 的 Tree 面板如何從 `git log` 拿到的 commit 清單,一路解析成帶分支線、上色的圖。內容對應 `src/gitkit/graph/lanes.py` 與 `src/gitkit/ui/app.py`,並以「因為…所以…」的方式說明每個設計決定的理由。

---

## 0. 設計前提與總則

- **commit 不可變**:一個 commit 的 parent 永遠不變。因為歷史只會在「頂端」長出更新的 commit、舊 commit 的結構固定,所以我們能用 cache 重用「邊界以下」已建立的圖象,只重算頂端。
- **版本無關、純函式**:整條圖管線只吃 `list[Commit]`,不碰 subprocess、不碰 git 文字。因為解析(git 文字 → `Commit`)已經在 backend 內部做完,所以圖層可以離線單元測試、換底層 git 也不受影響。
- **主幹 = first-parent**:這是 git 對「主幹」的定義(merge 的第一個 parent 是你當時所在、會繼續走下去的分支)。因為要忠於 git,所以我們用 parent 的「順序」判定主幹,而不是用 commit 數量(距離)或分支名稱。距離法在跨欄情境救不了任何東西,所以已正式棄用。
- **col0 可設定**:哪條 first-parent 鏈當第 0 欄是「呈現選擇」,預設取 HEAD 所在的鏈,查不到時退而取最新 commit 的鏈。

---

## 1. 全流程呼叫鏈

```
python -m gitkit <repo>
 └ __main__.main()                         # 解析 argv
    └ ui/app.py: run() → GitkitApp.run()    # Textual 事件迴圈
       └ on_mount() → action_reload() → _load()
          ├ be.log(limit=80)               # ① git log → list[Commit](backend 已解析)
          ├ head_sha = 含 "HEAD" 裝飾的 sha  # ② 決定 col0
          ├ self._gcache.render(commits, head_sha)   # ③ 解析成圖(每列字串)── 本文重點
          └ _commit_items(lines, width)     # ④ 逐欄上色、包成 ListView item
```

第 ③ 步展開:

```
GraphCache.render
 └ render_graph
    └ _specs
       └ build_layout
          ├ _decompose       # 階段 A:切 first-parent 鏈 → 分支
          ├ _annotate        # 階段 B:找 merge/fork、定區間與配欄基準
          └ _assign_columns  # 階段 C:把每條分支放進欄(區間排程)
       └ _node_string / _conn_string   # 把每列規格畫成字串(box-drawing)
```

**只有 `Commit.parents`(第一 parent 在前)被圖用到** —— 整張圖都由 parent 反推。

---

## 2. 資料結構與用意

| 結構 | 檔案 | 用意 |
|---|---|---|
| `Commit` | core/models.py | 一個節點;`parents` 是唯一真正的邊 |
| `Branch` | graph/lanes.py | 一條 first-parent 鏈 = 一條泳道;記 `column`、區間 `[top_row,bottom_row]`、`merge_commit`/`fork_commit`/`basis_branch` |
| `branch_of` | dict | `sha → branch id`(注意:是 **id**,不是 column) |
| `branches` | dict | `id → Branch` |
| `row_of` | dict | `sha → 列號`(垂直座標) |
| `GraphCache` | graph/lanes.py | 保存已建立的圖象,未變的列重用 |

```python
@dataclass
class Branch:
    id: int
    commits: List[str] = field(default_factory=list)  # 此鏈的 sha,top(新)→bottom(舊)
    column: int = 0
    merged: bool = False
    merge_commit: Optional[str] = None   # 把這條枝的「頭」併進來的 merge(上端)
    fork_commit: Optional[str] = None    # 這條枝回到既有歷史的點(下端)
    fork_branch: Optional[int] = None
    basis_branch: Optional[int] = None   # 配欄時要貼著哪條枝
    top_row: int = 0
    bottom_row: int = 0
```

**為什麼要有 `Branch` 這層**:因為原始輸入是「一串扁平的 commit」,直接畫很難決定誰站哪一欄;所以先把它轉成「幾條有起訖區間的泳道」,之後**配欄就變成對區間做排程、畫線就變成在固定欄之間連邊**,兩者都圍著 `Branch` 的 `column` 與區間做。

> 注意 **`branch_of` 給的是 id,不是 column**。要取 column 一定是兩跳:`branches[branch_of[sha]].column`。id 由階段 A 依「發現順序」給、column 由階段 C 依「區間排程」給,兩者不保證相等(見 §5)。

---

## 3. 階段 A:`_decompose` —— 切出 first-parent 鏈

```python
def _decompose(commits):
    index = {c.sha: c for c in commits}     # sha → Commit,供 O(1) 反查
    children = _children(commits)           # 反向邊:誰被誰當 parent
    branch_of, branches, nid = {}, {}, 0
    for c in commits:                       # 由新到舊
        sha = c.sha
        # 子節點裡「把我當第一 parent」的,代表它和我同一條線
        cont = [ch for ch in children[sha]
                if index[ch].parents and index[ch].parents[0] == sha]
        if cont:
            bid = min(branch_of[ch] for ch in cont)   # 沿用最靠主幹(id 最小)那條
        else:
            bid = nid; nid += 1; branches[bid] = Branch(id=bid)   # 我是分支頭 → 開新分支
        branch_of[sha] = bid
        branches[bid].commits.append(sha)
    return index, branch_of, branches
```

- **為什麼看「子節點」而不是看自己的 parent**:因為我們由新到舊處理,輪到 `sha` 時它的子節點(更新)已經處理完、已經有 `branch_of` 了。「`ch` 的第一 parent 是 `sha`」代表 `ch` 當初就直接 commit 在 `sha` 上、同一條線,所以 `sha` 應該延續 `ch` 的分支 —— 等於沿 first-parent 邊把分支身分「由新往舊」傳下來。
- **為什麼一定要由新到舊**:`git log --topo-order` 保證 parent 不會排在 child 前面。因為 `cont` 那行要讀子節點「已分配」的 `branch_of[ch]`,所以子節點必須先處理過,這個方向是硬性前提(反過來跑會 KeyError)。
- **`branch_of` 是一邊掃一邊長大的查表**:每輪迴圈最後一行 `branch_of[sha] = bid` 才寫入。所以讀 `branch_of[ch]` 一定查得到值(子節點早就寫過)。
- **`min(...)` 為什麼是「最靠主幹」**:branch id 由 `nid` 從 0 遞增、遇到新分支才 +1,而掃描是新→舊,所以越早遇到的 tip → id 越小、主幹通常 id=0。因為 id 越小越主幹,所以一個 commit 同時被多個子節點當第一 parent 時(分岔點),`min` 就讓它跟著最主幹的那條,其餘子節點變成從這裡岔出的支幹。

---

## 4. 階段 B:`_annotate` —— 標註 merge/fork、區間、配欄基準

```python
def _annotate(commits, index, branch_of, branches):
    row_of = {c.sha: i for i, c in enumerate(commits)}   # 需要「順序」才算得出列號
    merge_of = {}
    for c in commits:
        for p in c.parents[1:]:           # 第 2+ parent = 被某 merge 帶進來
            merge_of.setdefault(p, c.sha)  # 新→舊掃,最新的 merge 先佔位

    for b in branches.values():
        head, tail = b.commits[0], b.commits[-1]
        if head in merge_of:                       # 這條枝的「頭」被 merge 了
            b.merged = True
            b.merge_commit = merge_of[head]
        tailc = index[tail]
        if tailc.parents and tailc.parents[0] in branch_of:
            b.fork_commit = tailc.parents[0]        # 尾的第一 parent 落在別條枝 = fork 點
            b.fork_branch = branch_of[b.fork_commit]
        if b.merged:                                # 配欄基準:被合併 → 貼 merge target
            b.basis_branch = branch_of[b.merge_commit]
            b.top_row = row_of[b.merge_commit] + 1
        else:                                       # 未合併 → 貼 fork 枝,上端延伸到 tip
            b.basis_branch = b.fork_branch
            b.top_row = row_of[head]
        b.bottom_row = (row_of[b.fork_commit] - 1
                        if b.fork_commit is not None else row_of[tail])
        b.bottom_row = max(b.bottom_row, b.top_row)
    return row_of, merge_of
```

- **它的真正產出是「副作用」**:`_annotate` 不畫任何線,它就地把每個 `Branch` 物件的 `merged/merge_commit/fork_commit/fork_branch/basis_branch/top_row/bottom_row` 填好。回傳的 `row_of`/`merge_of` 是內部算出來的中間表,目前下游沒有再用(`build_layout` 接了 `row_of` 又被 `_specs` 丟掉);保留只是備用,可視為可清理的懸空回傳。
- **為什麼配欄基準分兩種**:因為一條「被合併」的支幹,要貼著它 **merge 進去的那條枝**(merge target),這樣上端的 merge 邊才會相鄰、短;而「未合併」的支幹沒有 merge target,所以退而貼著它 fork 出來的枝,上端則開放延伸到它的 tip(代表進行中)。
- **為什麼只記「頭」的那一個 merge**:因為 `Branch.merge_commit` 的唯一用途是「決定這條枝站哪一欄」,而那只取決於頭怎麼接上去。一條長鏈的「中段」也可能被別的 merge 當第二 parent(例如 crossing 裡 `Merge test` 併的 `t2` 是 test-custom 鏈的中段),但中段 merge 不改變這條枝的欄位,只是多一條邊要畫 —— 所以中段 merge **不在這裡處理**,而是在渲染時逐 commit 畫(見 §6)。
- **為什麼要 `index[tail]`**:這裡手上只有 tail 這個 sha,要拿回它的 `parents`,所以用 `index`(sha→Commit)做 O(1) 反查;而 `row_of`/`merge_of` 要的是「順序/逐項掃」,所以用有序的 `commits`。兩者來源相同但解決不同的存取需求。

---

## 5. 階段 C:`_assign_columns` —— 配欄(區間排程)

```python
def _assign_columns(branches, primary):
    occ = []                                  # occ[col] = 該欄已佔的 (top,bottom) 區間清單
    def free(col, top, bottom):               # 此區間放進 col 會不會撞到已佔的
        return all(bottom < t or top > b for (t, b) in occ[col])
    def place(b, mincol):                     # 從 mincol 往右找第一個放得下的欄
        col = max(0, mincol)
        while True:
            if col >= len(occ): occ.append([])
            if free(col, b.top_row, b.bottom_row):
                occ[col].append((b.top_row, b.bottom_row)); b.column = col; return
            col += 1

    place(branches[primary], 0)               # 主幹釘欄 0
    assigned = {primary}
    remaining = [bid for bid in branches if bid != primary]
    while remaining:                          # deferral:basis 先放、子枝後放
        still, progressed = [], False
        for bid in remaining:
            b = branches[bid]
            if b.basis_branch is None:
                place(b, 0); assigned.add(bid); progressed = True
            elif b.basis_branch in assigned:
                place(b, branches[b.basis_branch].column + 1); assigned.add(bid); progressed = True
            else:
                still.append(bid)
        remaining = still
        if not progressed: break              # 防呆:解不開的環就停
    for bid in remaining:
        place(branches[bid], 0)
```

- **它在解什麼**:給每條分支一個 column,同時滿足兩個約束 —— (1) 垂直區間重疊的分支不能同欄(因為同一批列上會並存);(2) 支幹要在 `basis.column + 1` 起跳(因為要貼在母枝右邊,merge 邊才相鄰)。因為這正是「在欄上排不重疊的區間」,所以用 interval scheduling。
- **deferral 迴圈的用意**:因為一條支幹要放在 `basis.column + 1`,必須先知道 basis 的欄;所以只在「basis 已分配」時才放它,否則擱到下一輪 —— 等於把 branch 樹按「basis 先、子枝後」的拓樸序處理。
- **id ≠ column**:因為 `free` 會讓「區間不重疊」的較晚分支**重用**較內側的空欄,所以一個 id 較大的分支可能拿到較小的 column(例如上下兩條不重疊的短支幹會共用同一欄)。所以畫圖一律用 `branches[branch_of[sha]].column`,不能把 id 當 column。

---

## 6. 渲染:`_specs` + `_node_string` / `_conn_string`

`_specs` 逐列產出「規格」(一個可雜湊的 key,完全決定該列字串,所以同時當 cache key):

```python
for r in range(n):
    c = commits[r]; col = branches[branch_of[c.sha]].column
    specs.append(("n", (active_cols(r), col, c.is_merge, width), c))   # 節點列
    moves = []
    for p in c.parents[1:]:                       # ★ 每個 commit 的每個第 2+ parent 都畫
        tcol = branches[branch_of[p]].column      #   → merge 邊(含併到鏈中段的情況)
        if tcol != col: moves.append((col, tcol))
    for b in branches.values():                   # 分支區間底部 → fork 枝
        if b.fork_branch is not None and b.bottom_row == r:
            fcol = branches[b.fork_branch].column
            if b.column != fcol: moves.append((b.column, fcol))
    if moves:
        specs.append(("c", (active_cols(r) & active_cols(r+1), frozenset(moves), width), None))
```

- **為什麼 merge 邊要逐 commit 掃 `parents[1:]`**:因為一個 merge 的第二 parent 可能落在某條既有泳道的「中段」(不是分支頭),如果只在分支頭 spawn 就會漏畫(這正是先前 `Merge test → t2` 漏掉的 bug)。改成「每個 commit 的每個第 2+ parent 都連到那個 parent 所在的欄」,所有 merge 邊(頭/中段)就都畫得到。

節點列與連接列的字串:

```python
def _node_string(active_cols, col, is_merge, width, node, merge, vline):
    cells = [" "] * (2 * width - 1)
    for x in active_cols: cells[2*x] = vline      # 覆蓋此列的泳道畫 │
    cells[2*col] = merge if is_merge else node    # 自己畫 ● / ◆
    return "".join(cells).rstrip()

def _conn_string(both, moves, width):             # 連接列:上下左右連通遮罩
    mask = [0] * (2*width - 1)
    for cc in both: mask[2*cc] |= _U | _D          # 直穿的泳道
    for f, t in moves:
        lo, hi = sorted((f, t))
        mask[2*lo] |= _R; mask[2*hi] |= _L
        for i in range(2*lo+1, 2*hi): mask[i] |= _L | _R   # 水平段(穿過泳道 → ┼)
        mask[2*f] |= _U; mask[2*t] |= _D            # 起點接上、終點接下
    return "".join(_GLYPH.get(m, " ") for m in mask).rstrip()
```

- **為什麼用「連通遮罩 + box-drawing」而不是 `\` `/`**:因為固定欄位下,跨欄的邊一定會穿過中間的直線泳道;用 `\`/`/` 會讓線在相鄰格子斷開(`│\│\`,有斷裂感)。改成記錄每格的上下左右連通方向、再對應 `│─├┤┬┴┼╭╮╰╯`,跨欄邊就成為連續線(例如 `├─┼─╮`),穿過泳道處用 `┼` 表示交叉。
- **為什麼連接列只在「有 moves」時才產生**:因為純直穿的泳道,上下兩個節點列的 `│` 自然對齊、不需要中間列;所以只有出現 spawn/converge(斜線)時才插一列。

---

## 7. cache:`GraphCache`

```python
class GraphCache:
    def render(self, commits, head_sha=None):
        sig = (head_sha, tuple((c.sha, tuple(c.parents)) for c in commits))
        if sig == self._sig:                 # 整體簽章未變 → 直接回上次結果
            return self._lines
        self._lines = render_graph(commits, head_sha=head_sha, cache=self._rows)
        self._sig = sig
        return self._lines
```

`render_graph` 在產每列時以 `(kind, key)` 查/存 `self._rows`:

```python
ck = (kind, key)
s = cache[ck] if ck in cache else _build_string(...)   # 命中就重用,否則建好再存
```

- **為什麼能安全重用**:因為一列的字串只由它的「結構 key」(該列有哪些欄、節點在哪、有哪些 moves)決定,而非由 sha/訊息決定;所以結構未變的列字串一定相同。又因為 commit 不可變、更新只發生在頂端,所以頂端長出新 commit 時,絕大多數舊列的 key 不變、直接命中 cache,只有頂端少數列要重建。

---

## 8. 上色:`_append_graph`(逐格顏色圖 / whole-edge 單色)

```python
LANE_COLORS = ["bright_cyan", "bright_magenta", "bright_green", "bright_yellow",
               "bright_blue", "bright_red", "cyan", "magenta"]   # 8 色循環

def _append_graph(text, graph, color=None):
    for i, ch in enumerate(graph):
        if ch == " ": text.append(" "); continue
        ci = color[i] if (color and 0 <= i < len(color) and color[i] >= 0) else i // 2
        text.append(ch, style=LANE_COLORS[ci % len(LANE_COLORS)])
```

- **規則**:渲染層(`_node_string` / `_conn_string`)除了字串,還輸出一份**逐格顏色欄位圖 `color[]`**;第 `i` 格用 `LANE_COLORS[color[i] % 8]` 上色。`render_graph` / `GraphCache` 因此回 `(graph, color, commit)` 三元組。
- **直線泳道**:`color[i] = 該格所在欄`,所以一條 branch 整段同色、好追(和舊版位置上色一致)。
- **連接邊 = 整條同色(已實作,2026-06 review)**:一條 merge/fork 邊整段用它的**來源欄顏色**(`_conn_string` 對該 move 的所有格 `color[i] = f`),包含跨過別的 `│` 變成的 `┼`。所以 `├─┼─╮` 是**一個連續顏色**,可沿色追到目標 —— 取代了舊版「邊被經過的每欄分段染色」的副作用。代價:`┼` 那一格的直線會被邊色蓋過一格(可接受的小缺口)。
- **尚未做**:跨很多欄的長邊仍是**單列**水平拉過去(見檔頭 §「TEMP v3」)。多列**階梯式 staircase**會讓邊逐列下降、更接近 `git log --graph`,但那需要動到「固定泳道」的核心假設,**刻意延後**;在 whole-edge 單色之後,長邊的可讀性已大幅改善,staircase 列為非必要。

---

## 9. 完整範例走一遍

輸入(新→舊),`M` 是 merge,第一 parent `A`(主幹)、第二 `B`(支幹):

```
M  parents [A, B]
A  parents [base]
B  parents [base]
base parents []
```

- **階段 A**:`branch_of = {M:0, A:0, B:1, base:0}`。M 無子→branch0;A 是 M 的第一 parent→續 branch0;B 不是→開 branch1;base 是 A、B 的第一 parent,`min(0,1)=0`→續 branch0。得 branch0(主幹)`[M,A,base]`、branch1 `[B]`。
- **階段 B**:branch1 `merged=True, merge=M, fork=base, basis=0`,區間 `[1,2]`;branch0 `basis=None`,區間 `[0,3]`。
- **階段 C**:branch0→col0;branch1 basis=0→col1。
- **渲染**:

```
◆        M       branch0(merge);其第二 parent B 岔到 col1
├─╮
● │      A
│ ●      B
├─╯              B 收束回 col0(fork=base)
●        base
```

**一句話**:`git log` 的 parent 順序 →(A 分解)first-parent 鏈=泳道 →(B 標註)merge/fork/區間/基準 →(C 配欄)merge-target 區間排程 →(渲染)逐 commit 畫所有 merge/fork 邊、box-drawing 連續線 →(cache)未變的列重用 →(上色)按欄上色。全程純函式、版本無關、只吃 `list[Commit]`。

_最後更新:2026-06-14 — v3 branch-tree pipeline_
