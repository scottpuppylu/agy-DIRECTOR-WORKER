---
name: dw-resume
description: Losslessly resume or hand off DIRECTOR-WORK governance state via DIRECTOR/INDEX.md and RESUME_STATE.md.
---
# `/dw-resume` — 無損恢復與跨平台換手

1. **接手第一件事**：先讀 `DIRECTOR/INDEX.md`，再依指標閱讀 `DIRECTOR/RESUME_STATE.md`、`DIRECTOR/BRIEF.md`、`DIRECTOR/DECISION_REGISTER.md`、`DIRECTOR/PO_PENDING.md`、`DIRECTOR/DIRECTOR_LOG.md`。勿重問已確認的需求。
2. 驗證交接快照是否新鮮：`python -B -I -X utf8 DIRECTOR/tools/handoff.py verify --director DIRECTOR`
3. 確認 `RESUME_STATE.md` 格式與預算未超限：`python -B -I -X utf8 DIRECTOR/tools/gate_check.py --resume DIRECTOR/RESUME_STATE.md`
4. 若為打包換手給新會話／跨變體總監，先更新紀錄並執行：`python -B -I -X utf8 DIRECTOR/tools/handoff.py export --director DIRECTOR`
