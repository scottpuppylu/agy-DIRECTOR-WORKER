# 02 工單、Gate、報告與測試

## 規則

### 治理模式（依風險選）

- **Direct**：純查詢、文件、沒有風險。不跑安全閘門與規格審查。
- **Standard**：一般隔離開發，可以回退。依 `director_work.json` `safety` 預設：須先鎖定測試（`TESTS_LOCKED`）、規格審查（`SPEC_REVIEW`）、並經計畫核准（`PLAN_APPROVED`）（`docs/13`）。
- **Strict**：核心邏輯、資料格式、存檔、對外介面、跨分支整合、發布。必須全套安全閘門、`--candidate` 40 位 SHA 綁定與嚴格證據核對。
- 流程：`Baseline → Plan (PLAN_APPROVED) → Isolated Change → Build/Test → 成品檢查 → Evidence → Gate → Checkpoint`。

### 工單

- 一律落檔：`DIRECTOR/orders/WO-<編號>-<短名>.md`，格式見 `templates/WORK_ORDER.md`。訊息只給路徑、一句摘要與必要決定。
- 必填：`PRIMARY_WORKER / MODEL / EFFORT / FALLBACK / GOVERNANCE / BASE_REF / SCOPE / FORBIDDEN / ACCEPTANCE / DELIVERABLES / STOP_RULES`。
- `BASE_REF` 寫 `origin/main @ <commit>`：派工前先 `git fetch`。`gate_check.py --order <工單> --repo <專案>` 會驗證新鮮度，落後印 `~ WARN BASE_REF`（`--strict-base` 判 FAIL）。
- 內容分段：A 背景、B 目標（MUST / OPTIONAL / OUT OF SCOPE、SCOPE / FORBIDDEN 清單）、C 執行前檢查、D 實作要求（註明應用的 `agydw` 工具）、E 驗收條件（含 `CHECKS:` 與 `TESTS_LOCKED:`）、F 安全與版控、G 完成定義、H 變更紀錄。
- 驗收條件每項含：ID、類型、要求、驗證方法、預期。結果只寫在 `TASK REPORT` 的 `ACCEPTANCE` 表，不寫回工單。
- 類型 `AUTO / REVIEW / PO`：
  - `AUTO`：寫進工單 `CHECKS:`（以派工雜湊鎖定），總監以 `evidence_record.py verify --rerun` 重跑判定。
  - `REVIEW`：須獨立審查（物理唯讀 `dw-blind-reviewer` 或獨立 `browser` 視覺 QA），開工前寫明審查視角與常識閘門（`docs/07`）。
  - `PO`：只有 PO 能判。
  - worker 可填狀態以 `tools/vocabulary.json` 為準：`AUTO` 為 `PASS / FAIL / BLOCKED / UNVERIFIED`；`REVIEW` 最多填 `READY_FOR_REVIEW`；`PO` 只填 `PENDING_PO`。
- 派工前先跑 `dispatch_check.py --dup` 查重派；Standard／Strict 必跑 `safety_gate.py --lock-tests`、獨立規格審查（`SPEC_REVIEW`）與計畫核准（`PLAN_APPROVED`），並於 `DIRECTOR_LOG.md` 記下 `gate_check.py --hash` 印出的 `DISPATCH <WO-ID> rev=<n> sha=<雜湊> | class=<類別> effort=<等級> model=<模型>`。
- 初衷卡指紋（`BRIEF_REVISION` / `BRIEF_FINGERPRINTS_V1`）與 `BRIEF_LOCKED` 規則沿用上游：初衷卡調整時，`--brief BRIEF.md` 會提示 `~ BRIEF_STALE` 與 `~ BRIEF_UNCONFIRMED`。

### Gate 與回報

