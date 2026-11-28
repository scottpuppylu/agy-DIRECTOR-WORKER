---
trigger: always_on
---
# DIRECTOR-WORK（Antigravity 工作區規則）

本專案採用 `agydw`（`DIRECTOR-WORK` Antigravity 變體）。

- 總監資料夾在專案根目錄內的 `DIRECTOR/`（已由 `.git/info/exclude` 與 `DIRECTOR/.gitignore` 排除，不進版本庫）。
- 開工前先檢查 `DIRECTOR/RESUME_STATE.md`、`DIRECTOR/INDEX.md` 與 `DIRECTOR/BRIEF.md`。
- 初衷卡（`DIRECTOR/BRIEF.md`）尚未通過 `DIRECTOR/tools/spec_gate.py` 前，不寫產品碼、不建工作分支或 worktree、不派動手子代理；唯讀查探與補充初衷卡不在此限。
- 決策、派工、交辦一律依「規則／總監／工作手冊」：使用者待決優先用 `ask_question` 並同步寫入 `DIRECTOR/PO_PENDING.md`；子代理僅透過 `send_message` 或回傳向總監回報，不得擅自對使用者或外部系統下決定。
- 執行 Python 工具請加 `-B -I -X utf8`（禁止留下 `__pycache__` 或 `.pyc`），修改或驗收後以 `DIRECTOR/tools/gate_check.py` 與轉接層工具做獨立查驗；完整指引見規則庫 `<RULES>/BOOTSTRAP.md`（小白模式見 `docs/12_chat_to_product.md`）及 `<RULES>/adapters/antigravity/README.md`。
