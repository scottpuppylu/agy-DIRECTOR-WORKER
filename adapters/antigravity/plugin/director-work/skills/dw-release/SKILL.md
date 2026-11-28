---
name: dw-release
description: Final verification, privacy scan, memory budget, and usage metrics checklist before handing a candidate to the PO.
---
# `/dw-release` — 收尾驗收與發版前總檢

1. 執行工單與報告交叉驗證：`python -B -I -X utf8 DIRECTOR/tools/gate_check.py --match <REPORT> <ORDER> --candidate <SHA40> --evidence-dir DIRECTOR/evidence --dispatch-log DIRECTOR/DIRECTOR_LOG.md`
2. 執行變更範圍與交付打包檢查：`scope_check.py` 與 `delivery.py`。
3. 執行終局稽核、用量與記憶體上限檢查：
   - `python -B -I -X utf8 DIRECTOR/adapters/antigravity/agy_usage.py --project-dir . --include-subagents`
   - `python -B -I -X utf8 DIRECTOR/tools/memory_size_check.py --chain`
   - `python -B -I -X utf8 DIRECTOR/tools/privacy_scan.py --rev HEAD`
   - `python -B -I -X utf8 DIRECTOR/adapters/antigravity/agy_doctor.py --repo . --probe-hooks`
4. 產出 `GATE_VERDICT.md`，確認無 `__pycache__`/`.pyc` 後交由使用者決定是否發版／推版。