- Worker 在 gate 完成、卡住、需要裁決或要偏離工單時，一次送出完整的 `TASK REPORT`（可用 `gate_check.py --report-skeleton` 自動填好固定欄位與指令清單）。
- 暫存檔位置：一律設在工單 `WORK_DIR/tmp`（設定 `TEMP` / `TMP` / `TMPDIR`），不將測試暫存檔留在專案 repo 或無法重現的系統暫存夾。
- 交件必附：`scope_check.py` 結論（`PASS / VIOLATION / NOT_RUN`）、`PROTECTED_CHANGED`、`evidence_record.py` 紀錄路徑（最後一次 commit 之後重記，加 `--order` 綁定）、`WORKTREE:`（由工具產生）、以及 `ran_line.py` 產生的 `RAN:` 行。
- **總監審查四階段**：
  1. 對照需求與工單驗收條件（不接受縮減；REVIEW 交給唯讀 `dw-blind-reviewer` / `dw-visual-qa`）。
  2. 獨立重跑核對：`scope_check.py`、`evidence_record.py verify --rerun --order ... --candidate <40 位>`、`agy_audit.py build`（或直接驗證 hook 寫入的 `audit_log`）+ `report_vs_audit.py`、`gate_check.py --match ... --evidence-dir ... --dispatch-log ...`。
  3. 判定詞彙（`tools/vocabulary.json`）：`PASS / PASS_WITH_WARNINGS / CONDITIONAL_PASS / PARTIAL / HOLD / BLOCKED / FAIL / NOT_RUN`。
  4. 下一步只選一個：`ACCEPT_AND_CLOSE / CONTINUE_SAME_WORKER / SWITCH_TO_OTHER_WORKER / REQUEST_INDEPENDENT_REVIEW / REQUEST_OWNER_AUTHORIZATION / STOP_AND_REPORT_BLOCKER`。
- 裁決（`templates/GATE_VERDICT.md`）採工程、成品、效能、穩定性、整合與 PO 驗收分開記錄，A 節必附總監親跑的自檢行（`- \`<cmd>\` exit=<n> at=<HH:MM>`），F 節預設鎖定 `NO MERGE / NO PUSH / NO FINAL ACCEPTANCE`。
- 證據等級：`VERIFIED_LOCAL / VERIFIED_REMOTE / REPORTED / SUPPORTED / ESTIMATED / ASSUMPTION / UNKNOWN / NOT_TESTED / INVALID`。
- **閘門順序與鎖後碼**：`gate_check.py --sequence` 驗 DISPATCH → SPEC_REVIEW → PLAN_APPROVED → worker 第一個 commit；`gate_check.py --after-verdict` 防止裁決後未經重審就改程式碼；里程碑收尾跑 `agy_usage.py --out` 與 `gate_check.py --final`（含 `evidence/E2E/` 公開入口實測）。
- **worker 上限**：預設 80 輪或上下文達 60% 即停，寫 `HANDOFF.md` 交接給新開的 worker；領航對話預設只讀 `TASK REPORT` 摘要段與 `HANDOFF.md`。

### 追溯、PO 驗收綁定與發版

- 用 `trace_report.py` 產生 `需求 → 工單 → AC → 證據 → 裁決` 追溯表，加 `--brief`、`--director-log`、`--acceptance` 對齊 `PO_ACCEPTANCE.json`（`po-acceptance-v1`）。
- 用 `tools/release.py` 發版：先 dry-run 檢查工作樹乾淨、遠端同步、`VERSION` 與 `CHANGELOG.md` 一致、執行 `tools/selftest_agy.py` 與公開掃描，經 PO 核准後才加 `--execute`。

### 重資源排程與測試預算

- 有共用重資源（GPU、長 build、獨佔量測時段）時，先送 `TEST REQUEST`（`templates/TEST_REQUEST.md`，含 `CODE_REFS:` 與預估 GiB／分鐘），跑 `pre_tr.py --config pre_tr.json`、`gate_check.py --test-request`，由排程看板寫 `GO` / `QUIET` / `HOLD`（搭配 `templates/run_closing.py` 與 `tools/waiver.py`），並用 `tools/pause_ctl.py` 搭配 `agy_guard.py --pause-flag` 強制暫停。沒有共用重資源的專案不設總管。
- 第 0 級（離線單元／雜湊檢查，`templates/CPU_REPRO.md`：`hash_check.py`、`flag_check.py`、`sim_check.py`）必在本地先過；第 1 級採開關批次對照；第 2 級單項例外須寫理由。

## 為什麼

- 工單、證據雜湊與裁決都以檔案為準，避免聊天洗掉或模型自我宣稱通過；讓獨立盲審與機械重跑取代口頭信任。
