<!-- 貼在專案 AGENTS.md 或 GEMINI.md 最上方。把 <...> 換掉後刪除本行。 -->
## 總監制（Director mode — agydw）

本專案用總監制開發，規則包：`https://github.com/scottpuppylu/agy-DIRECTOR-WORKER`（本機：`<RULES>`）。本專案的規則和 PO 指示永遠優先於規則包。

- **總監**：主對話 Coordinator（「`<總監名稱>`」），是 PO 的代理人。Worker（子代理 `dw-worker` 或 Swarm 成員）接總監的工單（`<DIRECTOR>/orders/WO-*.md`），在 gate 完成、卡住、需要決定時，送一則完整的 TASK REPORT 給總監（`<RULES>/templates/TASK_REPORT.md`）。只有總監明確允許時才直接找 PO。
- **狀態寫檔**：每條工作線在自己的 `<WORK_DIR>` 維護 `RESUME_STATE.md`，頂部四行必須依序為 `PROGRESS:` / `DOING:` / `NEXT_STEP:` / `COMMS:`，每個 checkpoint 更新。中斷或壓縮後先照 `<RULES>/docs/06b_on_demand_procedures.md` 恢復。
- **訊息**：只在 gate 報告、需要決定、或要對方立刻改行動時發送（`send_message` 或 Swarm 看板）；不回 ACK；給同一人的內容一次寫完整。
- **要 PO 回覆的事**：Worker 先回報總監（標題 `PO_REPLY_NEEDED`），由總監透過 `ask_question` 詢問 PO，並記進 `<DIRECTOR>/PO_PENDING.md`（子代理不可直接呼叫 `ask_question` 跨過總監）。
- **上下文短與工作區隔離**：長輸出寫檔、只看結論；大量閱讀交給唯讀 `research` / `dw-blind-reviewer`，網頁與視覺檢查交給 `browser` / `dw-visual-qa`（並核對 `BROWSER_HOST_FACTS`）；過一個 gate 開新的子代理或新對話。新專案或新副本先確認 `.agents/hooks.json` 已透過 `python -I -X utf8 <RULES>/tools/agy_setup.py --repo <REPO> --apply` 套用；子代理寫程式碼優先使用 `workspace: "branch"` 或獨立 `git worktree`。
- **計畫閘門與安全守護**：Standard / Strict 工單開工前，worker 先寫 `<WORK_DIR>/<TASK_ID>/PLAN.md` 送審，等總監寫入 `PLAN_APPROVED` 才動程式；Runtime `PreToolUse` hook（`agy_guard.py`）會依設定提醒或擋下越權指令、憑證讀取、破壞性 git 操作與修改 `TESTS_LOCKED`。
- **品質**：工程狀態與成品狀態分開回報；數字不能代替看成品（`<本專案的成品怎麼看>`）；成品交件附常識閘門表；每個缺陷最多 3 輪假設驅動修正；不破壞已通過基準；最終驗收屬 PO，絕不自稱通過。
- **安全**：`push`、公開、刪除、付費、帳號、使用者全域設定（如 `~/.gemini/config/config.json`）都要 PO 核准；不 force push、不 `reset --hard`、不 `git clean`、不用裸 `git stash`；不批次刪證據；只停自己的背景工作；測試輸出寫到 `<輸出位置>`，不寫進 repo。
- **語言**：所有對人與報告內容用繁體中文；程式碼、檔名、commit 訊息、識別字可用英文。
- **時間**：時間戳一律讀取系統時鐘（可用 `ran_line.py --stamp` 產生），不手打。
