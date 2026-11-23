# agydw tools

`agy-DIRECTOR-WORKER`（簡稱 `agydw`，`2.6.0-agy.1`）在 Google Antigravity（App 2.0 / `agy` CLI / IDE / SDK）上的工具手冊。全部工具只使用 Python 3.9+ 標準函式庫，不需 `pip install`；一律用以下方式執行（`-I` 隔離本機匯入）：

```text
python -I -X utf8 tools/<tool>.py ...
```

與 `dsh-DIRECTOR-WORKER` 最大差異：**Antigravity 原生具備完整的生命週期 Hooks（`SessionStart`、`PreToolUse`、`PostToolUse`、`PreInvocation`、`PostInvocation`、`Stop`）、Plugin 封裝、自訂子代理工具白名單、Swarm 共享留言板，以及三平台 OS 原生終端沙箱。** 因此這批工具直接提供自動執行期防護與可驗證的雜湊鏈紀錄，而不只是事後補查。

## Antigravity 專屬新工具（`agy_*.py`）

- **`agy_guard.py`** — 雙協定 `PreToolUse` 守衛（同時吃 Antigravity `camelCase` 與 Claude `snake_case`）：
  - 包裝上游 `hook_guard.py` 與 `guard_rules.json`（不改動上游規則引擎），並同時覆蓋 App 2.0（`run_command`、`read_file`、`write_file`、`edit_file`）與 IDE/CLI（`view_file`、`write_to_file`、`replace_file_content`、`multi_replace_file_content`、`grep_search`、`find_by_name`、`list_dir`）兩套工具名稱。
  - 保護 Antigravity 設定與規則（`G43`：`.agents/hooks*.json`、`plugins.json`、`AGENTS.md`、`GEMINI.md`、`~/.gemini/config/**`）與憑證（`G50` + `G53`），並支援 `--protect DIRECTOR` 鎖定 `orders/` 與 `DIRECTOR_LOG.md` 寫入。
  - 在 `--mode ask` 下輸出 Antigravity 的 `"decision": "force_ask"`（忽略 Always Allow 快取，強迫敏感操作如 `git push` 由 PO 現場核准）；`--mode remind` 下回傳 `"allow"` 並可透過 `--bridge-state` 在下一輪 `PreInvocation` 注入提醒；Antigravity 格式固定輸出 JSON 並以 exit `0` 回傳。
  - 選配 `--overwrite-utf8`：示範 `PreToolUse.overwrite`，將裸 `python script.py` 自動改寫成 `python -I -X utf8 script.py`。

- **`agy_hook_bridge.py`** — 兩段式 Hook 橋接器（補足 Antigravity `PostToolUse` 只能回 `{}` 的協定限制）：
  - `session-start`：`SessionStart` 時透過 `resume_inject.py` 注入 `RESUME_STATE.md` 四行表頭與未結段（最多 `--max-bytes`，預設 2048），可選 `--banner` 輸出零模型消耗的 `metadata.user_markdown`。
  - `post-tool`：`PostToolUse` 時偵測連續輪詢 log（`poll_hint`）並讀取 `transcriptPath` 對應 `stepIdx` 的輸出執行 `inject_scan.py` 提示注入掃描，有警示時寫入 `--state-dir/pending_<cid>.jsonl`，stdout 嚴格印出 `{}`。
  - `pre-invocation`：`PreInvocation` 時原子性取出待注入提醒，並比對 `transcript.jsonl` 是否新增 `SYSTEM / CHECKPOINT` 壓縮步驟，若有壓縮則自動重注 `RESUME_STATE.md`。
  - `stop-check`：`Stop` 時先檢查 `continuationActive == true` 或 `terminationReason != "NO_TOOL_CALL"` 立即放行（防止無限迴圈）；加 `--enforce-resume` 時，若工作樹檔案在 `RESUME_STATE.md` 之後有未記錄的修改，會擋回一次要求更新表頭。

