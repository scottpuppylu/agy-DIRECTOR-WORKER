# CHANGELOG

版本號在 `VERSION`（`3.0.0-agy.7`，依 `SPEC_VARIANTS_300` 接續 `2.6.0-agy.6` 遞增，永不歸 `1`）。基板 `3.0.0` 原文完整保留於 `CHANGELOG-upstream.md`。

## 3.0.0-agy.7 — 2026-11-28

對齊第三次凍結基板 `DIRECTOR-WORK 3.0.0`（commit `0e9ae22`，含 B1–B19、4 項小修與 F13 測試字面拆解）並承接 `build/agy-base` `af0e859`（agy P0 修正），另完成發版前獨立審查 P1-1..P1-3 修補與反例測試；所有 Antigravity 專屬適配、Hook、Plugin、Workflow 與自檢集中於 `adapters/antigravity/`，其餘基板檔與 `0e9ae22` 逐位元組一致。

### 與上游 `3.0.0`（`0e9ae22`）逐項處理對照

| 上游項目 | 怎麼處理（沿用／改寫／不做） | 理由 |
|---|---|---|
| `_common.py`（G1）、`exit_code_from`/`gate_status`（G2）、`usage_number`/`usage_partial`（D7） | 沿用（並於轉接層委派） | `adapters/antigravity/{agy_common,agy_audit,agy_usage}.py` 直接委派基板 `_common.py`，補齊 D7 `partial` 與 G2 `INVALID` |
| B1（`scope_check --candidate`）、B2（`gate_check` `RESULT: FAIL`＋`N failed`）、B4（`order_limits`/`protected_tests`） | 沿用基板（已刪除 `agy_gate_extra.py`） | 三項皆已併入基板 `0e9ae22`，轉接層全面改用 `tools/scope_check.py` 與 `tools/gate_check.py` |
| B15（`VARIANT.json` `default_strength`）＋ B19（`.agent/rules/` DW 段落計入必讀鏈） | 沿用＋新增 `VARIANT.json` | 新增 `adapters/antigravity/VARIANT.json` 宣告 `standard` 與 `.agent/rules/director-work.md` DW 段落，由基板 `safety_gate.py` 與 `memory_size_check.py --rules-file` 實測 |
| B6/B7/B16（`tools/base_sync_check.py`、`tools/readme_check.py`、`upstream_300`）＋ `0e9ae22` F13 字面拆解 | 沿用基板 | 撤掉 `tools/selftest.py` 的 `upstream_300` 覆寫並同步 `0e9ae22` F13 拆解行（僅留 2 處非 Claude README 語句對齊）；`adapters/antigravity/base_sync_check.py` 改為委派 `tools/base_sync_check.py` 的薄入口 |
| `spec_gate.py`（R6/R7/C1）、`safety_gate.py`（D1） | 沿用＋轉接層實作 Hook | `spec_gate_hook.py`＋`GATE_RULES.json` 於一般與 `--beginner` 模式強制無快取、Fail-Closed 執行 R6 |
| `bootstrap_repo.py`（D6/B13）、`R3` 專案內 `DIRECTOR/` | 沿用＋改寫 `agy_setup.py` | 總監目錄放 `<REPO>/DIRECTOR/`（`DIRECTOR/.gitignore`＋共通 `info/exclude`），工作區規則放 `.agent/rules/director-work.md` |
| `handoff.py`（R5/C3/C10）、`delivery.py`（C2/C4）、`worktree.py`（D3/G6）、`install.py`（C7） | 沿用＋轉接層 manifest | `agy_setup.py` 委派 `install.py` 安裝 `DIRECTOR/tools/`；`agy_hook_bridge.py` 呼 `_common.same_repo`；換手盲測分數詳見下節 |
| B3/B5/B8–B14/B17/B18（`release.py`、`loop_guard.py`、`dispatch_check.py`、`status_gen.py`、`privacy_scan.py` 等） | 沿用基板 | `B11` 所列 Claude 專用檔照基板清單同步保存（`hook_guard.py` 由 `agy_guard.py` 載入）；新增 `agy_version.py` 強制 `-agy.N` 遞增 |
| `README.md`、`CHANGELOG.md`、`VERSION`、`tools/selftest.py` | 轉接覆寫（4 份） | 登記於 `adapters/antigravity/BASE_SYNC.json`，由 `tools/base_sync_check.py` 核對 |

### 獨立審查 P1 修補（3.0.0-agy.7 發版前補強）

