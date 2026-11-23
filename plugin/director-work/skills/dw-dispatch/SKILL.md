---
name: dw-dispatch
description: >-
  Use when drafting a WORK_ORDER, locking acceptance tests before coding, running spec
  review, selecting between Antigravity Orchestrator, Subagent, Swarm, or run_workflow,
  or dispatching and monitoring worker lines.
---

# Work Order Authoring & Antigravity Dispatch

依風險等級（`Direct` / `Standard` / `Strict`）寫工單、過安全閘門，並選用最適合的 Antigravity 編排原語。

## 1. 開工單與安全閘門（派工前）

1. **防重派檢查**：
   `python -I -X utf8 <DIRECTOR>/tools/dispatch_check.py --dup "<任務標題與範圍>" --resume "<DIRECTOR>/../**/RESUME_STATE*.md" --repo <REPO>`
2. **測試先寫並鎖定（`Standard` / `Strict`）**：
   - 依工單 E 節邊界清單先寫驗收測試並 commit 至 `BASE_REF`。
   - 產出雜湊貼入工單 `TESTS_LOCKED:`：
     `python -I -X utf8 <DIRECTOR>/tools/safety_gate.py --lock-tests <test_files...> --repo <REPO>`
3. **工單格式與雜湊檢查**：
   `python -I -X utf8 <DIRECTOR>/tools/gate_check.py --order <ORDER_MD> --repo <REPO>`
   `python -I -X utf8 <DIRECTOR>/tools/gate_check.py --hash <ORDER_MD>`（將印出的 `DISPATCH ...` 貼入 `DIRECTOR_LOG.md`，補寫 `| class=<類> effort=<級> model=<模型>` 後用 `dispatch_check.py --log` 驗證）。
4. **規格審查（`Standard` / `Strict`）**：
   - `Strict` 或高風險多面向任務可使用 `templates/workflows/parallel_spec_review_workflow.py` 透過 `run_workflow` 平行審查，或指派獨立唯讀子代理依 `templates/SPEC_REVIEW.md` 審查，將 `SPEC_REVIEW <WO> tokens=... issues=... adopted=...` 記進 `DIRECTOR_LOG.md`。

## 2. Antigravity 四層編排選型（`docs/11`）

| 情境 | 選用原語 | 關鍵參數與優勢 |
|---|---|---|
| 單線中小型任務 | 主對話 **Orchestrator** 或單一 **`dw-worker` subagent** | `Workspace: "inherit"`（僅單寫手）或 `"share"`；省協調成本 |
| 單線需嚴格隔離或測試沙箱 | **`invoke_subagent`**（`dw-worker`） | `Workspace: "branch"`（獨立 clone，天然解決共用 `.git` 風險） |
| 多線並行開發 + 互審（2–4 線以上） | **`swarm`**（共享留言板 + 頻道） | 每個寫手開 `workspace: "share"` 或 `"branch"`；超過 8 人依區域開 Channels；**模型耗盡時用 `swarm{action:"model"}` 熱切換保留歷史**；**可用 `swarm{action:"models"}` 查即時剩餘額度** |
| 固定管線／批次結構化分析 | **`run_workflow`** | Python `parallel()` / `pipeline()` 搭配 JSON Schema 強制結構化回傳 |

## 3. 派工與計畫閘門（`PLAN_APPROVED`）

- 只給 worker **工單路徑、`RESUME_STATE.md` 路徑與一句明確目標**（不貼長段文件浪費上下文）。
- `Standard` / `Strict` 工單要求 worker 先產出 `<WORK_DIR>/<WO>/PLAN.md`（`- [ ] 1.1 ...` 對應 AC）並回報；總監審查無誤後執行：
  `python -I -X utf8 <DIRECTOR>/tools/safety_gate.py --plan-hash <PLAN_MD> --order <ORDER_MD>`
  將 `PLAN_APPROVED <WO> sha=...` 寫入 `DIRECTOR_LOG.md` 再通知 worker 開工。
- 若需定時巡檢阻塞或重資源佇列，可使用原生 `schedule` 工具並將重建方式登記於 `DIRECTOR/GUARDS.md`。
