---
name: dw-dispatch
description: Draft, lock, and dispatch a DIRECTOR-WORK work order after BRIEF.md passes spec_gate.
---
# `/dw-dispatch` — 開立並派工工單

1. 確認初衷卡已通過：`python -B -I -X utf8 DIRECTOR/tools/spec_gate.py check --brief DIRECTOR/BRIEF.md`
2. 鎖定測試雜湊：`python -B -I -X utf8 DIRECTOR/tools/safety_gate.py --lock-tests <TESTS...> --repo .`
3. 依 `templates/WORK_ORDER.md` 在 `DIRECTOR/orders/` 建立工單並跑靜態檢查：
   `python -B -I -X utf8 DIRECTOR/tools/gate_check.py --order DIRECTOR/orders/WO-xxx.md --repo .`
4. 取得 `DISPATCH` 記錄行並寫入 `DIRECTOR/DIRECTOR_LOG.md`：
   `python -B -I -X utf8 DIRECTOR/tools/gate_check.py --hash DIRECTOR/orders/WO-xxx.md`
5. 在隔離 worktree (`DIRECTOR/tools/worktree.py open . --order WO-xxx`) 派給 `dw-worker` 或子代理執行；結束時由 `dw-blind-reviewer` 獨立盲審。
