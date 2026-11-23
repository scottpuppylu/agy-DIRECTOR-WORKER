# 11 在 Google Antigravity 上操作（App 2.0 / `agy` CLI / IDE / SDK）

只在 Antigravity 上當總監或派工時讀，不列入每輪必讀。規則照 `docs/01` 到 `docs/10` 與 `docs/13`；本檔寫清楚「流程需要的能力，在 Antigravity 上用什麼原生原語做、邊界在哪」。

## 一、四層編排選型（Antigravity 核心優勢）

Claude Desktop 受限於「只有 PO 親自發訊息的那一輪能 `start_session` 開 worker」；Antigravity 沒有這項限制，並提供四層原生編排原語：

| 層級 | 工具 / 指令 | 適合場景 | 工作區隔離與關鍵特性 |
|---|---|---|---|
| **1. 單對話雙模型交棒** | **Orchestrator**（模型選單） | 單人可完成的 `Direct` / 小型 `Standard` 修改 | 高階 Planner（如 Neon/Carbon）規劃覆核 + 較快 Executor（Hydrogen）實作；單上下文、協調成本最低 |
| **2. 自訂隔離子代理** | `define_subagent` / `invoke_subagent` + `send_message` | 單一明確工單（`dw-worker`）、**物理唯讀盲審（`dw-blind-reviewer`）**、視覺 QA（`dw-visual-qa`） | 支援 `Workspace`: `"inherit"`（與父代理共用目錄，僅建議唯讀用）、`"share"`（共用底層 repo 的工作副本）、**`"branch"`（Antigravity 自動建立隔離的 Git worktree 與獨立分支，避免多寫手在工作目錄互踩，但底層 Git 物件與 refs 仍屬同 repo，非 VM 級隔離）** |
| **3. 團隊共享留言板** | `swarm` + `post_message` + `read_board` + `swarm_wait` | 2–8+ 條平行工作線、作者↔審查員↔收尾接力 | - `workspace: "share" \| "branch"` 隔離寫手<br>- `swarm_wait` **休眠零 token**<br>- `channel` 分區（>8 人必分頻道，維持 `main` 安靜）<br>- `fork` 熱分岔繼承既有上下文<br>- `swarm{action:"models"}` 查**即時剩餘額度**、`swarm{action:"model"}` **保留歷史熱切換模型**<br>- 內建 `@monitor` 防全體靜默卡死＋Swarm Overview 截圖時間軸 |
| **4. 程式化平行管線** | `run_workflow`（Python `parallel` / `pipeline` / `phase`） | 多角度 `SPEC_REVIEW`、大量檔案檢查、結構化交件閘門 | 強制每個子代理透過 `finish` 工具回傳符合 **JSON Schema** 的結果；可於 `templates/workflows/` 直接套用 |

### 特別注意：Custom Subagent 工具白名單的防卡坑

Antigravity 官方文件註明：自訂代理（`.agents/agents/*.md` 或 `plugin/.../agents/*.md`）YAML `tools:` 若填寫未映射或拼錯的工具名，可能導致子代理執行時掛起。
- **App 2.0 驗證通過的清單**：`read_file`、`edit_file`、`write_file`、`run_command`、`manage_task`、`search_web`（及協作工具 `invoke_subagent`、`send_message`、`schedule`、`ask_question` 等）。
- **嚴禁放進代理 YAML `tools:` 的名稱**：`view_file`、`write_to_file`、`replace_file_content`、`multi_replace_file_content`、`grep_search`、`find_by_name`（這些由 `agy_guard.py` 在 Hook 層做跨介面 matcher 保護即可）。
- `dw-blind-reviewer.md` 只填 `tools: [read_file]`，在執行期實現**物理唯讀**。

## 二、能力對照表

