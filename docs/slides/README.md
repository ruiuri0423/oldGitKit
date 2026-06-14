# gitkit 使用說明投影片

直接用瀏覽器打開 **`index.html`** 即可播放（方向鍵 / 空白鍵切換，或點左右半邊翻頁）。
所有畫面都是 gitkit 實際 TUI 的截圖（`img/*.svg`），投影片本身不需任何相依套件或網路。

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
