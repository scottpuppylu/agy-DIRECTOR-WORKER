<!-- BOOTSTRAP 第 3 步其餘檔案的骨架。也可執行 `python -I -X utf8 <RULES>/tools/agy_setup.py --repo <REPO> --director <DIRECTOR> --init-director --apply` 自動產生初稿；手動建立時把每段複製成 DIRECTOR/ 下的同名檔並替換 <...>。 -->

## DIRECTOR_OPERATING_RULES.md

```text
# 本專案版規則 — <專案名稱>
只寫和規則包不同的地方；沒寫的照 <RULES>/docs/xx。
| 規則包位置 | 本專案的做法 | 來源（PO-xx / DD-xx） |
（關閉 `safety.*` 安全開關這種改動，來源必須是 PO 的答覆，不能只寫 DD；詳見 docs/13「誰能關」）
```

## DIRECTOR_STATE.md

```text
# DIRECTOR_STATE — <專案名稱>
UPDATED: <讀系統時鐘 YYYY-MM-DD HH:MM ±TZ>
PENDING_METHOD: ask_question + PO_PENDING.md
AGY: 總監 <Antigravity Coordinator>／Effort <high|xhigh|max，由 PO 在 UI 設定>／沙箱 <OS 原生沙箱：AppContainer / sandbox-exec / Linux namespace>／工作區隔離 <workspace: branch | share | git worktree>／PreToolUse hook <agy_guard.py --mode remind|ask|block>
## 專案概況
目標 / 目前階段 / 已知 PO 決定 / repo、分支、worktree 策略 / build 與測試方式、預估時間 / 共用重資源 / 輸出位置與磁碟 / 成品怎麼看
## 各工作線狀態
| 工作線（子代理／Swarm 名稱） | 工單 | 狀態 | 下一個 gate |
## 下一步
```

## ACTIVE_WORKSTREAMS.md

```text
# ACTIVE_WORKSTREAMS
| 名稱（子代理／Swarm） | conversationId | 角色 | 工單 | RESUME_STATE 路徑 | workspace（branch / share / worktree / inherit） | 模型 | 狀態（草案 / 執行中 / 停止） |
```

還沒開的線先寫草案（名稱欄留 `w1`…），開完填入 `manage_subagents`（`list`）或 `swarm`（`status`）看到的實際 ID 與狀態；要更換模型或從頭開線時，於本表更新接班人並保留舊線路徑作為追蹤依據。

## RISK_REGISTER.md

```text
# RISK_REGISTER
| ID | 風險 | 影響 | 可能性 | 對策 | 負責人 | 狀態 |
```

## DIRECTOR_LOG.md

```text
# DIRECTOR_LOG（append-only，一行一件事）
<YYYY-MM-DD HH:MM ±TZ> | <決定 / 事件> | <依據或 DD-xx / PO-xx>
```

時間用 `ran_line.py --stamp --to DIRECTOR_LOG.md --text "| <事件> | <依據>"` 從系統時鐘取，不手打。只往下加：
`gate_check.py --sequence` 會比對條目順序，Standard / Strict 工單的閘門格式如下：

```text
<時間> | DISPATCH WO-12 rev=1 sha=<64 位> | session=<寫工單的 conversationId> class=dev model=<模型>
<時間> | SPEC_REVIEW WO-12 tokens=<n> issues=<P0/P1/P2> adopted=<n> session=<審查者的 conversationId>
<時間> | PLAN_APPROVED WO-12 sha=<PLAN.md 64 位或 NONE> | session=<核准者的 conversationId>
```

`PLAN_APPROVED` 必須早於 worker 在這張工單的第一個 commit（標題以 `WO-12` 開頭）；可用 `gate_check.py --sequence 工單 --dispatch-log DIRECTOR_LOG.md --repo <repo>` 檢查。

## IDEAS.md（點子清單）

```text
# IDEAS（還沒變成工單的點子；採用時開工單，在狀態欄寫工單編號）
| ID | 點子 | 理由（要解決什麼、依據） | 估計成本（輪數、額度、重資源分鐘；寫依據） | 提出者 / 時間 | 狀態（待議 / 採用 WO-xx / 不做：原因） |
```

## GUARDS.md

```text
# GUARDS（docs/06、docs/06b）
| 機制 | 擁有者 | 中斷會怎樣 | 怎麼檢查 | 重建指令 |
```

agydw 的核心機制：
- Runtime 守門與稽核（`.agents/hooks.json`：`agy_guard.py`、`agy_hook_bridge.py`）
- OS 原生沙箱與工作區隔離（`workspace: "branch"` / `git worktree` + Windows AppContainer / sandbox）
- 待決帳同步（`ask_question`＋`PO_PENDING.md`）
- 用量與上下文檢查（`agy_usage.py`）
- 動作與打轉稽核（`agy_audit.py`）
- 環境健康健檢（`agy_doctor.py`、`agy_setup_check.py --strict`）
- 排程喚醒與背景工作（僅在 PO 核准時使用 `schedule`，搭配 `manage_task` 檢查）

## BASELINE_METRICS.md

```text
# BASELINE_METRICS
| 日期（讀系統時鐘） | 指令 | 指標 | 數值 | 證據等級（VERIFIED_LOCAL / ESTIMATED / UNKNOWN） |
固定成本：規則檔與記憶檔大小（`memory_size_check.py --kind rules` 的 bytes，對齊 Antigravity 24 KB 單檔上限）與 `agy_usage.py --json` 的 `lastPromptTokens`、`promptPercent`。
歷史用量：`agy_usage.py` 統計的 `inputTokens` / `outputTokens` / `cacheReadTokens` / `totalTokens`、最後一次請求的 prompt tokens、上下文佔比與 ≥25 萬讀入比例。
沒有資料寫 UNKNOWN，不寫 0。
```