| DIRECTOR-WORK 能力 | Antigravity 原生做法 | 注意與工具對應 |
|---|---|---|
| **開 worker** | `invoke_subagent` 或 `swarm{action:"add"}` | 先把工單落檔並跑 `gate_check --order`；提示只給工單路徑、`RESUME_STATE.md` 路徑與一句目標 |
| **傳訊息與喚醒** | `send_message`（subagent）或 `post_message`（Swarm `@name`） | `@name` 是門鈴不是 CC，每次 `@mention` 會消耗對方一輪；只在 `docs/03` 三種情況發 |
| **唯讀研究／大量看檔** | `invoke_subagent{TypeName:"research"}` | 內建唯讀研究代理，適合搜尋大量檔案後只回精簡摘要 |
| **網頁與前端實測** | `invoke_subagent{TypeName:"browser"}`（`/browser`） | 總監**只採信宿主訊息 `BROWSER_HOST_FACTS`** 驗證過的訪問 origin 與截圖路徑；若遇認證牆／付款頁回報 `BLOCKED`，請 PO 親自操作後 `send_message` 續接 |
| **PO 選擇題與待決頁** | `ask_question` + `DIRECTOR/PO_PENDING.md`（圖表可選 Artifacts / `generative_ui`） | 子代理不直接問 PO，須報總監統一用 `ask_question` 提問並回寫 `PO_PENDING.md` |
| **排程與重啟喚醒** | `schedule`（一次性 `DurationSeconds` 或 `CronExpression`） | 只在有定期巡檢／重資源排隊需求時建；重開後須依 `DIRECTOR/GUARDS.md` 重建 |
| **規則與套件載入** | Antigravity Plugin（`.agents/plugins.json` 指向 `<RULES>/plugin`） | 必讀核心放 `rules/AGENTS.md`（單檔 ≤ 24,000 bytes），SOP 放 `skills/*/SKILL.md` 漸進揭露 |
| **執行期防線（Hooks）** | `.agents/hooks.json` + `agy_guard.py` + `agy_hook_bridge.py` + `agy_audit.py` | 支援 `SessionStart`、`PreToolUse`、`PostToolUse`、`PreInvocation`、`Stop`；`agy_setup.py --apply` 一鍵寫入 |
| **無頭與 CI 自動化** | `agy -p "..." --output-format json --json-schema '...'` | 需已登入快取憑證；也可用 `google-antigravity` Python SDK 驅動自訂驗證管線 |

## 三、生命週期 Hooks 與兩段式橋接細節

1. **`PreToolUse`（`agy_guard.py`）**：
   - Antigravity 支援回傳 `allow`、`deny`、`ask`、`force_ask`（忽略 Always Allow 快取，強迫現場確認）與 `overwrite`。
   - Windows 上 Antigravity 透過 `cmd /c` 執行 Hook，`agy_setup.py` 會自動避開 `cmd /c` 外層引號剝除陷阱（無空白路徑不加引號，含空白 exe 加 `call`）。
2. **`PostToolUse` → `PreInvocation` 兩段式橋接（`agy_hook_bridge.py`）**：
   - Antigravity 的 `PostToolUse` stdout 規定必須回傳 `{}`，無法直接送出提醒給模型；而且預設僅傳 `stepIdx` 與 `transcriptPath`，不直接內聯工具完整輸出。
   - 因此由 `post-tool` 讀取 `transcriptPath` 對應步驟執行 `inject_scan` 與 `poll_hint`，有告警就寫進 `.bridge_state/pending_<cid>.jsonl`；緊接著模型呼叫前的 `pre-invocation` 再取出並以 `injectSteps`（`systemMessage`）注入提醒。
3. **無損壓縮重注與 `Stop` 防呆**：
   - `session-start` 於新對話注入 `RESUME_STATE.md` 表頭與未結段（附零 token 消耗的 `metadata.user_markdown` 橫幅）。
   - `pre-invocation` 偵測 `transcript.jsonl` 新增 `SYSTEM / CHECKPOINT` 壓縮步驟時自動重注 `RESUME_STATE.md`。
   - `stop-check` 檢查 `continuationActive === true` 或 `terminationReason !== "NO_TOOL_CALL"` 時立刻放行，確保絕不陷入無限迴圈；選用 `--enforce-resume` 時可於程式碼異動後提醒更新 `RESUME_STATE.md`。

## 四、三平台 OS 原生終端沙箱與權限預設

Antigravity 依帳號權限預設（**Request Review** / **Default** / **Auto** / **Turbo**）與規則優先序（**`Deny > Ask > Allow`**）提供 OS 原生沙箱：
- **Linux**：Kernel Namespaces（檔案隔離、隱藏宿主程序、預設斷網）。
- **macOS**：`sandbox-exec` Seatbelt（SBPL 限制檔案與 socket）。
- **Windows**：**Windows 11 24H2 及以上（build ≥ 26100，含 2026-08 更新）** 採 **AppContainer + Process Security Environments**。
  - **Windows 邊界誠實說明**：官方文件明確指出，Windows 沙箱內「專案資料夾與 `write_file` 授權路徑可讀寫，使用者家目錄外其他資料夾**可讀不可寫**」，僅家目錄下未授權路徑與 `.env`／`~/.ssh` 等敏感檔隱藏阻擋。執行 `tools/agy_doctor.py` 會偵測 build 並如實印出此邊界。
- **不建議用於無監督 worker 的預設**：`Turbo`（關閉沙箱、無限制）。日常建議維持 **`Default`** 或 **`Auto`**，搭配 `workspace: "branch"` 與 `agy_guard.py`。
