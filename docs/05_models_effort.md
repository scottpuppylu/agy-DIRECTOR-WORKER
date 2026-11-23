# 05 模型與思考程度（effort）

## Antigravity 可用模型與階層（名稱隨版本更新，以 `agy models` 與介面選單為準）

| 類別 | 常見模型 / 模式 | 特性與定位 |
|---|---|---|
| **Orchestrator（預設推薦）** | `Orchestrator (Neon / Carbon / Argon)` | 單對話內自動由高階 Planner 規劃覆核、較快 Executor（如 Hydrogen）執行 |
| **高推理單一模型** | `Gemini 4 Neon`、`Gemini 4 Carbon`、`Gemini 4 Argon` | 全程維持高深度推理，適合系統架構設計、並發與疑難根因分析 |
| **高效率執行模型** | `Gemini 4 Hydrogen` | 速度快、適合例行維護、文件更新、既有模式實作與測試補充 |
| **第三方／跨模型族** | 依方案支援之 Claude / GPT 模型（或 SDK 本地模型） | **跨家族獨立盲審**：建議用與實作作者不同模型家族擔任獨立審查或第 2 組失敗後的診斷者，減少同源盲點 |

## Effort（思考程度）等級

Antigravity 官方 CLI 與模型變體支援五級 effort，與上游 `tools/dispatch_check.py` 定義完全一致：
**`low` / `medium` / `high` / `xhigh` / `max`**（依帳號方案與具體模型是否提供 `xhigh`/`max` 而定；Orchestrator 使用固定思考預算）。

### 派工難度表（`DIRECTOR_LOG.md` 的 `DISPATCH` 記錄欄位）

每次派工寫進 `DISPATCH ... | class=<類別> effort=<等級> model=<模型>`，`tools/dispatch_check.py --log` 會依下表檢查並提醒：

| 類別（`class=`） | 適用任務 | 建議 `effort=` |
|---|---|---|
| `routine` | 照清單做、文件整理、一般搬檔與結構查詢 | `medium` 或 `high` |
| `dev`、`ui` | 一般功能開發、介面實作、測試補充 | `high` |
| `root_cause`、`fix_after_fail`、`realism`、`core_engine` | 根因研究、修失敗後下一輪、真實度關鍵、核心引擎／資料格式 | `xhigh` |
| `repeated_failure` | 同一缺陷連續失敗後的最後深度根因分析 | `xhigh` 或臨時 `max`（解決後調回） |

> 若所選模型沒有 `xhigh` 或 `max` 變體（例如僅到 `high`），照 Antigravity 規則降用該模型最高可用等級並於工單或 log 註明。

## 依角色選型建議

| 角色 | 代理／機制 | 建議模型與設定 | 說明 |
|---|---|---|---|
| **總監（Director）** | 主對話（PO 選定模型） | `Orchestrator` 或高階模型（`high`/`xhigh`） | 以拆分工單、驗收與裁決為主；大量閱讀交給子代理 |
| **一般實作 Worker** | `dw-worker` 或 Swarm worker | 同主對話模型（Swarm 預設留空繼承）或 `Hydrogen`（`high`） | 依工單開 `workspace: "share"` 或 `"branch"` |
| **物理唯讀盲審員** | `dw-blind-reviewer`（僅 `read_file`） | 高推理模型（優先跨模型家族，預設不省） | 品質判斷不省腦力；工具物理鎖死唯讀 |
| **視覺／成品 QA** | `dw-visual-qa` + `browser` subagent | 支援多模態視圖模型 | 看截圖、跑 `ruler_check` / `pack_check`、操作 `/browser` |
| **機械大量閱讀** | 內建 `research` subagent | `flash` / `flash_lite` 級 | 搜尋程式碼與讀文件，只回精簡摘要 |

### 模型選擇與切換規範（配合 Swarm）

- **遵循 PO 選擇**：未經 PO 指定，Swarm 新增成員應留空 `model` 以繼承 PO 為主對話選擇的模型，不擅自換模型。
- **熱切換例外**：僅在 PO 明確要求、或發生額度耗盡（`[quota]` 警告或失敗）時，於 Swarm 使用 `swarm{action:"model", name, model}` 切換，並在回報中說明切換了哪個模型與原因。

## 選用角色與派工規劃

- **總管／清理員／盲審員**皆按需啟用，不為了形式開空轉代理。
- **每次派工前先規劃**：拆誰寫哪個檔（Single Writer）、哪些可平行、誰獨立驗收（絕不由實作者自審），並先跑 `dispatch_check.py --dup` 確認沒跟進行中工作線撞車。
