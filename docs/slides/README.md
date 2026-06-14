# gitkit 使用說明投影片

兩種瀏覽方式：

- **桌機**：用瀏覽器打開 **`index.html`** 播放（方向鍵 / 空白鍵切換，或點左右半邊翻頁）。
- **手機**：直接看 **`gitkit-slides.pdf`**（16 頁、16:9，每張投影片一頁，深色主題）。

所有畫面都是 gitkit 實際 TUI 的截圖（`img/*.svg`），不需任何相依套件或網路。

> PDF 由 `index.html` 用無頭 Chrome 列印產生（`--headless --print-to-pdf`，CSS 以
> `print-color-adjust: exact` 保留深色背景）。改了投影片後重印即可更新。

## 想自己動手跑同一組 demo？

投影片的情境來自一組可重建的 fixture（commit / branch / merge / 分歧 / 衝突 / revert）：

```
.fixtures/remote.git      共用 bare 遠端
.fixtures/local_user1     alice
.fixtures/local_user2     bob   ← master 與遠端已分歧、同一行有衝突
```

> `.fixtures/` 不納入版控（內含巢狀 git repo）。如需重建，依投影片「Demo 環境」一頁的劇情步驟即可。

開工具：

```sh
python -m gitkit .fixtures/local_user1     # alice：乾淨主線，可玩 branch / merge / revert
python -m gitkit .fixtures/local_user2     # bob：分歧狀態，可玩 push/pull 防呆與衝突解決
```
