# agydw tools

`agy-DIRECTOR-WORKER`（簡稱 `agydw`，`2.6.0-agy.6`）在 Google Antigravity（App 2.0 / `agy` CLI / IDE / SDK）上的工具手冊。`tools/` 目錄內含 **41 支 `.py` 工具腳本＋2 份 `.json` 設定檔**，全部工具只使用 Python 3.9+ 標準函式庫，不需 `pip install`；一律用以下方式執行（`-B` 不留 `__pycache__`、`-I` 隔離匯入；Linux / macOS 若未設 `python` 別名請改用 `python3`）：

```text
python -B -I -X utf8 tools/<tool>.py ...
```

與 `dsh-DIRECTOR-WORKER` 最大差異：**Antigravity 原生具備完整的生命週期 Hooks（`SessionStart`、`PreToolUse`、`PostToolUse`、`PreInvocation`、`PostInvocation`、`Stop`）、Plugin 封裝、自訂子代理工具白名單、Swarm 共享留言板，以及三平台 OS 原生終端沙箱。** 因此這批工具直接提供自動執行期防護與可驗證的雜湊鏈紀錄，而不只是事後補查。

## Antigravity 專屬新工具（`agy_*.py`）

- **`agy_common.py`** — 標準函式庫共用核心模組：集中路徑大小寫正規化、純檔案系統 `.git` / linked worktree 追蹤、`write_text_atomic` / `write_json_atomic`、Transcript 單遍尾掃描與 Token 統計輔助函式，減少跨模組重複並維持零外部依賴。

- **`agy_guard.py`** — 雙協定 `PreToolUse` 守衛（同時吃 Antigravity `camelCase` 與 Claude `snake_case`，採 Fail-Closed）：
  - 包裝上游 `hook_guard.py` 與 `guard_rules.json`（不改動上游規則引擎），同時覆蓋 App 2.0（`run_command`、`read_file`、`write_file`、`edit_file`）、IDE/CLI（`view_file`、`write_to_file`、`replace_file_content`、`multi_replace_file_content`、`grep_search`、`find_by_name`、`list_dir`）與 MCP 工具（`call_mcp_tool`、`mcp_<server>_<tool>` 轉譯為 `mcp__<server>__<tool>` 以啟動 `G11`/`G21`/`G30`/`G42`）。
  - 保護 Antigravity 設定與規則（`G43`：`.agents/hooks*.json`、`plugins.json`、`AGENTS.md`、`GEMINI.md`、`~/.gemini/config/**`，對 worker 強制 `deny`）、憑證（`G50` + `G53`，含 Windows 尾隨空白／NTFS 資料流／PowerShell `-Path:`／`dd of=`／`git show` 檢查），並支援 `--protect DIRECTOR` 鎖定 `orders/`、`DIRECTOR_LOG.md`、`PO_PENDING.md`、`reviews/**`、`DIRECTOR_STATE.md`、`evidence/**`、`PAUSE.flag` 等治理檔案寫入。
  - 在 `--mode ask` 下輸出 `"decision": "force_ask"`；`--mode remind` 下回傳 `"allow"` 並可透過 `--bridge-state` 在下一輪 `PreInvocation` 注入提醒；空輸入或損壞 JSON 一律 Fail-Closed 回傳 `"deny"`，Antigravity 格式固定以 exit `0` 輸出 JSON。
  - 選配 `--overwrite-utf8`：示範 `PreToolUse.overwrite`，將裸 `python script.py`（含引號空白路徑）自動改寫成 `python -X utf8 script.py`（不加 `-I`，保留專案工作目錄與套件匯入）。

- **`agy_hook_bridge.py`** — 兩段式 Hook 橋接器（補足 Antigravity `PostToolUse` 只能回 `{}` 的協定限制）：
  - `session-start`：`SessionStart` 時透過 `resume_inject.py` 注入 `RESUME_STATE.md` 四行表頭與未結段（最多 `--max-bytes`，預設 2048），可選 `--banner` 輸出零模型消耗的 `metadata.user_markdown`。
  - `post-tool`：`PostToolUse` 單一程序（搭配可選 `--audit-log FILE` 呼叫 `agy_audit.append_payload_entry` 同時寫入動作稽核鏈）偵測連續輪詢 log（`poll_hint`）並單遍讀取 `transcriptPath` 對應 `stepIdx` 的輸出執行 `inject_scan.py` 提示注入掃描，有警示時寫入 `--state-dir/pending_<cid>.jsonl`，stdout 嚴格印出 `{}`。
  - `pre-invocation`：`PreInvocation` 時原子性取出待注入提醒，並比對 `transcript.jsonl` 是否新增 `SYSTEM / CHECKPOINT` 壓縮步驟，若有壓縮則自動重注 `RESUME_STATE.md`。
  - `stop-check`：`Stop` 時先檢查 `continuationActive == true` 或非 idle 事件立即放行，支援 `NO_TOOL_CALL`、`model_stop`、`END_TURN` 與 `fullyIdle`；加 `--enforce-resume` 與 `--sessions` 時優先以同 Git `common-dir` 驗證的事件工作樹為準，區分已乾淨提交的 linked worktree worker 分支，檢查未提交改動與新於 `RESUME_STATE.md` 的已提交 `HEAD`（含 `--state-dir` 單次防迴圈保護）。

