# 04 額度與效率

## 成本模型（Google Antigravity 實測口徑）

- **每次模型呼叫都會重送活躍上下文。** 在 Antigravity 本機 `transcript.jsonl` 的 `PLANNER_RESPONSE` 步驟中（已在本機實測驗證）：
  - `input_tokens` = 該輪**未快取的新增輸入 token**
  - `cache_read_tokens` = 該輪**Prompt Cache 命中重讀的 token**
  - **該輪活躍上下文大小（Prompt Size）= `input_tokens + cache_read_tokens`**
  - `output_tokens` = 模型輸出與思考產生的 token
- 用 `tools/agy_usage.py` 直接讀取 `transcript.jsonl` 量測真實數字（不花模型額度、不印對話本文）。

## Antigravity 獨有的省額度槓桿

1. **Skills 漸進揭露（Progressive Disclosure）**：
   - 不要把幾萬字操作手冊全塞進永遠載入的 `AGENTS.md` / `GEMINI.md`。`plugin/director-work/rules/AGENTS.md` 僅保留 ~4 KB 核心規則；建置、派工、審查、恢復、發版全部放在 `skills/*/SKILL.md`，需要時才載入。
   - 單一規則檔務必遠低於 Antigravity 的 `24,000` bytes 上限，避免規則總預算（20,000 tokens）被擠爆或被降級為檔案指標。用 `tools/memory_size_check.py --kind rules` 檢查。
2. **用對編排層級**：
   - 大量唯讀搜尋交給內建 `research` subagent 或自訂唯讀子代理，主對話只收精簡結論。
   - 單對話內中小型修改善用 **Orchestrator**（自動把執行委交較快的 Executor 模型，Planner 只負責規劃與覆核）。
   - 多線開發用 **Swarm**：等待狀態用 `swarm_wait` 休眠（零 token 成本）；超過 8 個 worker 務必切 **Channels**，避免無關對話喚醒所有成員。
3. **減少重複與無效動作**：
   - 長指令輸出導向檔案或僅看尾端；不要連續輪詢 log（`agy_hook_bridge` 會提醒，可用 `agy_audit.py loops --reads` 抓出最常重讀的路徑與打轉呼叫）。
   - 截圖與大量檔案檢查交給 `dw-visual-qa` 或短上下文 subagent，不要把幾十張圖留在主對話裡每輪重送。

## 額度防線與熱切換（誠實落差說明）

- **單對話模式（與 Claude 版之差異）**：Antigravity 未開放 agent 程式化讀取帳號 5 小時／週額度百分比的工具，也沒有專案級 `autoCompactWindow` 門檻或免費 `context_probe.py`（已於 `ANTIGRAVITY-PORT.md` 標註 `NOT_PORTED`）。因此**無法由總監腳本自動精準觸發 98%/99% 停工線**；實務上以 `agy_usage.py` 追蹤單對話 `peak_prompt_tokens` 與每日總量，並依平台實際用量面板為準（讀不到一律記 `UNKNOWN`，不當成 `0` 放行）。
- **Swarm 模式的即時優勢**：在 Swarm 中，工具 `swarm{action:"models"}` 會回傳各模型即時的 `quota_remaining` 與 `quota_resets`；若某成員耗盡額度，可用 `swarm{action:"model", name, model}` **保留完整對話歷史熱切換至可用模型**，不必 kill 重開。
- **不為了縮上下文隨便重開對話**：原專案實測新對話的冷啟動與重建狀態成本往往高於原地壓縮。僅在工單明確設定 `WORKER_LIMIT`（預設 80 輪或活躍上下文偏高）或任務切換階段時，透過 `RESUME_STATE.md` + `HANDOFF.md` 乾淨接棒。
- **收尾豁免**：里程碑收尾或等待重置時，使用 `tools/waiver.py snooze --lane A B C --to <時間>` 一次寫好閒置豁免，不讓監看逐線跳提醒。
