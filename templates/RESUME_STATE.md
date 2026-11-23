PROGRESS: <最後完成的一步、commit、證據路徑>
DOING: <正在做什麼，屬於哪張工單的哪一項>
NEXT_STEP: <接下來的精確動作，可以直接照做>
COMMS: <我在等誰（等什麼）／誰在等我（欠誰什麼）／做完要通知誰；沒有寫 NONE>

# RESUME_STATE — <TASK_ID>
UPDATED: <YYYY-MM-DD HH:MM ±TZ，讀系統時鐘>
ORDER: <工單路徑>
AGENT_NAME: <coordinator（主對話）／w1（子代理或 Swarm 名稱）>
SESSION_ID: <本 Antigravity conversationId（UUID）或 NONE>
WORKTREE / BRANCH / BASE / HEAD: <...>
LAST_VERIFIED_CHECKPOINT: <commit / tag + 一句話>
COMPLETED_STEPS:
- <步驟>（證據：<路徑>）
IN_FLIGHT: <manage_task 背景工作 ID、manage_subagents 子代理 conversationId、schedule 排程 ID、啟動時間與輸出路徑> 或 NONE
COORDINATOR: <已送 / 已取得的 TEST REQUEST、PLAN_APPROVED、已送的 send_message> 或 NONE
PARTIAL_OUTPUTS_TO_DISCARD: <被中斷時要重做的輸出路徑> 或 NONE
KEEP_AS_EVIDENCE: <要保留的證據路徑>（同一路徑不得同時出現在可回收清單）
FIX_ROUNDS: <每個修過的缺陷一行：`- <缺陷>: round=<累計輪次> set=<組> study=<ROOT_CAUSE_STUDY.md 或 NONE>`> 或 NONE
<研究檔每一節先放 `| 項目 | 測試條件 | 參考條件 |` 表；宣告一組做完前，核對資料夾、程式、提示詞是同一組>
OPEN_DECISIONS: <等總監 / PO 的決定> 或 NONE
DO_NOT_REPEAT: <已失敗的方法，不要重試> 或 NONE
<同一呼叫失敗兩次：先寫一行「為什麼失敗、下一步改什麼」再試；派出的子工作逾時沒結果或出錯，用 send_message 回報主管，不可靜默等待；宣告完成前本輪跑過驗證指令並附 RAN 行>
<!-- 整份 ≤ 20 KiB、單行 ≤ 4 KiB；超過用 tools/resume_archive.py 把舊段落原文搬到 RESUME_HISTORY_<YYYYMMDD>.md（docs/06b）。 -->
