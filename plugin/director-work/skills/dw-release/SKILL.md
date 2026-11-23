---
name: dw-release
description: >-
  Use when closing a milestone or preparing a public release: running closing gate checks,
  generating final usage and traceability reports, scanning for leaked secrets or private
  paths, and running release.py dry-run prior to PO push approval.
---

# Milestone Closing & Release Protocol

**推送（`git push`）、建立 GitHub Release 與公開發布一律必須取得 PO 明確同意，本流程預設僅執行本機檢查與 dry-run。**

## 收尾檢查（Milestone Closing）

1. **時間戳與用量彙總**：
   `python -I -X utf8 <RULES>/tools/agy_usage.py --project-dir <REPO> --include-subagents --loops --out <DIRECTOR>/usage.json`
2. **終驗與追溯**：
   - `python -I -X utf8 <RULES>/tools/trace_report.py --orders <DIRECTOR>/orders --reports <DIRECTOR>/reports --verdicts <DIRECTOR>/reviews --config <DIRECTOR>/director_work.json`
   - `python -I -X utf8 <RULES>/tools/metrics_report.py --director <DIRECTOR>`
   - `python -I -X utf8 <RULES>/tools/gate_check.py --final <DIRECTOR> --usage <DIRECTOR>/usage.json`
   - 確認所有工單在裁決後沒有未授權的程式提交：`gate_check.py --after-verdict <VERDICT> --repo <REPO> --orders <DIRECTOR>/orders`。
   - 檢查是否仍有豁免未到期或需統一續期：`waiver.py`（可用 `--to <DATE>` 統一到期日）。

## 發版前檢查（Dry-Run Only）

1. 確認工作樹乾淨、無追蹤檔未提交、`VERSION` 與 `CHANGELOG.md` 最上方版本標題一致。
2. 執行整套自我測試：
   `python -I -X utf8 <RULES>/tools/selftest_agy.py`
3. 執行發版預演（絕不先加 `--execute`）：
   `python -I -X utf8 <RULES>/tools/release.py --repo <REPO> --config <DIRECTOR>/director_work.json`
   `release.py` 會自動檢查遠端狀態、跑 selftest、並掃描 `DIRECTOR/`、`_work/`、本機使用者路徑、信箱與憑證外洩。
4. **交由 PO 拍板**：將 dry-run 計畫步驟與掃描結果回報 PO，**取得 PO 核准後**，才可執行 `--execute` 或讓 PO 自行推送。
