# 09 規則與記憶容量管理

## Antigravity 的規則容量模型（與 Claude 版差異）

Claude Code 使用個別專案的 `~/.claude/projects/.../memory/MEMORY.md`；Google Antigravity 則自動探索專案內外層的 **`AGENTS.md` / `GEMINI.md`** 與啟用中的 **Plugin `rules/AGENTS.md`**，再加上按需載入的 **Skills（`skills/<name>/SKILL.md`）**。

Antigravity 官方明確限制：
1. **單檔上限 `24,000` bytes（24 KB）**：超過會在換行處被截斷。
2. **常駐規則總預算 `20,000` tokens**：所有常駐規則合計超標時，會被系統降級成「只給檔案路徑指標」，不再直接內聯全文。
3. **漸進揭露（Progressive Disclosure）**：Skills 只注入名稱與簡短 `description`，不主動讀不花本文上下文。

## 規則

1. **專案常駐 `AGENTS.md` / `GEMINI.md` 與總監段保持精簡**：日常細節放 `DIRECTOR/` 與按需 Skills，常駐指示檔嚴守 `<= 24,000` bytes（推薦 `< 8,000` bytes）。
2. **定時與改動後量測**：
   - 單檔或外掛規則：`python -I -X utf8 <RULES>/tools/memory_size_check.py --kind rules --file <AGENTS.md>`
   - 工作線 `RESUME_STATE.md` 總檢查：`python -I -X utf8 <RULES>/tools/memory_size_check.py --kind resume --workstreams <DIRECTOR>/ACTIVE_WORKSTREAMS.md --config <DIRECTOR>/director_work.json`
   - 指示鏈與跨檔重複檢查：`python -I -X utf8 <RULES>/tools/memory_size_check.py --chain AGENTS.md AGENTS.local.md --max-bytes 24000`
3. **結案條目不留常駐檔**：已結案的決策、歷史工單細節與過往除錯過程，搬到 `DIRECTOR/DECISION_REGISTER.md`、`DIRECTOR_LOG.md` 或 `RESUME_HISTORY_<YYYYMMDD>.md`（用 `resume_archive.py`），不留在每次必讀的 `AGENTS.md` 或 `RESUME_STATE.md` 表頭。
4. **規則類條目保留**：PO 長期偏好的工作方式、安全禁令與專案架構原則留在 `AGENTS.md`（但寫成「規則 → 為什麼」一句一行）。
5. **整理前先備份**：大幅清理前先將原檔備份到 `DIRECTOR/research/` 或使用 `resume_archive.py --execute`（會自動留 `.bak`）。

## 為什麼

- 每多 1,000 tokens 的常駐規則，在整個專案數百次工具呼叫中都會乘數放大消耗。把程序性步驟放進 Skills、把歷史寫進 `DIRECTOR/` 封存檔，是 Antigravity 省上下文最有效的方式。
