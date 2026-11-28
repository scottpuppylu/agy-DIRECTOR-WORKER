# Antigravity 轉接層（`adapters/antigravity/`）

本目錄是 `DIRECTOR-WORK 3.0.0` 的 Antigravity（App 2.0 / IDE / CLI）變體轉接層（版本 `3.0.0-agy.7`），與上游純規則／治理基底分工：**基底 `tools/` 原樣保留，平台適配、Hook 與外掛全部集中於 `adapters/antigravity/`**。

## 一、與基底 `tools/` 的分工（新增／沿用上游／被取代）

| 類別 | 檔案 | 說明 |
|---|---|---|
| **新增（Antigravity 轉接層）** | `spec_gate_hook.py`、`GATE_RULES.json` | **R6 初衷卡嚴格閘門**（一般與 `--beginner` 皆安裝）；每次寫檔／建分支／非唯讀命令／動手子代理即時呼叫 `spec_gate.py`，不建快取、Fail-Closed |
| **新增** | `agy_common.py` | 轉接層共用輔助：委派 `tools/_common.py` 的原子寫檔、Git root／worktree／common dir 探索、JSONL 尾端掃描、`G2` exit code 判定、與 `D7` usage 數值檢查 |
| **新增** | `agy_guard.py` | Antigravity PreToolUse 安全帶：正規化 `run_command` / `read_file` / `find_by_name` / `call_mcp_tool` / `mcp_*` 等工具後載入 `tools/hook_guard.py` 與 `guard_rules.json`，補上 `G43`（`.agents/`、`.agent/rules/`）、`G53`（檔案與 shell 讀 oauth 憑證）、PowerShell `-Path:` 繞過、`--protect DIRECTOR/` 與 `--hard-only` |
| **新增** | `agy_hook_bridge.py` | **G3** 單一進程 `post-tool`（一次讀取 stdin / transcript 完成 `audit_log` + `poll_hint` + `inject_scan`）、`session-start`、`pre-invocation`（壓縮偵測與警報匯出）與 **G6** `stop-check`（`same_repo` 區分總監與 linked worktree worker） |
| **新增** | `agy_audit.py`、`agy_usage.py` | `transcript.jsonl`（必要時回退 `transcript_full.jsonl`）雜湊鏈重建、迴圈／注入掃描與單遍串流 Token 統計（**G7** `workspacePaths` 歸因 + **D7** `usage_number`/`partial`） |
| **新增** | `agy_setup.py`、`agy_setup_check.py`、`agy_doctor.py` | 一鍵在 `<REPO>/DIRECTOR/`（**R3**）初始化與升級、部署 `<REPO>/.agent/rules/director-work.md`、呼叫 `tools/install.py` 與 SHA-256 校驗複製轉接層、合併 `.agents/{hooks,plugins}.json` 與活體探針 |
| **新增** | `rules/director-work.md`、`hooks.antigravity.json`、`plugin/director-work/`、`workflows/`、`fixtures/` | 工作區規則範本、精簡外掛（<= 8 KB，**G5**；內建 `seatbelt_hook.py` fallback）、並行 `gate_verify_workflow.py`（保留 `af0e859` 單一 exit code 檢查與 Windows 防錯） |
| **新增** | `agy_version.py`、`base_sync_check.py`（轉接入口）、`VARIANT.json`、`BASE_SYNC.json`、`selftest_antigravity.py` | `-agy.N` 單調遞增檢查、轉接委派基板 `tools/base_sync_check.py`、`VARIANT.json` 設定預設強度（B15）與規則段落納入 24 KB 必讀鏈（B19），以及轉接層全套自檢 |
| **沿用上游** | `tools/_common.py`、`spec_gate.py`、`gate_check.py`、`scope_check.py`、`safety_gate.py`、`hook_guard.py`、`audit_log.py`、`loop_guard.py`、`inject_scan.py`、`resume_inject.py`、`install.py`、`bootstrap_repo.py`、`handoff.py`、`delivery.py`、`privacy_scan.py`、`memory_size_check.py`、`base_sync_check.py`、`readme_check.py`、`evidence_record.py` 等 | 全部沿用 `3.0.0`（`0e9ae22`）凍結版；B1（`scope_check --candidate`）、B2（`RESULT: FAIL`＋`EXIT_CODE: 0`）、B4（`order_limits`/`protected_tests`）已併入基板，轉接層不再保留重複副本 |
| **平台取代（未於 Antigravity 使用）** | `setup_project_settings.py`、`poll_hint.py`（CLI 獨立入口）、`transcript_audit.py`、`usage_report.py`、`doctor.py` | 檔案留在 `tools/` 以保持與基底同步；Antigravity 工作流改由 `agy_setup.py`、`agy_hook_bridge.py`、`agy_audit.py`、`agy_usage.py`、`agy_doctor.py` 承接 |

