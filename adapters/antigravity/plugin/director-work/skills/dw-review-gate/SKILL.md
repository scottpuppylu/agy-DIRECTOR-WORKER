---
name: dw-review-gate
description: Audit work order reports, evidence records, action logs, and blind reviews with gate_check and agy_audit.
---
# `/dw-review-gate` — 審查報告與稽核閘門

1. 驗證雜湊鏈與工具實際執行紀錄：
   `python -B -I -X utf8 DIRECTOR/adapters/antigravity/agy_audit.py verify --log DIRECTOR/audit/<worktree>.jsonl`
   `python -B -I -X utf8 DIRECTOR/tools/report_vs_audit.py --report <REPORT> --log DIRECTOR/audit/<worktree>.jsonl`
2. 檢查每筆實跑證據（含 `--rerun` 重驗關鍵 AUTO 項）：
   `python -B -I -X utf8 DIRECTOR/tools/evidence_record.py verify --rerun --order DIRECTOR/orders/WO-xxx.md <EVIDENCE.json>`
3. 執行 `--match` 與最終收尾檢查，確認沒有 `NOT_RUN`、`INVALID` 或未覆核項目。