- **`agy_audit.py`** — 雙模式動作稽核與迴圈/注入掃描：
  - `hook-append --log FILE`（及可匯入的 `append_payload_entry`）：`PostToolUse` 即時寫入與 `audit_log.py` 完全相容的 SHA-256 雜湊鏈（缺漏輸出或非終態 `RUNNING` 誠實記為 `unverified` / `running`，不假寫 `ok`），stdout 印 `{}`。
  - `build --out FILE [--include-subagents]`：從 `transcript.jsonl` / `transcript_full.jsonl` 同一快照重建稽核鏈（`kind=rebuild` 在 seq 1，`source.transcripts` 納入主檔與實際讀取之 `transcript_full.jsonl` 雜湊/大小），採兩階段批次回合配對（`tool_call_id` 先配對、剩餘匿名 1:1 配對、保留 `batch_pending` 與 `GENERIC` 孤兒失敗事件；缺 `status` 一律記 `unverified`），並以拓撲排序發現追蹤子代理 UUID。
  - `verify`：轉交 `audit_log.py verify` 後輸出 `AGY_AUDIT_STATUS` 摘要（含 `confirmed_ok`、`confirmed_error`、`unverified` 與 `running`）。
  - `loops [--reads] [--since YYYY-MM-DD] [--until YYYY-MM-DD]`：呼叫上游 `loop_guard.py` 檢查 6 類迴圈與讀取熱點（容錯略過壞 JSON 行）；`inject` 掃描提示注入。

- **`agy_usage.py`** — 單遍串流用量與上下文統計：
  - 單遍串流解析（`step_index` 後蓋前去重、不載入整檔 `transcript_full.jsonl`），優先採用結構化 `workspacePaths` / `cwd` 進行 `--project-dir` 嚴格歸屬（消除父資料夾前綴誤歸屬並正確納入 `--include-subagents` 子代理）；僅在欠缺結構化工作區欄位時退回邊界安全正則比對並標註 `attribution_mode: "text_heuristic"`；非數字計數欄位安全跳過並統計 `invalid_metric_fields`。
  - 依據實測語意計算未快取輸入、`cache_read_tokens`、`input + cache_read` 上下文峰值、`output_tokens`、每日彙總、`--loops` 與 `--effort-check ORDER`（修正子代理搭配 `--since`/`--until` 誤報）；無符合資料印出 `NO_DATA` 並 exit `2`。

- **`agy_doctor.py`** — 環境與防線實機健檢：
  - 檢查 Python（含 `python3` 相容提示）、Git、OS 沙箱、`agy` CLI、`transcript.jsonl`（單行讀取免載入大檔）、`.agents/hooks.json` 結構（群組 `enabled`、`matcher` 核心工具＋MCP 覆蓋率、全部 4 個生命週期）與 OS Shell 實跑探測（`HOOK_EXEC_OK`）、啟用中之並存版本化工具清單（含 `agy_common.py`）、Plugin 官方 Schema 與 `dw-blind-reviewer`（`tools: [read_file]` + `inheritMcp: false` + `inheritCustomizations: false`）、規則大小與 `--licenses`。

- **`agy_setup.py`** & **`agy_setup_check.py`** — 專案建置與驗證：
  - `agy_setup.py`：先完整預檢目標路徑型別、專案歸屬、Windows `%` 路徑警告與既有 JSON 語法（失敗零寫入）；為每個專案建立獨立 `DIRECTOR-<repo>`（含非 ASCII 雜湊防撞、`director_work.json["agydw"]` 同步與舊版 `project_git_common_dir` 遷移）、以跨程序排他鎖維護 `.sessions.json`（`default: "worker"`）與 `.agydw_setup_state.json` 持久化護欄選項（支援 `--enable`、`--no-exclude-agents`、`--keep-versions` 與保留既有自訂欄位），並透過並存版本化目錄 `tools_v*` 完成單次統一 `PostToolUse` 掛載、無空窗工具切換與舊版清理／備份。
  - `agy_setup_check.py`：驗證專案歸屬、持久化護欄一致性、Hook 群組啟用/Matcher/生命週期（相容單次統一或舊版雙命令 `PostToolUse`）、`PO_PENDING.md`、`RESUME_STATE.md` 大小上限與 `.agents/*.json`（預設 shadow 模式，`--strict` 才因 shadow 失敗）。

## 沿用上游與安全閘門工具

- **27 份與 `DIRECTOR-WORK v2.6.0` 逐位元組相同的核心工具與設定檔**（25 支 `.py` ＋ 2 份 `.json`）：包含 `audit_log.py`、`hook_guard.py`、`loop_guard.py`、`inject_scan.py`、`resume_inject.py`、`report_vs_audit.py`、`evidence_record.py`、`dispatch_check.py`、`trace_report.py`、`round_check.py`、`ruler_check.py`、`run_plan_check.py`、`pack_check.py`、`pre_tr.py`、`delete_guard.py`、`resume_archive.py`、`waiver.py`、`secret_mask.py`、`vocabulary.json`、`guard_rules.json` 等。
- **7 支自 `dsh-DIRECTOR-WORKER` 移植並對齊 `agydw` 的工具**：`safety_gate.py`、`gate_check.py`（整合 `TESTS_LOCKED`、`PLAN_APPROVED`、`SPEC_REVIEW`、`--report-skeleton`、`--final` 含 E2E `RESULT: FAIL` 防護與 `E2E_INCOMPLETE` 警示）、`scope_check.py`（`order_limits`、`protected_tests`、`--shared`、孤兒 `HEAD` 支援）、`memory_size_check.py`（預設找 `AGENTS.md`/`GEMINI.md` 並支援 `--kind rules` 24 KB 上限）、`metrics_report.py`、`status_gen.py`、`release.py`。
- **自動化測試**：`python -B -I -X utf8 tools/selftest_agy.py`。
