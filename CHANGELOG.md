# CHANGELOG — agy-DIRECTOR-WORKER

本 repo 是 [DIRECTOR-WORK](https://github.com/scottpuppylu/DIRECTOR-WORK) `v2.6.0`（commit `787f67b`）在 Google Antigravity（App 2.0 / `agy` CLI / IDE / Python SDK）的原生移植版（簡稱 `agydw`），並承襲同作者 `dsh-DIRECTOR-WORKER`（`v2.6.0-ds.6`）的雙軌上游同步架構與安全閘門。
上游完整變更歷史保留在 [`CHANGELOG-upstream.md`](CHANGELOG-upstream.md)；本檔只記 `agy-DIRECTOR-WORKER` 自己的版本與「對齊初衷」。

## 2.6.0-agy.1（2026-11-24）

首個完整移植與端到端驗證版本（對齊上游 `DW v2.6.0` + `dshdw 2.6.0-ds.6`）。

### 新增與 Antigravity 原生升級
- **一等 Antigravity Plugin（`plugin/director-work/`）**：提供 `plugin.json`、精簡常駐 `rules/AGENTS.md`（約 4 KB，遠低於 24 KB 單檔上限）、5 個按需載入 Skills（`dw-bootstrap`、`dw-dispatch`、`dw-review-gate`、`dw-resume`、`dw-release`）、3 個自訂子代理（含只配賦 `read_file` 的物理唯讀盲審員 `dw-blind-reviewer`），以及外掛根目錄 `hooks.json` 安全帶。
- **雙協定與兩段式 Hook 鏈（`tools/agy_guard.py`、`tools/agy_hook_bridge.py`）**：
  - `PreToolUse` 包裝上游 `hook_guard.py` 與 `guard_rules.json`，同時支援 App 2.0 與 IDE/CLI 工具名、保護 `AGENTS.md`/`GEMINI.md`/`.agents/`（`G43`）與憑證（`G50`+`G53`），並於 `--mode ask` 回傳 `force_ask` 忽略 Always Allow 快取。
  - 針對 Antigravity `PostToolUse` 必須輸出 `{}` 的限制，實作「`PostToolUse` 排隊 → `PreInvocation` 注入 `injectSteps`」兩段式橋接，還原輪詢提醒、提示注入掃描與 `SYSTEM/CHECKPOINT` 壓縮後自動重注 `RESUME_STATE`，並新增防無限迴圈的 `Stop` hook（`stop-check`）。
- **Antigravity Transcript 用量與雜湊鏈稽核（`tools/agy_usage.py`、`tools/agy_audit.py`）**：
  - 依本機真實 `transcript.jsonl` 欄位實測驗證：`input_tokens` 為未快取輸入、`input_tokens + cache_read_tokens` 為該輪上下文大小。
  - `agy_audit.py` 同時支援 `PostToolUse` 即時寫入雜湊鏈（`hook-append`）與從 `transcript.jsonl` / `transcript_full.jsonl` 事後重建（`build`，含子代理 UUID 追蹤與非 0 退出碼偵測），產物可直接送交上游 `audit_log.py verify` 與 `report_vs_audit.py`。
- **專案一鍵設定與環境健檢（`tools/agy_setup.py`、`tools/agy_setup_check.py`、`tools/agy_doctor.py`）**：自動生成 `.agents/hooks.json`（含 Windows `cmd /c` 空白路徑防護）與 `.agents/plugins.json`，絕不手改全域 `config.json`。
- **四層編排與 `run_workflow` 範本（`docs/11_antigravity_operations.md`、`templates/workflows/`）**：完整定義 Orchestrator、Subagents（`share`/`branch`）、Swarm（Channels、即時額度查詢與保留歷史熱切換模型）與 `run_workflow` 的選用規則。
- **保留 27 支與 `DIRECTOR-WORK v2.6.0` 逐位元組相同的核心工具**，以及移植自 `dshdw` 的 `safety_gate.py`（`TESTS_LOCKED`、`PLAN_APPROVED`、`order_limits`、`protected_tests`）。移除 `tools/container/`（Docker 容器改由 Antigravity 原生 OS 沙箱 + `workspace: branch` 取代）。

### 誠實落差（`NOT_PORTED`，詳見 `ANTIGRAVITY-PORT.md` 第 7 節）
- 沒有專案級 `autoCompactWindow: 250000` 開關與免費 CLI `context_probe.py`。
- 沒有無頭帳號 5 小時／週額度百分比 API（但 Swarm 內建 `swarm{action:"models"}` 即時剩餘額度）。
- `PostToolUse` 無法像 Claude Code 原地回傳 `updatedToolOutput` 遮罩輸出；改以 `PreToolUse` 前置攔截密鑰讀取。
- 專案設定無法直接關閉使用者全域已安裝的 plugin。
- Windows 11 24H2+ 原生 AppContainer 沙箱對使用者家目錄外路徑預設仍可讀（不可寫）。

### 對齊初衷（`PURPOSE.md` 五題檢查）
1. **實戰解決的問題**：將原本高度依賴 Claude Code 設定檔與 Cloud Artifact 的總監制，完整搬上 Google Antigravity，並解除 Claude Desktop 子 session 無法直接開 worker、Windows 缺原生沙箱、盲審無法物理唯讀三大限制。
2. **實際使用與端到端驗證**：已通過 `tools/selftest_agy.py`，並在獨立測試專案完成真實 `agy_setup.py`、`safety_gate.py`、`dw-demo-worker` 與物理唯讀 `dw-demo-blind-reviewer` 子代理端到端派工與裁決（見 `ANTIGRAVITY-PORT.md` 第 8 節）。
3. **服務的初衷**：服務第 1（一條連結或一個外掛就開工）、第 2（Skills 漸進揭露省上下文）、第 4（`SessionStart`/`PreInvocation` 無損續航）、第 5（物理唯讀盲審 + `/browser` 實測）。
4. **九項核心檢查**：每輪必讀文件 `01 + 03 + 06 + 08` 合計嚴控於 9,550 bytes 以內；`Direct` 工單維持免規格審查與免安全閘門；PO 操作透過 `ask_question` 與單頁 `PO_PENDING.md` 更精簡。
5. **相容性**：27 支核心工具與 `DW v2.6.0` 逐位元組一致；`safety_gate.py` 與 `gate_check.py` / `scope_check.py` 沿用 `dshdw 2.6.0-ds.6` 格式。