- **`agy_audit.py`** — 雙模式動作稽核與迴圈/注入掃描：
  - `hook-append --log FILE`：`PostToolUse` 即時寫入與 `audit_log.py` 完全相容的 SHA-256 雜湊鏈（將 `run_command` 正規化為 `PowerShell`/`Bash` + `input.command` 並保留 `tool_raw: "run_command"`），stdout 印 `{}`。
  - `build --out FILE [--include-subagents]`：從 `transcript.jsonl` / `transcript_full.jsonl` 事後重建可驗證的稽核鏈（`kind=rebuild` 在 seq 1），自動解包 `PLANNER_RESPONSE.tool_calls` 雙重 JSON 引號參數、從 `Created At`/`Completed At` 算耗時、偵測 `Command exited with code N.`（非 0 即 `failure`），並追蹤 `invoke_subagent` 輸出的子對話 UUID；產物可直接交由 `audit_log.py verify` 與 `report_vs_audit.py` 驗證。
  - `verify`：直接轉交 `audit_log.py verify`。
  - `loops [--reads] [--since YYYY-MM-DD] [--until YYYY-MM-DD]`：呼叫上游 `loop_guard.py` 檢查 `repeat` / `retry_same_error` / `poll_no_progress` / `ping_pong` / `bad_args` / `child_error_unhandled`，加 `--reads` 另統計最常重讀的檔案路徑。
  - `inject`：以 `inject_scan.scan_text` 掃描 transcript 工具輸出是否含提示注入。

- **`agy_usage.py`** — 用量與上下文統計：
  - 讀取 `~/.gemini/antigravity/brain`、`antigravity-cli/brain`、`antigravity-ide/brain` 的 `transcript.jsonl`。
  - 依據實測驗證的欄位語意計算：未快取輸入 `input_tokens`、快取命中 `cache_read_tokens`、活躍上下文大小 `input_tokens + cache_read_tokens`（峰值與最後一輪）、`output_tokens`、快取命中率、模型與 effort 分佈、`CHECKPOINT` 次數、每日彙總、`--loops` 與 `--effort-check ORDER`。
  - 找不到資料時印出 `NO_DATA` 並 exit `2`（若有 `--out` 會寫入 `{"status": "NO_DATA", ...}` 以便 `gate_check.py --final` 讀取）。

- **`agy_doctor.py`** — 環境與防線實機健檢：
  - 檢查 Python、Git、OS 原生終端沙箱（Windows 11 build >= 26100 AppContainer、macOS Seatbelt、Linux userns；並誠實標示 Windows 家目錄外路徑仍可讀的邊界）、`agy` CLI（僅用桌面 App 時報 `INFO` 不算錯）、transcript 目錄、`.agents/hooks.json` 語法與 `agy_guard.py` **實跑探測**（`git push --force`、`read_file .env`、`git status`）、Plugin 清單與 `agents/*.md` 工具名稱合法性（抓出會導致卡住的未映射工具名）、專案規則 `<= 24,000` bytes、`--config` 與 `--licenses`。

- **`agy_setup.py`** & **`agy_setup_check.py`** — 專案建置與驗證：
  - `agy_setup.py`：預設 dry-run，加 `--apply` 冪等寫入 `<REPO>/.agents/hooks.json`、`<REPO>/.agents/plugins.json`（指向 `<RULES>/plugin`）、將本機私用檔加入 `.git/info/exclude`，並支援 `--init-director` 建立 `DIRECTOR/` 骨架；**絕不觸碰使用者全域 `~/.gemini/config/config.json`**。
  - `agy_setup_check.py`：檢查 `DIRECTOR/` 必要檔案、`DIRECTOR_STATE.md` 的 `PENDING_METHOD`、`PO_PENDING.md`、`RESUME_STATE.md` 表頭與大小上限、`director_work.json` 與 `.agents/*.json`（預設 shadow 模式，`--strict` 才 fail）。

## 沿用上游與安全閘門工具

- **27 支與 `DIRECTOR-WORK v2.6.0` 逐位元組相同的核心工具**：包含 `audit_log.py`、`hook_guard.py`、`loop_guard.py`、`inject_scan.py`、`resume_inject.py`、`report_vs_audit.py`、`evidence_record.py`、`dispatch_check.py`、`trace_report.py`、`round_check.py`、`ruler_check.py`、`run_plan_check.py`、`pack_check.py`、`pre_tr.py`、`delete_guard.py`、`resume_archive.py`、`waiver.py`、`secret_mask.py`、`vocabulary.json`、`guard_rules.json` 等。
- **7 支自 `dsh-DIRECTOR-WORKER` 移植並對齊 `agydw` 的工具**：`safety_gate.py`、`gate_check.py`（整合 `TESTS_LOCKED`、`PLAN_APPROVED`、`SPEC_REVIEW`、`--report-skeleton`、`--final`）、`scope_check.py`（`order_limits`、`protected_tests`、`--shared`）、`memory_size_check.py`（預設找 `AGENTS.md`/`GEMINI.md` 並支援 `--kind rules` 24 KB 上限）、`metrics_report.py`、`status_gen.py`、`release.py`。
- **自動化測試**：`python -I -X utf8 tools/selftest_agy.py`。
