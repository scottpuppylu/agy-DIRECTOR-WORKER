---
name: dw-bootstrap
description: >-
  Use when initializing DIRECTOR-WORK governance in a repository under Google Antigravity,
  onboarding a beginner or general PO, scaffolding DIRECTOR/, wiring .agents/hooks.json
  and .agents/plugins.json, or running environment and baseline checks.
---

# Bootstrap DIRECTOR-WORK in Antigravity (`agydw`)

照 `BOOTSTRAP.md` 順序建置，先詢問 PO 模式選擇，**絕不自行修改 `~/.gemini/config/config.json` 或執行 `git push` / 刪檔**。

## 步驟

1. **唯讀盤點**：
   - 確認 `<REPO>` 的 `git rev-parse --show-toplevel`、`git status`、`git worktree list`。
   - 讀取專案既有 `AGENTS.md`、`GEMINI.md`、`CLAUDE.md`（只讀不改衝突規則，既有專案規則永遠優先）。
   - 讀取系統時鐘（含時區）。
2. **取得模式與初衷確認**：
   - 使用 `ask_question` 詢問 PO 選「一般模式」或「小白模式（推薦給沒寫過程式的使用者）」，並登記進 `DIRECTOR/PO_PENDING.md`（`PENDING_METHOD: ask_question + PO_PENDING.md`）。
   - 小白模式先以最精簡狀態檔寫入專案外 `<DIRECTOR>/`，談完需求與初衷卡（`templates/BRIEF.md`）後再進行完整建置與派工。
3. **建置 `<DIRECTOR>/` 與專案 `.agents/` 設定**：
   - 先跑 dry-run 檢視：
     `python -B -I -X utf8 <RULES>/tools/agy_setup.py --repo <REPO> --rules <RULES> --init-director`
   - 確認無誤後加 `--apply` 寫入（自動產生 `.agents/hooks.json`、`.agents/plugins.json`、Git common `info/exclude`、每專案獨立 `<DIRECTOR>` 骨架與預設受限 `.sessions.json`）；接著以 `--register-director <CONVERSATION_ID> --apply` 登記總監對話。
4. **量基準與環境自檢**：
   - 規則大小：`python -B -I -X utf8 <RULES>/tools/memory_size_check.py --kind rules --file <REPO>/AGENTS.md`（若無專案規則改查 `<RULES>/plugin/director-work/rules/AGENTS.md`）。
   - 環境與沙箱自檢：`python -B -I -X utf8 <RULES>/tools/agy_doctor.py --repo <REPO> --plugin <RULES>/plugin/director-work --probe-hooks`。
   - 用量基準：`python -B -I -X utf8 <RULES>/tools/agy_usage.py --project-dir <REPO> --out <DIRECTOR>/research/usage-baseline.json`（若無歷史對話記 `UNKNOWN`）。
   - 建置驗證：`python -B -I -X utf8 <RULES>/tools/agy_setup_check.py <DIRECTOR> --repo <REPO> --strict`。
5. **第一份回報**：
   - 依 `BOOTSTRAP.md` 第 13 步以繁體中文提出六點精簡結論，並用 `ask_question` 讓 PO 拍板待決事項（如第一批 worker 編制、是否啟用 `--guard-mode ask`）。
