---
name: dw-review-gate
description: >-
  Use when a worker submits a TASK REPORT or deliverable, to run automated gate checks,
  scope verification, evidence reruns, transcript audit matching, physically read-only
  blind review, browser visual QA, and write GATE_VERDICT.
---

# Deliverable Verification, Blind Review & Gate Verdict

**測試通過不等於成品對；沒有重跑證據與獨立審查就不算完成。**

## 1. 自動化機械核對（總監親跑，不採信轉述）

依序執行（可使用 `templates/workflows/gate_verify_workflow.py` 輔助）：

1. **範圍與上限檢查**：
   `python -I -X utf8 <DIRECTOR>/tools/scope_check.py --order <ORDER_MD> --repo <WORKER_REPO> --candidate <HEAD_SHA>`
2. **證據重跑驗證**：
   `python -I -X utf8 <DIRECTOR>/tools/evidence_record.py verify <EVIDENCE_JSON> --rerun`
3. **動作稽核鏈對照報告**：
   - 若已啟用 `PostToolUse` hook log：先跑 `audit_log.py verify --log <AUDIT_LOG>`。
   - 若要從對話紀錄獨立複驗：
     `python -I -X utf8 <DIRECTOR>/tools/agy_audit.py build --out <DIRECTOR>/audit/<WO>_rebuilt.jsonl --conversation <WORKER_CONV_ID> --force`
     `python -I -X utf8 <DIRECTOR>/tools/agy_audit.py verify --log <DIRECTOR>/audit/<WO>_rebuilt.jsonl`
   - 核對報告 `RAN:` 宣稱：
     `python -I -X utf8 <DIRECTOR>/tools/report_vs_audit.py --report <TASK_REPORT> --log <AUDIT_OR_REBUILT_JSONL>`
4. **工單對照與安全閘門**：
   `python -I -X utf8 <DIRECTOR>/tools/gate_check.py --match <TASK_REPORT> <ORDER_MD> --evidence-dir <DIRECTOR>/evidence --dispatch-log <DIRECTOR>/DIRECTOR_LOG.md --repo <WORKER_REPO> --candidate <HEAD_SHA>`

## 2. 獨立唯讀盲審與視覺驗收

- **物理唯讀盲審（`dw-blind-reviewer`）**：透過 `invoke_subagent` 呼叫 `TypeName: "dw-blind-reviewer"`（僅有 `read_file` 工具，無法改檔），給定**工單規格、變更檔案與盲審包**（不給作者自評結論），要求獨立找缺口。
- **跨模型族建議**：若 Swarm 或多模型可用，優先選與作者不同模型／較高思考層級擔任盲審。
- **前端／視覺成品驗收**：呼叫內建 `browser` subagent（`/browser`）實測頁面、檢查 console/network 錯誤與截圖，且僅採信宿主層 `BROWSER_HOST_FACTS` 驗證過的來源與截圖；資料／圖包先跑 `pack_check.py` / `ruler_check.py`。

## 3. 產出裁決（`GATE_VERDICT`）

- 依 `templates/GATE_VERDICT.md` 寫裁決檔（工程狀態與成品狀態分欄，附總監親跑的 `- \`<cmd>\` exit=<n> at=<HH:MM>` 自檢行）。
- 檢查格式：
  `python -I -X utf8 <DIRECTOR>/tools/gate_check.py --verdict <VERDICT_MD> --config <DIRECTOR>/director_work.json`
- 若同一缺陷經 2 輪未收斂，跑 `round_check.py` 並強制獨立根因研究（至多 3 輪假設驅動修正）。
- **絕不自行替 PO 做最終驗收或 push / merge 到 `main`。**
