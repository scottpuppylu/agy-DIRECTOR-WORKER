# TASK REPORT（一個 gate 一則，一次寫完整）

省事的寫法：`python -I -X utf8 <RULES>/tools/gate_check.py --report-skeleton <工單> --repo <專案> --out <報告路徑>` 會照工單把 `TASK_ID`、`MODEL`、`BASE`、`ORDER`、`ORDER_SHA256` 和每條 AC 一列先填好，並印出錄證據、`scope_check.py`、`--match` 的完整指令；只要把 `<...>` 換成實際內容，不用讀工具原始碼學格式。

```text
首行：<一句話結論>
TASK_ID / WORKER / MODEL / BRANCH / BASE / HEAD / WORKTREE_CLEAN(yes|no)
  WORKER 寫子代理或 Swarm 名稱（例：w1）；MODEL 寫實際模型（例：inherit / flash / pro / Gemini 3.x），EFFORT 寫 UI / 規範層級（low / medium / high / xhigh / max）
  HEAD 寫 git rev-parse HEAD 的完整 40 位（Strict 一定要 40 位，總監拿它和審查的 commit 比）
ORDER: <WO-檔名.md> REVISION <n>（REVISION 要和工單現在的相同）
ORDER_SHA256: <開工時跑 gate_check.py --hash 工單 的 64 位輸出>
WORKTREE: <隔離 worktree / workspace="branch" | 共用 checkout ...；由 scope_check.py 或 gate_check.py --report-skeleton 印出，照抄，不手填>
CANDIDATE_ID: <給 PO 看的成品 id，和 PO_ACCEPTANCE 紀錄的 candidate_id 相同；同一個 commit 有兩份以上成品時必填，只有一份可省>
RESULT: PASS | PARTIAL | BLOCKED | FAIL
  ENGINEERING: ...
  PRODUCT（成品）: ...
  PERFORMANCE: ...
  STABILITY: ...
DONE: 完成項目，每項附證據路徑
TESTS: 實際執行的測試與結果；沒跑的寫 NOT_RUN
SCOPE_CHECK: PASS | VIOLATION | NOT_RUN，貼 scope_check.py 的最後一行
PROTECTED_CHANGED: 改到的受保護測試檔（TESTS_LOCKED 或 protected_tests），逐檔列出，逗號分隔；沒有就寫 NONE（無）。照抄 scope_check.py 印的同名行；gate_check --match 會拿它對 diff
EVIDENCE: 重跑指令與產物雜湊的紀錄路徑（evidence_record.py）；沒有就寫 NONE: 原因
AUDIT_RANGE: （選用）動作紀錄的區段行（audit_log.py cite 印出的那一行）；動作紀錄由 agy_audit.py 從 hook / transcript 產生
AUDIT_LOG: （選用）agy_audit.py build 或 hook-append 產出的動作紀錄路徑；沒有就寫 NONE
AGENT_NAME: （選用）本子代理或 Swarm 名稱（例：w1）；有動作紀錄時一起寫，總監才知道是哪條線
SESSION_ID: <本子代理或對話的 Antigravity conversationId（UUID）；Strict / 盲審比對用，總監與審查者不得相同>
RAN: （選用）一行一個實際跑過的指令：- `指令` exit=N at=HH:MM
  總監用 report_vs_audit.py 拿 RAN 和 TESTS / EVIDENCE 裡的指令核對動作紀錄（--log <agy_audit.py 產生的紀錄>）；沒有動作紀錄時記 UNKNOWN，不當成通過或失敗
  RAN 行用 ran_line.py 產生，不手打：ran_line.py --to 報告 -- 指令（代跑）或 --exit N --cmd "指令"（剛跑完補記）
  自寫腳本和暫存輸出放 WORK_DIR（如 <WORK_DIR>/tmp），不放系統暫存資料夾（算工作區外，總監重跑不到）
ACCEPTANCE: 工單 E 節每條一列，不增不減。狀態：AUTO 填 PASS / FAIL / BLOCKED / UNVERIFIED；
  REVIEW 最多填 READY_FOR_REVIEW；PO 只填 PENDING_PO。AUTO 填 PASS 時證據欄寫 evidence_record 的 .json 檔名。
  交件前跑 gate_check.py --match 本報告 工單 --evidence-dir 證據資料夾
| ID | 實際 | 證據 | 狀態 |
|---|---|---|---|
| AC-1 | | | |
COMMON_SENSE_GATE: 表格路徑（成品類必附）
UNVERIFIED:
KNOWN_ISSUES:
FAILED_ATTEMPTS: 試過但失敗的方法（避免下一個人重試）
PACKS: 路徑 + SHA-256
DECISIONS_NEEDED: 送總監（技術）或標明「PO 保留」；要 PO 看的圖片路徑或 Artifact 連結
NEXT: 下一步與資源需求（重資源分鐘數）
```

不寫分數或 PASS 進盲審層；中途進度寫 `RESUME_STATE.md`，不另外發無謂訊息。
摘要段＝首行到 `RESULT` 那幾行：總監預設先讀這段和 `HANDOFF.md`（`templates/HANDOFF.md`），要查細節才往下讀，所以結論和缺口都要寫在這段。
