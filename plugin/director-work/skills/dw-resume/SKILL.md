---
name: dw-resume
description: >-
  Use when resuming a Director or worker session after compaction, interruption, quota
  exhaustion, or restart, to inspect RESUME_STATE, check loops and health, and rebuild
  timers or guards cleanly.
---

# Lossless Resume & Guard Recovery

不靠對話記憶，先讀檔再看現況。

## 恢復步驟

1. **讀待決與恢復表頭**：
   - 先查 `DIRECTOR/PO_PENDING.md` 是否有 PO 已回覆的題目（`STATUS: answered`）。
   - 執行 `python -B -I -X utf8 <RULES>/tools/resume_inject.py --file <DIRECTOR>/RESUME_STATE.md` 讀回四行表頭與未結段（若已啟用 `SessionStart` / `PreInvocation` hook 橋接器，會自動注入）。
2. **一次看完專案現況**：
   - 若已透過 `python -B -I -X utf8 <RULES>/tools/status_gen.py` 產生 `<DIRECTOR>/status.py`，直接執行 `python -B -I -X utf8 <DIRECTOR>/status.py` 一次讀完待決、未結工單、動作稽核與迴圈告警（不逐一重掃檔案）。
   - 檢查所有工作線 `RESUME_STATE.md` 大小：
     `python -B -I -X utf8 <RULES>/tools/memory_size_check.py --kind resume --workstreams <DIRECTOR>/ACTIVE_WORKSTREAMS.md --config <DIRECTOR>/director_work.json`
     超過上限時先 dry-run 再執行 `python -B -I -X utf8 <RULES>/tools/resume_archive.py` 歸檔舊段（不動現況與未結項）。
3. **掃描迴圈與風險**：
   `python -B -I -X utf8 <RULES>/tools/agy_audit.py loops --include-subagents --reads --out <DIRECTOR>/loops.json`
   `python -B -I -X utf8 <RULES>/tools/agy_doctor.py --repo <REPO> --config <DIRECTOR>/director_work.json --probe-hooks`
4. **Swarm 額度與喚醒重建**：
   - 若是在 Swarm 中某成員額度耗盡，使用 `swarm{action:"models"}` 檢視可用額度並以 `swarm{action:"model", ...}` **熱切換模型保留對話**，勿直接 `kill`。
   - 對話重開後若排程中斷，對照 `DIRECTOR/GUARDS.md` 重建 `schedule`（cron / timer）並寫回 `DIRECTOR_LOG.md` 的 `RESUMED <TASK_ID> ... redo=<NONE|...>`。
5. **壓縮前保護**：若察覺對話已長，先更新 `RESUME_STATE.md` 四行再視需要使用 `/compact`。
