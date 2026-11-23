# 03 溝通

## 規則

### 跨代理訊息（`send_message` / Swarm 留言板 `post_message`）

1. **只在三種情況發訊息**：gate 完成交件 `TASK REPORT`、卡住需要決定、要對方立刻改行動（總監發工單、裁決、`PLAN_APPROVED` 亦屬此類）。
2. **不回 ACK**：不發「收到」「開始了」。收到就做，進度寫進 `RESUME_STATE.md`。
3. **一次完整**：給同一人的指示集中一則；只為避免錯誤才立刻補發更正。
4. **狀態寫檔**：進度寫 `RESUME_STATE.md` 與 `DIRECTOR_LOG.md`，總監定期讀檔。
5. **等別人做完**：結束本輪讓系統喚醒，或用 Swarm `swarm_wait`（休眠零 token）；不要輪詢 `read_board` 或狂讀 log（`poll_hint` 會警示）。
6. 通知多人寫共用檔或 Swarm 對應頻道，避免無關 `@all` 喚醒全員。
7. 首行一句話說明主旨，正文給檔案路徑，長內容寫檔不貼訊息。

### PO 訊息與待決流程（`ask_question` + `PO_PENDING.md`）

PO 訊息優先於一切 worker 訊息：
1. **每輪第一步**：讀 `DIRECTOR/PO_PENDING.md`（是否有 `STATUS: answered`）及聊天最新指示，同一輪處理並登記進 `DECISION_REGISTER.md`；已處理則寫 `RESOLUTION`、時間並改 `STATUS: closed`。
2. **待決提問**：用原生 `ask_question`（一題一項、附推薦選項）向 PO 提問，並同步記帳於 `PO_PENDING.md`；需要看圖選題或並排比對成品時，配合 Artifacts / `generative_ui` 或附圖展示（`docs/12`）。
3. **Worker 不能直接問 PO**：子代理對 PO 提問時應先報總監（標題 `PO_REPLY_NEEDED`，附問題、選項、建議、影響），由總監記帳並統一問 PO。
4. **撤回**：不再需要的待決項主動關閉並寫明原因。

### 回報給 PO

- 用繁體中文（台灣用語），先結論後行動項；未驗證寫 `NOT_VERIFIED`／`UNKNOWN`，細節放檔案。
- 時間戳一律讀系統時鐘（`date` / `Get-Date`），不手估。

## 為什麼

- 其他理由見 `docs/10`。