## 二、安裝與驗證順序（R3 專案內目錄＋`.agent/rules/`）

1. 先預覽安裝計畫（不寫入檔案）：
   ```powershell
   python -B -I -X utf8 <RULES>/adapters/antigravity/agy_setup.py --repo <REPO> --init-director
   ```
2. 確認無誤後正式寫入（會在 `<REPO>/DIRECTOR/` 建立總監目錄、`DIRECTOR/.gitignore` 與 `.git/info/exclude` 排除、透過 `tools/install.py` 安裝 `DIRECTOR/tools/`、複製並雜湊校驗 `DIRECTOR/adapters/antigravity/`、寫入 `.agent/rules/director-work.md` 與 `.agents/{hooks,plugins}.json`）：
   ```powershell
   python -B -I -X utf8 <RULES>/adapters/antigravity/agy_setup.py --repo <REPO> --init-director --register-director <CID> --apply
   ```
   - 小白模式加 `--beginner`：強制安裝 `agydw-spec-gate`、專案規則檔與 **`agydw-guard --hard-only`**（硬性規則 `G00`／`G50`–`G53`／`PROTECT`／worker `G43` 照樣拒絕，僅放寬一般提醒與詢問）。
   - 若僅驗證而不複製工具，可傳 `--no-copy-tools`（之後重跑 `--copy-tools --apply` 可隨時切回本機複製模式）。
3. 嚴格檢查與實跑探針：
   ```powershell
   python -B -I -X utf8 DIRECTOR/adapters/antigravity/agy_setup_check.py --strict DIRECTOR --repo <REPO>
   python -B -I -X utf8 DIRECTOR/adapters/antigravity/agy_doctor.py --repo <REPO> --probe-hooks
   ```

## 三、R6 與已知邊界（不誇大能力）

- **`spec_gate_hook.py` 原則**：讀不到或解析失敗的 stdin JSON、`GATE_RULES.json`、`spec_gate.py`，或缺少 `cwd` 的相對寫入路徑，一律回傳 `{"decision": "deny"}`（Fail-Closed）；永遠即時呼叫 `spec_gate.check()`，不寫通過快取。
- **就算初衷卡已 PASS 仍永遠攔截**：`spec_gate.py approve`、`gate_check.py --snapshot`，以及寫入 `DIRECTOR/tools/**`、`DIRECTOR/adapters/**`、`DIRECTOR/brief-history/**`、`.agents/hooks*.json`、`.agents/plugins.json`、`seatbelt_hook.py`。
- **宿主與工具已知限制**：
  1. Antigravity `.agents/hooks.json` 的外部執行契約不同於 Claude Code 的 `onFailure: "block"`；若 Python 直譯器本身未能啟動，仍受宿主環境制約，應同時搭配 `request review`／OS 權限／worktree 隔離。
  2. 透過腳本檔內容（如 `bash ./script.sh` 或未經 hook 的背景行程）執行的修改無法在 PreToolUse 靜態看穿。
  3. 透過 `send_message` 續用既有子代理與 `3.0.0` 同屬已知邊界；重要狀態應落地到 `DIRECTOR/INDEX.md` 與 `RESUME_STATE.md`。

## 四、同步與版本紀律

- `adapters/antigravity/base_sync_check.py --base <BASE_DIR>`（薄入口委派基板 `tools/base_sync_check.py`）核對 `BASE_VERSION`、`CHANGELOG-upstream.md` 與全部基底檔案 SHA-256，確保只覆寫 `README.md`、`CHANGELOG.md`、`VERSION`、`tools/selftest.py`。
- `adapters/antigravity/VARIANT.json` 宣告變體預設強度（`standard`，B15）與 `.agent/rules/director-work.md` DW 段落計入 24 KB 必讀鏈（B19）。
- `agy_version.py` 強制 `VERSION` 沿用 `<BASE_VERSION>-agy.<N>` 遞增序號（如 `2.6.0-agy.6` -> `3.0.0-agy.7`，永不歸 `1`）。
- 完整轉接層測試：`python -B -I -X utf8 adapters/antigravity/selftest_antigravity.py`。
