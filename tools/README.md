# tools

`agy-DIRECTOR-WORKER`（`agydw`，`2.6.0-agy.6`）工具總覽。Python 3.9 以上，只用標準函式庫；一律用 `python -B -I -X utf8 <tool> ...` 執行。每支都有 `--help`。

詳細 Antigravity 專屬工具參數與協定說明見 [`AGY-TOOLS.md`](./AGY-TOOLS.md)。

| 工具 | 類別 | 做什麼 | 會寫檔嗎 |
|---|---|---|---|
| `agy_common.py` | agydw 新增 | 共用標準函式庫核心模組（路徑正規化、工作樹 `.git` 追蹤、原子寫檔、Transcript 單遍尾掃描與 Token 統計） | 不寫 |
| `agy_guard.py` | agydw 新增 | 雙協定 `PreToolUse` 守衛（Fail-Closed）：轉譯 Antigravity／MCP 工具與兩套檔名，支援 `deny`/`force_ask`/`allow`、`--protect`、`--pause-flag`、`--bridge-state`、`--overwrite-utf8` | `--audit` / `--bridge-state` / `--pause-flag` |
| `agy_hook_bridge.py` | agydw 新增 | `SessionStart` 注入 `RESUME_STATE`、單次統一 `PostToolUse`（`post-tool --audit-log` 兼寫稽核鏈＋掃描提示注入與輪詢）、`PreInvocation` 注入提醒與壓縮重注、`Stop` 防呆 | 僅 `--state-dir` 與 `--audit-log` |
| `agy_audit.py` | agydw 新增 | `PostToolUse` 即時寫入雜湊鏈（`hook-append`），或從 `transcript.jsonl` 事後重建（`build`）、`verify`、`loops`（含 `--reads`）、`inject` | 僅 `--log` / `--out` |
| `agy_usage.py` | agydw 新增 | 從 Antigravity `transcript.jsonl` 統計精確 token、活躍上下文峰值、快取命中率、模型與 effort 分佈、`--loops`、`--effort-check` | 僅 `--out` |
| `agy_doctor.py` | agydw 新增 | 檢查 Python/Git、三平台 OS 原生沙箱、`agy` CLI、`transcript.jsonl`、`.agents/hooks.json` 與 `agy_guard` 實跑探測、Plugin、`AGENTS.md` 大小 | 不寫 |
| `agy_setup.py` | agydw 新增 | 冪等產生 `.agents/hooks.json`、`.agents/plugins.json`、`.git/info/exclude` 與可選 `DIRECTOR/` 骨架（預設 dry-run） | `--apply` 才寫；不動全域設定 |
| `agy_setup_check.py` | agydw 新增 | 驗證 `DIRECTOR/` 必要檔案、`PENDING_METHOD`、`PO_PENDING`、`RESUME_STATE`、設定檔與 `.agents/*.json` | 不寫 |
| `safety_gate.py` | 安全閘門 | `TESTS_LOCKED` 鎖定驗收測試、`PLAN_APPROVED` 計畫雜湊、防止自降標準開關 | 不寫 |
| `gate_check.py` | 治理（已適配） | 檢查工單/報告/裁決/RESUME_STATE、`--match`（含證據與安全閘門）、`--report-skeleton`、`--after-verdict`、`--sequence`、`--final` | 僅 `--out` / `--snapshot` |
| `scope_check.py` | 治理（已適配） | 比對實際改動與工單 `SCOPE` / `FORBIDDEN`、`order_limits`、`protected_tests`、`--shared` | 不寫 |
| `evidence_record.py` | 上游一致 | 執行驗收指令並留下 SHA-256 證據紀錄，支援 `--order` 綁定與 `verify --rerun` | `run` 寫 JSON 紀錄 |
| `audit_log.py` | 上游一致 | 雜湊鏈 `append` / `verify` / `head` / `cite` / `recover` | `append` / `recover` |
| `report_vs_audit.py` | 上游一致 | 比對 `TASK REPORT` 的 `RAN:` 宣稱與實際動作紀錄 | 不寫 |
| `loop_guard.py` | 上游一致 | 偵測重複呼叫、同錯重試、無進度輪詢、A/B 乒乓、參數驗證失敗、未處理子代理錯誤 | 不寫 |
| `inject_scan.py` | 上游一致 | 掃描文字與輸出中的提示注入模式 | 不寫 |
| `resume_inject.py` | 上游一致 | 截取 `RESUME_STATE.md` 四行表頭與未結段（最多 `--max-bytes`） | 不寫 |
| `memory_size_check.py` | 治理（已適配） | 檢查 `AGENTS.md` / `GEMINI.md`、`--kind rules`（24 KB 上限）、`--kind resume`、`--chain` | 不寫 |
| `resume_archive.py` | 上游一致 | 將舊的 `RESUME_STATE.md` 段落搬到 `RESUME_HISTORY_YYYYMMDD.md`（預設 dry-run） | `--execute` 才寫 |
| `dispatch_check.py` | 上游一致 | 檢查 `DISPATCH` 行的難度類別與 effort（`low/medium/high/xhigh/max`），`--dup` 查重複派工 | 不寫 |
| `delete_guard.py` | 上游一致 | 刪檔前檢查是否仍被追蹤檔或 `RESUME_STATE` 引用 | 不寫 |
| `round_check.py` | 上游一致 | 檢查 `FIX_ROUNDS:` 修正輪數上限與根因研究表 | 不寫 |
| `trace_report.py` | 上游一致 | 產生 需求 → 工單 → AC → 證據 → 裁決 追溯表 | 不寫 |
| `metrics_report.py` | 治理（已適配） | 彙整 `PURPOSE.md` 五項成效指標與接手演練數據 | 不寫 |
| `ruler_check.py` | 上游一致 | 驗證量尺是否真正拍到／讀到有效數據（`INVALID` 則不判） | 不寫 |
| `run_plan_check.py` | 上游一致 | 驗證實測計畫 `run-plan-v1` 的程式碼參照與啟動參數 | 不寫 |
| `pack_check.py` | 上游一致 | 檢查截圖盲審包完整性、不重複性（`unique=`）與必要片段 | 不寫 |
| `pre_tr.py` | 上游一致 | 實測前先查 `inputs` 雜湊、進程與預檢項 | 不寫 |
| `waiver.py` | 上游一致 | 管理限期豁免（支援多工作線與 `--to`） | 寫豁免 JSON |
| `status_gen.py` | 治理（已適配） | 產生單一唯讀 `status.py` 供恢復時一次看清現況（支援 `po_pending`、`orders`、`audit`、`loops`） | 產生 `status.py` |
| `release.py` | 治理（已適配） | 發版前檢查、執行 `selftest_agy.py`、外洩掃描、推分支與 tag（預設 dry-run） | `--execute` 動遠端 |
| `pause_ctl.py` | 上游一致 | 讀寫暫停旗標與檢查重資源程序 | 開關旗標檔 |
| `secret_mask.py` | 上游一致 | 憑證與機密遮罩核心模組 | 不寫 |
| `completion.py` / `flag_check.py` / `hash_check.py` / `line_brief.py` / `ran_line.py` / `sim_check.py` | 上游一致 | 交付完整性、旗標對照、雜湊、BRIEF 鎖定、RAN 行格式化、模擬器檢查 | 依指令而定 |
| `selftest_agy.py` | agydw 測試 | 驗證 27 支上游工具逐位元組一致、全部 `agy_*` 工具、`cmd /c` hook 實測、Plugin 結構、必讀上限與零外洩 | 只寫暫存夾並清理 |
