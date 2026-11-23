# 06 無損續航

目標：壓縮、額度耗盡、錯誤、當機或重開之後，每條工作線都能從最後已驗證的 checkpoint 接著做，不重做、不沿用半成品。

## 規則

### `RESUME_STATE.md`

- 每條工作線維護一份 `RESUME_STATE.md`（`templates/RESUME_STATE.md`），路徑登記在 `DIRECTOR/ACTIVE_WORKSTREAMS.md`。
- **頂部固定四行** `PROGRESS:` / `DOING:` / `NEXT_STEP:` / `COMMS:`，只看四行就能接手；其餘欄位缺則寫 `NONE`。`IN_FLIGHT` 記背景任務 ID、輸出與完成判定。
- **何時更新**：每次 checkpoint commit 後、送 TEST REQUEST 前、背景／子代理啟動與結束時、收到總監裁決後、對話長到接近壓縮前。寫完再做下一步。
- `UPDATED` 讀系統時鐘；寫完跑 `gate_check.py --resume <檔>` 查格式與大小（預設 20 KiB／單行 4 KiB，超過用 `resume_archive.py` 歸檔舊段，不動近期與未結段）。
- 有成果就 commit；進度絕不只留在對話記憶。

### 恢復與每輪第一步

- 啟用 `agy_hook_bridge.py` 時，`SessionStart` 會注入四行表頭與未結段，`PreInvocation` 會在偵測到 `CHECKPOINT` 壓縮後自動重注；未啟用 hook 時，恢復後手動跑一次 `resume_inject.py --file <RESUME_STATE.md>`（與有設則跑 `status.py`）。
- 任何輪次被中斷的擷取／量測／打包一律重跑，不沿用半成品。
- 若在 Swarm 中遇模型額度耗盡，優先以 `swarm{action:"model"}` 熱切換模型接續對話，勿直接 `kill`。

### 機制與重建

所有防護與排程（`.agents/hooks.json`、`schedule` 定時任務、`pause_ctl`）必登記於 `DIRECTOR/GUARDS.md` 附檢查與重建指令；重建步驟見 `docs/06b`。

## 為什麼

- 其他理由見 `docs/10`。