- **P1-1（G53 shell oauth 讀取）**：`agy_guard.py` 與外掛 `seatbelt_hook.py` fallback 皆補充 shell 指令解析（涵蓋 `cat`/`type`/`Get-Content`/`Copy`/`curl -d @`/輸入重導 `<` 與 `run_checks` MCP）攔截 `.gemini` / `oauth_creds.json`，同時維持 `echo "oauth_creds.json"` 放行。
- **P1-2（`--beginner` 保留硬性護欄）**：`agy_setup.py` 於 `--beginner` 模式保留 `agydw-guard --hard-only`（只放寬軟提醒與詢問，`G00`/`G50`–`G53`/`PROTECT`/worker `G43` 在全模式硬性拒絕），並同步 `agy_setup_check.py` 與 `agy_doctor.py` 探針。
- **P1-3（R6 唯讀白名單旗標防護）**：`spec_gate_hook.py` 事前阻擋 `sort -o`/`--output`/`/o`/`--compress-program`、`tree -o`/`--output`、`git grep -O`/`--open-files-in-pager`、`git -c`/`--exec-path`/`--ext-diff`、`command rm`、`less -o`、`bat --pager`、`file -C`、`printf -v` 與 PowerShell ScriptBlock `{...}`，並在 `selftest_antigravity.py` 納入上述反例測試。

### 換手盲測（DW → agy）

- 2026-11-28：實測 **DW → agy** 15 題換手盲測（以 `tools/handoff.py export` / `verify` / `quiz` 建立隔離專案，僅提供專案根路徑、一句話接手提示詞與 15 題由獨立 `research` 子代理盲答），`tools/handoff.py score` 計分結果為 **15/15 PASS**（門檻 >= 14/15）。

### `NOT_PORTED`（Antigravity 宿主差異）

- 無 `autoCompactWindow` 與免費 CLI `context_probe.py`（改以 `agy_usage.py` ＋ `CHECKPOINT` 自動重注 `RESUME_STATE`）。
- `.agents/hooks.json` `PostToolUse` 不支援原地回寫 `updatedToolOutput`（改由 `PreToolUse` `agy_guard.py` 與 `audit_log.py` 寫入遮罩）。
- 專案無法以設定檔停用使用者全域 Plugin；Windows AppContainer 預設仍可讀取家目錄外路徑。

### 對齊初衷與致謝（Credits）

- 本版落實 R3（專案內 `DIRECTOR/` ＋ `.agent/rules/`）、R5（`INDEX.md` 單一換手入口）、R6（`spec_gate_hook.py` 無快取硬閘門）、G3（單一 `post-tool` 處理）、G5（必讀鏈 ≤ 24 KB）、G6（`same_repo` Stop 防護）與 D7/G7（單遍串流＋`partial` 用量誠實標記）。
- Credits：上游 `DIRECTOR-WORK`（`3.0.0`），以及 `dsh-DIRECTOR-WORKER`、`cdx-DIRECTOR-WORKER`、`cld-DIRECTOR-WORKER`。

---

## 2.6.0-agy.6（2026-11-24）

- 修復 `agy_guard.py` Fail-Closed、MCP 原生工具攔截、`find_by_name` 與 PowerShell `-Path:` 參數繞過；修復 `agy_usage.py --project-dir --include-subagents` 子代理漏計與單遍串流優化（+25 MiB 樣本記憶體降至 0.02 MiB）；修復 `gate_check.py --final` 對 `RESULT: FAIL` 假通過；並合併 P0 修復 `gate_verify_workflow.py` 無結束碼或多重結束碼回傳 `INVALID`（`af0e859`）。
- 對齊初衷（PURPOSE.md）：落實 Fail-Closed 與工具不替代真相（不猜測無結束碼或失敗的 E2E、實測修補安全邊界）。

## 2.6.0-agy.5（2026-11-24）

- 外掛 `seatbelt_hook.py` 獨立可攜式隔離、工作區與外掛結構精簡（G5）、版本化防碰撞工具升級與 30/30 自檢強化。
- 對齊初衷（PURPOSE.md）：降低常駐 token 膨脹並隔離不可靠邊界，維持安全護欄不因重構失效。

## 2.6.0-agy.4（2026-11-24）

- 強化 P1 稽核重建、`agy_setup.py` 跨進程互斥鎖、Worktree Stop 綁定（G6）、`agy_usage.py` `workspacePaths` 歸屬（G7）、Skills 對齊與 Ubuntu `python3`-only CI 檢查。
- 對齊初衷（PURPOSE.md）：確保多工與 worktree 下不丟失狀態、用量誠實歸屬且跨平台無預設路徑假設。

## 2.6.0-agy.3（2026-11-24）

- 強化候選提交 `scope_check --candidate`、transcript 終態分類、獨立狀態隔離與版本升級預檢。
- 對齊初衷（PURPOSE.md）：在乾淨工作副本也可核實範圍並防止驗收與狀態混線。

## 2.6.0-agy.2（2026-11-24）

- 對齊官方 Antigravity plugin schema、補齊 `seatbelt_hook.py` 與非破壞性 `.agents/*.json` 合併邏輯。
- 對齊初衷（PURPOSE.md）：保留使用者既有設定且僅於工具層落實防呆。

## 2.6.0-agy.1（2026-11-24）

- 首個 Antigravity 完整移植版（Plugin、PreToolUse/PostToolUse/PreInvocation/Stop 雙段式橋接、hash-chained `agy_audit.py` 與 `agy_usage.py`）。
- 對齊初衷（PURPOSE.md）：建立工具級閘門、不可竄改稽核與可重現的用量盤點。
