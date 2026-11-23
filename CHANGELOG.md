# CHANGELOG — agy-DIRECTOR-WORKER

本 repo 是 [DIRECTOR-WORK](https://github.com/scottpuppylu/DIRECTOR-WORK) `v2.6.0`（commit `787f67b`）在 Google Antigravity（App 2.0 / `agy` CLI / IDE / Python SDK）的原生移植版（簡稱 `agydw`），並承襲同作者 `dsh-DIRECTOR-WORKER`（`v2.6.0-ds.6`）的雙軌上游同步架構與安全閘門。
上游完整變更歷史保留在 [`CHANGELOG-upstream.md`](CHANGELOG-upstream.md)；本檔只記 `agy-DIRECTOR-WORKER` 自己的版本與「對齊初衷」。

## 2.6.0-agy.4（2026-11-24）

### 稽核真實性與多檔案清單（P1 / P2）
- **`tools/agy_audit.py`（重建 v2.6.0-agy.4）**：
  - 修正省略 `status` 欄位時預設當作 `"DONE"` 的問題；缺少明確終態（如 `status` 缺失或非終態且無明確完成／退出碼）統一標記為 `"unverified"`，使 `report_vs_audit.py` 對無終態 `exit=0` 聲明正確失敗。
  - 將 `extract_calls_detailed()` 的單遍 FIFO 改成每輪（turn）雙階段批次配對：第一階段跨整批事件優先依 `tool_call_id`／`call_id`／`id` 配對具名結果（失敗／中斷但找不到前置啟動步驟的孤立事件另記為 `unmatched_results` 避免被吞掉），第二階段再將剩餘匿名結果 1:1 配對尚未結案槽位，解決同一 turn 內「有 ID 與無 ID 混合步驟」互相錯配的問題。
  - 新增 `load_transcript_steps_with_sources()`：對主 `transcript.jsonl` 與伴隨 `transcript_full.jsonl` 各僅讀取一次並計算 SHA-256／bytes／lines，寫入 `source.transcripts` 供稽核清單完整溯源。

### 安裝狀態保留、並行鎖與零中斷工具升級（P1 / P2）
- **`tools/agy_setup.py`、`agy_setup_check.py`、`agy_doctor.py`、`plugin/director-work/scripts/seatbelt_hook.py`**：
  - **設定保留與快速會話路徑**：新增 `.agydw_setup_state.json` 狀態檔並搭配從 `.agents/hooks.json` 反推既定參數（`infer_options_from_hooks()`）；執行 `--register-director`／`--revoke-session` 等維護指令時保留既有 `--guard-mode`（如 `block`）、`--enforce-resume`、`--overwrite-utf8` 與自訂 `--audit-log`／`--resume-file`／`--bridge-state` 路徑，無結構變更時不重寫 `.agents/hooks.json`／`.agents/plugins.json`。
  - **並行註冊互斥鎖**：比照上游 `audit_log.py` 以 `os.O_CREAT | os.O_EXCL` 實作 `file_lock()`（預設等待 15 秒、陳舊鎖門檻 30 秒），保護 `.sessions.json` 與完整 `--apply` 提交，消除同時註冊多筆會話時的 lost update 競態。
  - **零中斷 Side-by-Side 升級**：工具改先寫入並驗證版本化目錄 `tools_v<VER>_<HASH8>`，再原子切換 `.agents/hooks.json` 指向該版本化目錄，最後才更新相容層 `<DIRECTOR>/tools/` 並保留 `.tools_backup_*` 回滾備份。
  - **跨目錄節點衝突預檢與誠實錯誤語義**：`validate_target_path_types()` 在寫入前先檢查目標路徑節點型別衝突（檔案 vs 目錄）；若通過預檢後在 OS 寫入階段發生 I/O 例外則誠實回報錯誤。
  - **舊版 `director_work.json` 遷移與非 ASCII 倉庫雜湊防撞**：`plan_director_config_migration()` 替舊設定補上 `project_git_common_dir` 與 `director_worktree`（同時保留所有未知／自訂欄位）；`default_director_dir()` 對含非 ASCII 字元的倉庫名附加 8 碼路徑雜湊（`DIRECTOR-<slug>-<hash8>`），`agy_setup_check.py --strict` 亦將缺少 `project_git_common_dir` 的 git 專案標為 shadow。

### 最終閘門、Worktree Stop 綁定與用量歸屬（P1 / P2）
- **`tools/gate_check.py`**：`--final` 預設拒絕只有 `ENTRY_POINT`／`started` 而缺少明確成功完成證據（`EXIT_CODE: 0`／`exit=0`／`RESULT: PASS`／`VERDICT: PASS`）的 `evidence/E2E/` 紀錄（除非明確傳入 `--allow-incomplete-e2e`）；另支援 `--candidate <40HEX>` 檢查通過的 E2E 證據是否引用該候選提交（前 7+ 碼）。
- **`tools/agy_hook_bridge.py`**：新增 `_git_common_dir()` 與 `resolve_stop_repo()`，當 Stop 事件帶入同一個 Git 倉庫底層物件庫的 `cwd` 或 `workspacePaths[0]`（含 worker linked worktree）時優先用於定位該工作區上下文。
- **`tools/agy_usage.py`**：若 session transcript 含結構化 `workspacePaths`／`cwd`／`workspace` 欄位，要求精確歸屬該工作區，不再因 `USER_INPUT` 文字提及而誤歸他案；同時在報告輸出 `attribution_mode`／`attribution_modes` 並於退回 `text_heuristic` 時提示。

### 文件、Skills 與雙平台 CI 對齊
- 全部 5 份 `plugin/director-work/skills/*/SKILL.md` 統一採用 `python -B -I -X utf8 <RULES>/tools/...` 與 `<DIRECTOR>/**/RESUME_STATE*.md` 單專案 glob，並修正 `dw-dispatch` 中 Worktree「獨立 clone」用詞為「隔離 Git worktree（底層共用 `.git` 物件庫）」。
- 更新 `tools/AGY-TOOLS.md`、`README.md`、`ANTIGRAVITY-PORT.md` 版號與工具數量盤點（`40` `.py` ＋ `2` `.json`），並在 GitHub Actions 加入無 `python` 別名、僅保留 `python3` 的 Ubuntu 自檢步驟。
- 自檢套件擴增為 25 組（含 12 輪雙程序並行註冊壓測與 skills/文件一致性檢查），保留上游 `39/39` 核心檔案與 `v2.6.0` 位元完全一致。

## 2.6.0-agy.3（2026-11-24）

針對獨立深度審查發現的「候選提交／執行終態／專案歸屬」三大綁定漏洞與 11 項實測缺陷進行徹底根治，並全面保留 39/39 份上游 `DIRECTOR-WORK v2.6.0` 核心檔 SHA-256 一致：

### 關鍵修正與強化
- **候選提交一致性綁定（`tools/scope_check.py`）**：
  - 指定 `--candidate` 時（非 `--shared` 模式），`SCOPE`、`FORBIDDEN`、`OUTSIDE`、`PROTECTED`、`SIZE` 與 `REQUIRED_CHANGES` 全部統一對齊同一份 `since..commit` 候選差異，徹底消除總監在乾淨 `main` 分支審查 worker SHA 時漏報違規的問題。
- **執行終態、關聯 ID 配對與尾部掃描預算（`tools/agy_audit.py`）**：
  - 顯式區分完成終態（`DONE` / `OK` / `SUCCEEDED` / `SUCCESS` / `COMPLETED`）與非終態（`RUNNING` / `PENDING` / `IN_PROGRESS` 或 `Process still running`），`RUNNING` 步驟記為 `result.status="running"`（不再誤標 `ok`，確保 `report_vs_audit.py` 拒絕假通過）。
  - `extract_calls_from_transcript` 優先依據 `tool_call_id` / `call_id` / `id` 關聯配對，不存在歧義 FIFO 錯配；`--include-subagents` 只從真實派工工具（`invoke_subagent` / `swarm` / `Agent` / `Task`）抽取子對話 ID；`_scan_tail_for_step` 與 `agy_hook_bridge.py` 採最大 2 MiB 不重疊視窗，避免缺失步驟時重讀並峰值配置整份大檔。
- **每專案獨立狀態目錄、會話角色綁定、版本升級與原子安裝（`agy_setup.py`、`agy_guard.py`、`agy_setup_check.py`、`agy_doctor.py`）**：
  - 預設狀態目錄改為每專案獨立 `<PRIMARY_REPO>/../DIRECTOR-<repo>`（同步支援連結 worktree 共用與舊版 `DIRECTOR/` 歸屬升級檢查），寫入 Git `common-dir` 指紋並由 `agy_setup_check.py` 跨專案拒檢；`info/exclude` 透過 `git rev-parse --git-path info/exclude` 正確寫入 Git common dir。
  - `agy_setup.py` 預設接通 `--sessions <DIRECTOR>/.sessions.json`（`default: "worker"`，只有透過 `--register-director <CID>` 註冊的總監對話才可寫 `DIRECTOR_LOG.md` 與 `orders/`，同時保護 `.sessions.json` 本身與修正 `BOOTSTRAP` `--protect` 參數），並在 POSIX（如含 `$` 路徑）改用 `shlex.quote`。
  - 實作實質預檢後再寫入的兩階段原子提交；從已複製的 `DIRECTOR/tools/agy_setup.py` 重跑可自動回溯 manifest/state 定位規則根目錄；重新執行 `agy_setup.py` 會比較每個工具檔案 SHA-256，備份舊版並升級 `DIRECTOR/tools/` 與 `.agents/plugins.json` 內舊版 `director-work` 路徑。
  - `agy_setup_check.py --strict` 與 `agy_doctor.py` 強制檢查 `enabled`、`matcher` 對核心工具覆蓋率及全部 4 個生命週期（`SessionStart`/`PostToolUse`/`PreInvocation`/`Stop`），停用或失效 Matcher 一律判 `FAIL` 且不報 `HOOK_EXEC_OK`，並回報實際啟用工具版本。
- **收尾閘門與續接橋接硬化（`tools/gate_check.py`、`tools/agy_hook_bridge.py`）**：
  - `gate_check.py --final` 會拒絕含非零退出碼或 `FAILED` / `ERROR:` / `Traceback` 的 E2E 紀錄，且缺漏 `totals.total_tokens` 的空 `{}` `usage.json` 回報 `WARN USAGE`（不再報 `ok`）。
  - `agy_hook_bridge.py stop-check` 同時轉接 `model_stop` / `END_TURN` / `fullyIdle` 與偵測已提交 `HEAD` 新於 `RESUME_STATE.md`（並附 `--state-dir` 單次防迴圈保護）；`pre-invocation` 以檔頭雜湊與大小偵測 transcript 輪替截短並重新注入 `RESUME_STATE`。
- **用量歸屬與盲審／外掛相容性（`tools/agy_usage.py`、`plugin/director-work/`）**：
  - `agy_usage.py --project-dir` 改採路徑邊界正則比對避免 `/app` 誤抓 `/app-long`，非數字 token 欄位安全跳過並計入 `invalid_metric_fields`。
  - `dw-blind-reviewer.md` 明確設 `inheritMcp: false` 與 `inheritCustomizations: false` 並於 `agy_doctor.py` 強制檢查；`plugin/director-work/hooks.json` 改用 `python ... || python3 ...` 相容僅有 `python3` 的 Linux/Ubuntu 環境。

## 2.6.0-agy.2（2026-11-24）

基於獨立第三方審查重現結果，針對首次安裝體驗、總監權限邊界、稽核誠實性與長對話效能進行關鍵修補：

### 關鍵修正與強化
- **外掛官方 Schema 對齊與獨立可攜式安全帶（`plugin/director-work/`）**：
  - 依 `https://antigravity.google/schemas/v1/plugin.json` 將 `plugin.json` 的 `repository` 由物件改為字串 URL，補齊 `$schema` 與 `license`，並讓 `agy_doctor.py` 執行完整官方 JSON Schema 欄位型別驗證。
  - 新增純標準庫 `plugin/director-work/scripts/seatbelt_hook.py` 並改寫 `hooks.json`：即使獨立複製或安裝 `plugin/director-work/`（無外部 `../../tools/`），仍可攔截憑證讀取、`G00`/`G43`/`G01`/`G02`；若偵測到完整 `DIRECTOR/tools/` 或 repo `tools/` 則自動委派給 `agy_guard.py`。
- **總監 `--protect` 權限修正（`tools/agy_guard.py`）**：
  - 修正 `--protect <DIRECTOR>` 同時阻擋 `director` profile 的問題，確保 `director` 可寫入 `DIRECTOR_LOG.md` 與 `orders/`，而 `worker` / `cleaner` 仍一律強制拒絕（`deny`）。
- **非破壞性合併安裝與誠實健檢（`agy_setup.py`、`agy_setup_check.py`、`agy_doctor.py`）**：
  - `agy_setup.py` 遇到已存在的 `.agents/hooks.json` 或 `.agents/plugins.json` 時，改為保留使用者既有群組與外掛清單、合併寫入 `agydw` 項目；遇毀損 JSON 直接 `exit 1` 失敗，不再假報 `DONE`。
  - `agy_setup_check.py` 實際檢查 `agydw-guard` / `agydw-audit-and-bridge` 與 `director-work` 是否真的在設定檔內（不再只驗 JSON 語法）；`agy_doctor.py` 移除過度樂觀的靜態 `ENFORCED` 標籤，區分 `CONFIGURED` 與實際透過 OS Shell 探測後的 `HOOK_EXEC_OK`。
- **稽核誠實化與效能優化（`tools/agy_audit.py`、`tools/agy_hook_bridge.py`）**：
  - `hook-append` 在既無 `error` 亦找不到對應輸出時改記 `result.status="unverified"`（不再假寫 `ok`），並於 `agy_audit.py verify` 輸出 `confirmed_ok` / `confirmed_error` / `unverified` 統計；同時相容 `args` 與 `arguments`（含 JSON 編碼字串）。
  - `agy_hook_bridge.py` 與 `agy_audit.py` 改採尾端分塊預篩與位元偏移量增量游標讀取 `transcript.jsonl`，避免長對話每次全檔重掃。
- **文件、範本與隔離描述修正＋跨平台 CI**：
  - 修正 `README.md`、`BOOTSTRAP.md`、`dw-review-gate/SKILL.md` 的 `evidence_record.py verify --rerun` 參數；補齊 `templates/TEST_REQUEST.md` 的 `v2.6.0` `CODE_REFS:` 等必填欄位；將 `docs/11` 與 `templates/WORK_ORDER.md` 的 `workspace: branch` 更正為準確的「隔離 Git worktree」。
  - 新增 `.github/workflows/ci.yml`（`windows-latest` ＋ `ubuntu-latest` 矩陣）與全面回歸測試於 `tools/selftest_agy.py`。

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
