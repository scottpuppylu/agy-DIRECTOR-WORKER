# 06b 按需程序：恢復、機制重建、刪檔前比對與程序衛生

建置、恢復、重建機制或刪檔時讀；不列入每輪必讀。

## 一、中斷／壓縮／重啟後的恢復程序

1. **注入與讀檔（唯讀先做）**：
   - 若啟用 `.agents/hooks.json` 的 `agy_hook_bridge.py`，新對話會於 `SessionStart` 自動收到 `RESUME_STATE.md` 表頭與未結段，壓縮發生後於 `PreInvocation` 自動重注；未啟用時先手動執行：
     `python -I -X utf8 <RULES>/tools/resume_inject.py --file <DIRECTOR>/RESUME_STATE.md`
   - 若有產生 `DIRECTOR/status.py`，跑一次 `python -I -X utf8 <DIRECTOR>/status.py` 看清待決、未結工單與迴圈告警。
2. **優先處理 PO 訊息**：讀 `DIRECTOR/PO_PENDING.md`（是否有 `STATUS: answered`）與對話最新訊息，同一輪處理並登記進 `DECISION_REGISTER.md`。
3. **盤點所有工作線與背景任務**：
   - 讀 `DIRECTOR/ACTIVE_WORKSTREAMS.md` 與各線 `RESUME_STATE.md`、`DIRECTOR/PO_PENDING.md`。
   - 用 `manage_subagents` (`list`)、`manage_task` (`list`) 與 `swarm{action:"status"}` 核對目前仍在跑或 `waiting`／`stopped` 的成員。
   - **重開機／伺服器重啟後**：Swarm 的 `@monitor` 計時器與 `schedule` 定時任務可能已停止；對 `@monitor` 發 `@monitor resume` 喚醒，並依 `GUARDS.md` 重建排程。
4. **處理未竟工作與重做**：
   - `RESUME_STATE` 中寫在 `PARTIAL_OUTPUTS_TO_DISCARD` 的半成品輸出或被中斷的量測，一律捨棄重跑，不沿用。
   - 依工單和 `git status` 確認最後已驗證 commit，必要時用 `agy_audit.py build` + `loops` 檢查中斷前是否正在打轉。
5. **登記恢復紀錄**：在 `DIRECTOR_LOG.md` 記一行 `RESUMED <TASK_ID> redo=<NONE|重做項目>`（`metrics_report.py` 靠這行統計中斷遺失）。

## 二、機制清單與重建（`DIRECTOR/GUARDS.md`）

任何會因對話重開或機器重啟消失的機制（`.agents/hooks.json`、`schedule` cron/timer、`pause_ctl` 暫停旗標）都必須登記在 `DIRECTOR/GUARDS.md`，包含：擁有者、中斷影響、檢查指令、重建指令。
- 檢查環境與防線：`python -I -X utf8 <RULES>/tools/agy_doctor.py --repo <REPO> --config <DIRECTOR>/director_work.json --probe-hooks`
- 重建 `.agents/hooks.json` 與 `.agents/plugins.json`：`python -I -X utf8 <RULES>/tools/agy_setup.py --repo <REPO> --director <DIRECTOR> --rules <RULES> --apply --force`

## 三、刪除檔案之前（五步防誤刪）

1. 列候選清單（路徑一行一個、大小、原因）與 `KEEP_AS_EVIDENCE` / `PO_PENDING` 保留清單比對；同一路徑不得同時在保留與可刪清單。
2. 執行引用檢查：`python -I -X utf8 <RULES>/tools/delete_guard.py --list <清單> --repo <REPO> --refs "<DIRECTOR>/**/RESUME_STATE*.md"`；exit 1（仍被引用）項目不刪，報總監／PO 確認。
3. 未獲 PO 明確同意不執行刪除；核准後優先移入 `_trash_pending/` 備份並記 SHA-256，保留期過後再永久刪除並對帳。

## 四、`RESUME_STATE.md` 瘦身（`resume_archive.py`）

當 `memory_size_check.py --kind resume` 超過 `resume.max_bytes`（預設 20 KiB）或單行超過 `4096` bytes：
- 先跑 dry-run：`python -I -X utf8 <RULES>/tools/resume_archive.py --file <RESUME_STATE.md> --config <DIRECTOR>/director_work.json`
- 確認只搬移很久以前的段落（保留最新 `keep_recent` 段與受保護關鍵字如 `現況` / `未結` / `OPEN` / `BLOCKED` / `IN_FLIGHT`），再加 `--execute` 寫入同目錄 `RESUME_HISTORY_<YYYYMMDD>.md`（不刪原文）。

## 五、Git 與程序衛生細節

- **需要暫存時**：優先使用 `WIP` commit；若非用 stash 不可，必須 `git stash push -u -m "<workstream>-<YYYYMMDD-HHMM>"`（勿用裸 `git stash` / `stash pop`），記下 SHA 後用 `git stash apply <SHA>`，經 PO 同意才 drop。
- **只停自己的程序樹**：停背景工作時先依完整命令列查 `ProcessId` / `ParentProcessId`（Windows：`Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,CommandLine`；Linux：`ps -eo pid,ppid,args`），只停自己啟動的 PID 或 Antigravity `manage_task{Action:"kill", TaskId:...}`。
- **非版控必要輸入（`pre_tr.py` `inputs`）**：建置需要的本機大檔或外部檔案，須列明路徑、來源與 SHA-256；缺檔或雜湊不符立即 `MISSING_INPUT` / `INPUT_CHANGED` 停工。
- **跨平台與編輯衛生**：用 `&&`（Bash）或 `; if ($LASTEXITCODE -ne 0)` 保護檢查與 commit 順序；commit 前 `git diff --cached --check`（可選裝 `templates/.pre-commit-config.yaml` 防衝突標記與 `.gitleaks.toml`）；不要對 CRLF 檔案用 `sed -i`；讀 JSON 用 `python` 解析不靠 `grep`；時間戳用 `ran_line.py --stamp` 取時鐘，不手打。
