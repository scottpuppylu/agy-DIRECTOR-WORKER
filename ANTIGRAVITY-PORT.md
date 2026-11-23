# ANTIGRAVITY-PORT — `DIRECTOR-WORK v2.6.0` 到 Antigravity 的設計對照與實證紀錄

本文件記錄 **agy-DIRECTOR-WORKER（`agydw` `2.6.0-agy.1`）** 如何從 [scottpuppylu/DIRECTOR-WORK](https://github.com/scottpuppylu/DIRECTOR-WORK)（`v2.6.0`）移植到 **Google Antigravity**，同時參考 [scottpuppylu/dsh-DIRECTOR-WORKER](https://github.com/scottpuppylu/dsh-DIRECTOR-WORKER) 的架構經驗：保留乾淨的上游可同步核心，並將 Antigravity 的原生平台能力轉化為實質治理優勢。

---

## 1. 移植原則與上游邊界（Option 1）

為了讓日後上游 `DIRECTOR-WORK` 釋出新版時可以精準同步、不被平台適配碼污染，本 repo 採取嚴格分層：

1. **39 份完全一位元不差（byte-identical）的上游核心檔**：
   - 根目錄：`LICENSE`、`PURPOSE.md`、`CHANGELOG-upstream.md`（對應 `DIRECTOR-WORK v2.6.0` 的 `CHANGELOG.md`）。
   - 純範本（9 份）：`templates/CPU_REPRO.md`、`GATE_FEASIBILITY.md`、`HANDOFF.md`、`pre_tr.json`、`run_closing.py`、`SETTINGS_MANIFEST.md`、`weird_inputs.py`、`.gitleaks.toml`、`.pre-commit-config.yaml`。
   - 純核心工具（27 份）：`audit_log.py`、`completion.py`、`delete_guard.py`、`dispatch_check.py`、`evidence_record.py`、`flag_check.py`、`guard_rules.json`、`hash_check.py`、`hook_guard.py`、`inject_scan.py`、`line_brief.py`、`loop_guard.py`、`pack_check.py`、`pause_ctl.py`、`pre_tr.py`、`ran_line.py`、`report_vs_audit.py`、`resume_archive.py`、`resume_inject.py`、`round_check.py`、`ruler_check.py`、`run_plan_check.py`、`secret_mask.py`、`sim_check.py`、`trace_report.py`、`vocabulary.json`、`waiver.py`。
   - 所有上述 39 份檔案均以 SHA-256 與 `DIRECTOR-WORK` tag `v2.6.0` 比對驗證，**禁止直接修改**。
2. **適配層（7 支治理工具）**：在盡量相容 CLI 介面的前提下，適配 Antigravity 路徑、工具名稱、`ask_question` 待決機制與 24 KB 規則上限。
3. **Antigravity 原生整合層（7 支 `tools/agy_*.py`＋`plugin/director-work/`＋`templates/workflows/`）**：以組合包裝（composition）方式呼叫未修改的上游工具（`hook_guard.py`、`audit_log.py`、`loop_guard.py`、`inject_scan.py`、`resume_inject.py`）。

---

## 2. 實測驗證過的 Antigravity 平台事實

以下每一點皆在本機開發環境與真實 Antigravity 執行紀錄（`transcript.jsonl`）實際查證，並非猜測文件：

1. **Hooks JSON 命名與合約（`.agents/hooks.json` 與 plugin `hooks.json`）**：
   - 事件鍵名為駝峰命名：`PreToolUse`、`PostToolUse`、`SessionStart`、`PreInvocation`、`Stop`（而非全小寫）。
   - `PreToolUse` / `PostToolUse` 使用命名群組（named group → `{matcher, hooks}`），而 `SessionStart` / `PreInvocation` / `Stop` 在命名群組內直接放 hook 物件陣列（`[{type, command, timeout}]`）。
   - `PreToolUse` 支援五種決策：`allow`、`auto_approve`、`deny`、`ask`、`force_ask`，以及改寫工具輸入的 `overwrite`；為確保 Antigravity 正常讀取 JSON 決策而不是將 hook 視為執行錯誤崩潰，`agy_guard.py` 在 Antigravity 模式下總是輸出 JSON 於 `stdout` 並回傳 `exit 0`。
   - `PostToolUse` 合約要求 `stdout` 輸出 `{}`，無法像 Claude Code 一樣直接把文字注入該回合模型上下文；因此提示警告透過兩段式設計橋接至緊接而來的 `PreInvocation`（`injectSteps` 含 `systemMessage`）。
   - `Stop` Hook 若直接回傳 `decision: "block"` 而未檢查 `continuationActive`，容易引發無限迴圈；`agy_hook_bridge.py stop-check` 在 `continuationActive == true` 或 `terminationReason != "NO_TOOL_CALL"` 時一律立即放行 `allow`。
2. **Windows `cmd /c` 執行 Hook 的雙引號剝除陷阱**：
   - Windows 上 Antigravity 透過 `cmd /c <command>` 執行 Hook，作業系統的工作目錄預設為 `hooks.json` 所在資料夾。
   - 若指令字串以 `"` 開頭**且**以 `"` 結尾（例如 `"C:\...\python.exe" -I -X utf8 "D:\...\script.py"`），`cmd /c` 會先剝除最外層一對引號，導致內部引號全部錯位失敗。
   - 解法：`plugin/director-work/hooks.json` 採用無空白相對路徑 `python -I -X utf8 ../../tools/agy_guard.py ...`；`agy_setup.py` 僅在路徑含空白或特殊字元時加引號，且當第一段（`python.exe`）被加引號時於最前方自動加上 `call `，並納入 `agy_doctor.py --probe-hooks` 與 `selftest_agy.py` 實測回歸驗證。
3. **`transcript.jsonl` Token 與事件語意**：
   - 對話紀錄位於 `<appDataDir>/brain/<conversation-id>/.system_generated/logs/transcript.jsonl`（必要時對應完整欄位檔 `transcript_full.jsonl`）。
   - 經實測 `PLANNER_RESPONSE` 的 token 欄位語意為：`input_tokens` = 未快取輸入 token；`cache_read_tokens` = Prompt Cache 命中 token；`output_tokens` = 輸出（含思考）token；因此**該回合實際總上下文大小 = `input_tokens + cache_read_tokens`**。
   - 工具呼叫的 `tool_calls[].arguments` 在紀錄中可能為 JSON 編碼字串，解析時需再解碼；緊隨在後的 `GENERIC` 步驟記錄工具輸出。失敗的 `run_command` 在紀錄上 `status` 仍為 `"DONE"`，但 `content` 會包含 `Command exited with code N.`（`N != 0`）。壓縮事件會寫入 `type="CHECKPOINT"`。`invoke_subagent` 成功後回傳 JSON 含 `"conversationId": "<uuid>"`。
4. **規則與外掛限制**：
   - 單一規則檔案硬上限 **24,000 bytes**；系統提示詞總預算為 20,000 tokens。本外掛 `plugin/director-work/rules/AGENTS.md` 控制在 ~4 KB（`< 8,500` B），且四份每輪必讀文件合計 `<= 9,550` B。
   - `plugin.json` 僅允許嚴格頂層欄位（`name`、`description`、`logo`、`suggestedPrompts` 上限 3 筆等），`rules/AGENTS.md` 不帶 YAML frontmatter。
   - **絕不編輯使用者全域 `~/.gemini/config/config.json`**；專案掛載透過專案內 `.agents/plugins.json`。
5. **自訂子代理 YAML `tools:` 防掛死守則**：
   - Antigravity 2.0 若在子代理 `.md` 的 YAML `tools:` 欄誤寫未映射或舊版工具名（如 `view_file`、`grep_search`），啟動後可能無限等待；應使用明確有效的核心工具名（如 `dw-blind-reviewer.md` 僅列 `[read_file]`）。
6. **OS 原生沙箱（無須 Docker）**：
   - Antigravity 內建跨平台系統沙箱（Linux namespaces、macOS `sandbox-exec`、Windows 11 24H2+ `AppContainer`）。注意 Windows AppContainer 下工作區與使用者設定檔之外預設為「可讀不可寫」，因此機密防護仍須搭配 `PreToolUse`（`agy_guard.py`）。

---

## 3. Claude Code vs DSH (`dshdw`) vs Antigravity (`agydw`) 能力矩陣

| 治理機制 | Claude Code (`DIRECTOR-WORK`) | DeepSeek Harness (`dshdw`) | Google Antigravity (`agydw`) | 優勢來源與實作說明 |
|---|---|---|---|---|
| **事前指令／檔案攔截** | `PreToolUse` (`hook_guard.py`) | 無（事後稽核） | **有**：`PreToolUse` (`agy_guard.py`) | 支援 `allow` / `force_ask` / `deny` / `overwrite`，並保護 `DIRECTOR/orders` 與 `DIRECTOR_LOG.md` |
| **環境與分支隔離** | `git worktree`＋Docker 容器 | 共用工作副本＋單一寫入者紀律 | **OS 原生沙箱＋`workspace: "branch"`／`"share"`** | 免裝 Docker 即可平行隔離寫碼與系統寫入邊界 |
| **Tool 後提醒／注入檢查** | `PostToolUse` 直接吐訊息 | 無（事後掃描） | **2-stage 橋接**：`PostToolUse` 排隊 + `PreInvocation` `injectSteps` | 補償 Antigravity `PostToolUse` 必須回 `{}` 的協定限制 |
| **壓縮後強制恢復** | `SessionStart` / `compact` | 無（依賴 AI 主動讀檔） | **有**：`SessionStart` + `PreInvocation` 偵測 `CHECKPOINT` | 自動重注 `RESUME_STATE.md` 前 4 行與未結段落 |
| **物理唯讀盲審員** | 工具集限制 | 無法限制工具（只靠提示詞） | **原生 YAML `tools: [read_file]`** | `dw-blind-reviewer` 在引擎層就沒有 `write_file`/`run_command` |
| **視覺與網頁自動化驗收** | 依賴截圖腳本 | 手動貼圖 | **`browser` / `dw-visual-qa` + `BROWSER_HOST_FACTS`** | 宿主獨立驗證實際訪問站台與截圖證據，不單信子代理口述 |
| **平行與管線編排** | Shell / 腳本 | `subagent` 手動派工 | **原生 Swarm 看板＋Python `run_workflow`** | 支援多作者 `share` 工作副本、`fork` 歷史與 `parallel`/`pipeline` 型別驗證 |
| **PO 互動決策** | Web Choice Page＋置頂提醒 | `ask_user_question`＋`PO_PENDING.md` | **`ask_question`＋`PO_PENDING.md`** | 對話內即時表單互動＋本機長期可追溯待決帳 |
| **Token 與動作稽核** | JSONL 解析 | `dsh_usage.py` / `dsh_audit.py` | **`agy_usage.py` / `agy_audit.py`** | 解析 `transcript.jsonl`（含子代理遞迴追蹤與雜湊鏈驗證） |

---

## 4. 文件調整與容量控制

- **必須合併讀量預算**：`docs/01_roles_authority.md` + `docs/03_communication.md` + `docs/06_resume_lossless.md` + `docs/08_safety_rules.md` 總位元數嚴格壓在 **`<= 9,550` bytes**（實測 `7,570` bytes）。
- **新增／大幅改寫文件**：
  - [`docs/11_antigravity_operations.md`](docs/11_antigravity_operations.md)：Antigravity 工具對照、App/IDE/CLI 雙命名映射、`workspace: branch|share|inherit`、Swarm 與 `run_workflow` 使用指南、OS 沙箱與 Windows `cmd /c` 實戰須知。
  - [`docs/04_quota_efficiency.md`](docs/04_quota_efficiency.md)、[`docs/05_models_effort.md`](docs/05_models_effort.md)、[`docs/06b_on_demand_procedures.md`](docs/06b_on_demand_procedures.md)、[`docs/07_quality_gates.md`](docs/07_quality_gates.md)、[`docs/08_safety_rules.md`](docs/08_safety_rules.md)、[`docs/09_memory.md`](docs/09_memory.md)、[`docs/12_chat_to_product.md`](docs/12_chat_to_product.md)、[`docs/13_safety_gates.md`](docs/13_safety_gates.md)：全面替換 Claude / DSH 專有介面為 Antigravity 等價機制並保留全部 `vocabulary.json` 關鍵詞。
  - [`docs/10_lessons_learned.md`](docs/10_lessons_learned.md)：完整保留上游 DW v2.6.0 全部 134 條實戰教訓並於頁首補上 Antigravity 適用註記。
  - [`plugin/director-work/rules/AGENTS.md`](plugin/director-work/rules/AGENTS.md)：精簡至 `3,981` bytes（`< 8,500` B，遠低於 Antigravity 24 KB 單檔上限）。

---

## 5. 工具清單與分層總表

| 分類 | 數量 | 檔案名稱 | 說明 |
|---|---|---|---|
| **上游完全原樣 (Byte-identical)** | 27 | `audit_log.py`, `completion.py`, `delete_guard.py`, `dispatch_check.py`, `evidence_record.py`, `flag_check.py`, `guard_rules.json`, `hash_check.py`, `hook_guard.py`, `inject_scan.py`, `line_brief.py`, `loop_guard.py`, `pack_check.py`, `pause_ctl.py`, `pre_tr.py`, `ran_line.py`, `report_vs_audit.py`, `resume_archive.py`, `resume_inject.py`, `round_check.py`, `ruler_check.py`, `run_plan_check.py`, `secret_mask.py`, `sim_check.py`, `trace_report.py`, `vocabulary.json`, `waiver.py` | 與 `DIRECTOR-WORK v2.6.0` SHA-256 一致，零改動 |
| **治理適配版** | 7 | `safety_gate.py`, `gate_check.py`, `scope_check.py`, `memory_size_check.py`, `metrics_report.py`, `status_gen.py`, `release.py` | 整合 `TESTS_LOCKED` / `PLAN_APPROVED`、`--kind rules` 24 KB 上限、`agy_usage.py` / `agy_audit.py`、`ask_question` 與 Antigravity 洩漏模式 |
| **Antigravity 原生工具** | 7 | `agy_guard.py`, `agy_hook_bridge.py`, `agy_audit.py`, `agy_usage.py`, `agy_doctor.py`, `agy_setup.py`, `agy_setup_check.py` | 僅用 Python 標準函式庫；完整文件見 [`tools/AGY-TOOLS.md`](tools/AGY-TOOLS.md) |
| **整合測試套件** | 1 | `selftest_agy.py`（＋ `tools/fixtures/`） | 驗證上游 SHA-256、Hook 雙協議、Windows `cmd /c` 含空白路徑、兩段式橋接、稽核用量、外掛結構與隱私掃描 |

---

## 6. 外掛、模板與 Workflow 編排設計

- **Plugin 結構 (`plugin/director-work/`)**：
  - `plugin.json`：遵循嚴格頂層 schema，附 3 條繁體中文建議啟動詞。
  - `hooks.json`：無需手動編修路徑即可啟動 `PreToolUse` 基本安全帶（跨 OS 相對路徑設計）。
  - `skills/`（5 個按需技能）：`dw-bootstrap`、`dw-dispatch`、`dw-review-gate`、`dw-resume`、`dw-release`。
  - `agents/`（3 個專屬子代理）：`dw-worker.md`（寫作施工）、`dw-blind-reviewer.md`（唯讀 `tools: [read_file]`）、`dw-visual-qa.md`（強調 `BROWSER_HOST_FACTS` 與跨畫面比較）。
- **Workflow Fan-Out 範本 (`templates/workflows/`)**：
  - `gate_verify_workflow.py`：用 Antigravity Python `run_workflow` 並聯驗證多個模組／AC 並彙總結構化 verdict JSON。
  - `parallel_spec_review_workflow.py`：分階段在派工前同時進行規格矛盾檢查、邊界壓力檢查與字面期望值手算覆核。

---

## 7. 未移植項目 (`NOT_PORTED`) 與已知風險

| 項目 | 處置 | 原因與替代方案 |
|---|---|---|
| **Docker 容器 Worker (`tools/container/*`)** | **`NOT_PORTED`（經 PO 核准移除）** | Antigravity 原生提供 OS 沙箱（AppContainer / `sandbox-exec` / Linux namespaces）與 `workspace: "branch"` / `"share"`，不需額外維護跨平台 Docker-in-Windows 負擔 |
| **Claude 專用 Web Choice Page (`choice_page.html`)** | **由原生互動取代** | 改由 Antigravity `ask_question`＋本機 `PO_PENDING.md` 長期紀錄帳本取代 |
| **全域自動開啟外掛** | **刻意不自動修改** | `~/.gemini/config/config.json` 為使用者所有專案共享，改由專案級 `.agents/plugins.json` 或 PO 手動控制 |
| **`transcript.jsonl` 內部格式未來變動風險** | **已緩解** | `agy_audit.py` 與 `agy_usage.py` 採用寬容欄位讀取、同時支援 `transcript_full.jsonl` 補讀並提供 fixture 回歸測試；若資料無法解析誠實標記 `UNKNOWN` / `NOT_VERIFIED` |
| **Windows 沙箱外圍唯讀特性** | **分層防禦** | 透過 `agy_guard.py` 阻擋讀取常見憑證檔、`.pre-commit` / `.gitleaks` 掃描及 `release.py` 雙重把關 |

---

## 8. 端到端實戰驗證紀錄（Live E2E Verification）

本節記錄在獨立沙盒示範工作區（`agydw-e2e-demo`）實際以兩名 **真實 Antigravity 子代理**（寫作 Worker ＋ 實體唯讀盲審員）跑完 `WO-001` 完整生命週期的實測結果（所有命令皆實際執行且回傳 `exit 0`；路徑與識別碼已去識別化）：

1. **測試專案與機制初始化**：
   - 建立示範 Git 倉庫 `demo-project`（含 `calc.py`、`test_calc.py`），執行 `agy_setup.py --init-director --apply` 成功產出 `20` 個步驟（`AGY_SETUP: DONE steps=20`），自動寫入 `.agents/hooks.json`、`.agents/plugins.json` 與 `DIRECTOR/` 完整骨架與 `tools/` 複本。
   - 鎖定驗收測試 `test_calc.py`（`test_divide_exact`、`test_divide_by_zero`）於 `BASE_REF` commit `dd038059fbad`，透過 `safety_gate.py --lock-tests` 寫入 `TESTS_LOCKED:`，開立 `Standard` 工單 `WO-001-add-divide.md`（SHA-256 前綴 `a167947245b0`），完成 `gate_check.py --order`（`exit 0`）並於 `DIRECTOR_LOG.md` 寫入 `DISPATCH`。
2. **雙子代理真實協作流程**：
   - **子代理 1（`dw-demo-worker`，`EnableWriteTools: true`，`conversationId` 前綴 `4b193101`）**：
     - **Turn 1（Plan Gate）**：讀取工單與程式碼後先寫出 `work/WO-001/PLAN.md`（3 項對應 `AC-1` 的 `- [ ]` 條目）即暫停等候，未偷改任何程式碼。總監以 `safety_gate.py --plan-hash` 驗證並寫入 `PLAN_APPROVED` 後，透過 `send_message` 授權進入 Turn 2。
     - **Turn 2（實作與交件）**：切換分支 `demo/w1-divide`，僅於 `calc.py` 新增 5 行 `divide(a, b)`（含 `b == 0` 拋出 `ValueError`）並勾選 `PLAN.md`，產生候選 commit `46f0a0bebdba`，執行 `evidence_record.py run` 留存 `20261124T015537-AC-1.json`，附帶 `ran_line.py` `RAN:` 與 `RESUME_STATE.md`。
   - **子代理 2（`dw-demo-blind-reviewer`，`EnableWriteTools: false`，`conversationId` 前綴 `902309a8`）**：
     - 物理上僅具唯讀權限，獨立閱讀工單、實作、鎖定測試、計畫與報告並逐項手算驗收期望值（`10/2=5.0`、`7/2=3.5`、`divide(1,0)->ValueError`），回報 `NO_ISSUES` 且明確標註未跑 shell 指令。
3. **總監實證重跑與治理檢查結果（全數 `exit 0`）**：

| 檢查項目 | 實測結論摘要 |
|---|---|
| `scope_check.py` | `SCOPE_CHECK: PASS base=dd038059fb changed=1 forbidden=0 outside=0`（`SIZE: files=1 lines=7 test_lines=0`，`PROTECTED_CHANGED: NONE`） |
| `evidence_record.py verify --rerun` | `LOG_LINE: EVIDENCE_RERUN WO-001 AC-1 candidate=46f0a0bebdba5e6b6f117dd6c14aff29d44ef54d result=ok record=20261124T015537-AC-1.json` |
| `gate_check.py --match` | `OK match`（`ORDER_SHA256`、`CHECK`、40 字元 `HEAD` 與鎖定測試均相符） |
| `gate_check.py --verdict` | `OK verdict`＋`OK match`（總監具獨立 `SESSION_ID` 與自身重跑 `RAN` 行） |
| `gate_check.py --sequence` | `OK sequence`（`PLAN_APPROVED` `01:54 +0800` 早於首個 commit `46f0a0bebd` `01:55:28+08:00`） |
| `gate_check.py --final` | `OK final`＋`ok USAGE: .../usage.json`（含 `evidence/E2E/e2e_divide.txt` 與 `DD-01` 驗收案例） |
| `agy_audit.py build`＋`audit_log.py verify` | 從真實 worker `transcript.jsonl` 重建 `34` 筆紀錄（`calls=33 no_result=0`），雜湊鏈驗證 `entries=34 head_seq=34 OK` |
| `report_vs_audit.py` | `REPORT_VS_AUDIT: PASS claims=1 ok=1 failed=0 session=4b193101-... time_mismatch=0 not_run=0` |
| `agy_setup_check.py --strict` | `OK`（`PENDING_METHOD`、`PO_PENDING`、`RESUME_STATE`、`.agents/` 均過關） |
| `agy_doctor.py --probe-hooks` | `OK=14, ENFORCED=2, CONFIGURED_NOT_VERIFIED=1 (os_sandbox), WARN=0, FAIL=0, INFO=1` |

4. **真實 `agy_usage.py` Token 與上下文實測**：
   - **Worker（`4b193101`）**：`23 turns`、`59 steps`、`33 tool_calls`、`input_tokens=136,214`、`cache_read_tokens=796,214`、`output_tokens=21,423`、`total_tokens=953,851`、`last_prompt_tokens=57,833`、`peak_prompt_tokens=57,833`（快取命中率 `85.4%`）。
   - **唯讀盲審員（`902309a8`）**：`3 turns`、`11 steps`、`6 tool_calls`、`input_tokens=50,387`、`cache_read_tokens=23,934`、`output_tokens=8,017`、`total_tokens=82,338`、`last_prompt_tokens=31,119`（快取命中率 `32.2%`）。
   - **合計**：`2 sessions`、`26 turns`、`39 tool_calls`、`total_tokens=1,036,189`。
5. **自檢整合回歸（`tools/selftest_agy.py --verbose`）**：
   - 全套件 **8/8 PASS（`pass=8 skip=0 fail=0`, `exit 0`）**：涵蓋 `39/39` 上游 `v2.6.0` SHA-256 逐位元一致比對、`40` 支工具 `--help`、Windows `cmd /c` 含空白路徑掛勾執行、兩段式 `PostToolUse -> PreInvocation` 橋接、壓縮 `CHECKPOINT` 自動接回與無洩漏檢查。
