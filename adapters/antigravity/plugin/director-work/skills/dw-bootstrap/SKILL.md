---
name: dw-bootstrap
description: Initialize or upgrade an Antigravity repository for agy-DIRECTOR-WORKER 3.0.0-agy.7 (DIRECTOR/ inside repo, .agent/rules/, hooks).
---
# `/dw-bootstrap` — 初始化或升級專案治理環境

1. 執行 dry-run 預覽安裝內容（將總監目錄建立在 `<REPO>/DIRECTOR/`、複製基底與 Antigravity 工具、寫入 `.agent/rules/director-work.md` 與 `.agents/{hooks,plugins}.json`）：
   `python -B -I -X utf8 adapters/antigravity/agy_setup.py --repo <REPO> --init-director`
2. 確認預覽無誤後加上 `--apply`（小白模式可加 `--beginner`，仍會安裝 `agydw-spec-gate`）：
   `python -B -I -X utf8 adapters/antigravity/agy_setup.py --repo <REPO> --init-director --register-director <CID> --apply`
3. 驗證所有設定與 Hook 實跑探針：
   `python -B -I -X utf8 adapters/antigravity/agy_setup_check.py --strict <REPO>/DIRECTOR --repo <REPO>`
   `python -B -I -X utf8 adapters/antigravity/agy_doctor.py --repo <REPO> --probe-hooks`
4. 完成需求對談後填寫 `DIRECTOR/BRIEF.md`，再由使用者或總監執行 `spec_gate.py approve` 解除 R6 初衷卡關卡。
