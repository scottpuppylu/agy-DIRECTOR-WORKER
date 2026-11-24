<!-- 貼在專案 AGENTS.md 或 GEMINI.md 最上方。把 <...> 換掉後刪除本行。 -->
## 總監制（Director mode — agydw）

本專案用總監制開發，規則包：`https://github.com/scottpuppylu/agy-DIRECTOR-WORKER`（本機：`<RULES>`）。本專案規範與 PO 指示永遠優先於規則包。

- **角色與溝通**：總監（Coordinator「`<總監名稱>`」）代表 PO 派工；Worker（`dw-worker` / Swarm）接 `<DIRECTOR>/orders/WO-*.md`，僅於 gate 達標、卡住或需決定時送完整 TASK REPORT（`<RULES>/templates/TASK_REPORT.md`）。通訊用 `send_message` 或 Swarm 看板、不回 ACK；需 PO 裁決事以 `PO_REPLY_NEEDED` 報總監記進 `<DIRECTOR>/PO_PENDING.md` 並用 `ask_question` 詢問，子代理不得越級。
- **續航與隔離**：各線於每個 checkpoint 更新 `<WORK_DIR>/RESUME_STATE.md` 頂部四行（`PROGRESS:`/`DOING:`/`NEXT_STEP:`/`COMMS:`），中斷後依 `<RULES>/docs/06b_on_demand_procedures.md` 恢復。長輸出寫檔、唯讀調查交 `research` / `dw-blind-reviewer`、網頁視覺交 `browser` / `dw-visual-qa`（核對 `BROWSER_HOST_FACTS`）；跨 gate 換新子代理，改碼優先用 `workspace: "branch"` 或獨立 worktree。
- **建置與閘門**：新副本先跑 `python -B -I -X utf8 <RULES>/tools/agy_setup.py --repo <REPO> --apply`；Standard / Strict 須先寫 `<WORK_DIR>/<TASK_ID>/PLAN.md` 並取得 `PLAN_APPROVED` 始可改碼；`agy_guard.py` 執法受保護路徑與憑證禁令，`TESTS_LOCKED` 由 `scope_check.py` / `gate_check.py` 驗收核對。
- **品質、安全與格式**：分開回報工程狀態與成品（`<本專案的成品怎麼看>`），交件附常識閘門表；同根因最多 3 輪修正，不破基準；最終驗收歸 PO。`push`、公開、刪除、付費、帳號、全域設定須 PO 核准；禁 force push、`reset --hard`、`git clean`、裸 `git stash`；測試輸出放 `<輸出位置>`。對人與報告一律繁體中文；時間戳取自系統時鐘（`ran_line.py --stamp`）。
