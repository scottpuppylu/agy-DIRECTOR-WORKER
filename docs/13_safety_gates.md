# 13 安全閘門（承襲 `dshdw` 安全閘門與 Antigravity 執行期雙層防線）

本檔是 `agy-DIRECTOR-WORKER`（簡稱 `agydw`）寫 Standard／Strict 工單前的安全閘門規則。規則摘要在 `docs/02`；本檔說明完整流程、設定開關和「誰能關」。

## `agydw` 的雙層安全升級

承襲 `dsh-DIRECTOR-WORKER` 實戰證實有效的事前鎖定設計（不加回 OpenSpec），並在 Antigravity 上升級為**事前鎖定＋執行期硬約束**：

1. **事前鎖定（`safety_gate.py`）**：驗收測試（`TESTS_LOCKED`）與實作計畫（`PLAN_APPROVED`）在寫程式前就以 SHA-256 鎖住；工單並經過獨立規格審查（`SPEC_REVIEW`）。
2. **執行期攔截（`agy_guard.py` + `dw-blind-reviewer`）**：
   - Antigravity `PreToolUse` Hook（`--protect <DIRECTOR>`）在工具層保護 `orders/`、`DIRECTOR_LOG.md`、`.agents/` 與憑證。
   - 獨立審查交由只配賦 `read_file` 工具的 `dw-blind-reviewer`（物理唯讀），且多線寫入隔離於 `workspace: "branch"` / `"share"`。

## 為什麼需要這套雙鎖

- 任何模型在交件壓力下都可能直接修改測試或跳過計畫；只靠提示詞約束容易失效。
- 事前雜湊鎖定 + `PreToolUse` 工具層防護，讓偏離與越權在執行當下就被攔截、或於交件時由程式自動判 `FAIL`。

## 流程（Standard／Strict）

| 步 | 誰 | 做什麼 | 留下什麼 |
|---|---|---|---|
| 1 | 總監 | 寫需求：寫在工單的 ACCEPTANCE。逐項想過 `templates/WORK_ORDER.md` E 節的邊界與壓力清單，用得到的每一項都要有一條 AC 或測試；需求寫「不當掉」時，至少一條用極端輸入 | 工單草稿 |
| 2 | 總監（或測試子代理） | **測試先寫**：照 AC 寫驗收測試，commit 到 BASE_REF。`python -I -X utf8 tools/safety_gate.py --lock-tests <測試檔> --repo <專案>` 印出 `TESTS_LOCKED:`，貼進工單。`CHECKS:` 寫跑這些測試的指令（AUTO 優先） | 工單 `TESTS_LOCKED`、`CHECKS` |
| 3 | 總監 | 完成工單：一張最多 3 個檔、約 200 行，超過就拆；`FORBIDDEN` 加上鎖定的測試檔；`STOP_RULES` 寫「同一根因 1 輪沒進展就停」；`WORK_DIR` 寫好（計畫檔放在那裡） | 工單 |
| 4 | 總監 | 跑 `gate_check.py --order <工單> --repo <專案>`，要通過。這一步會比對鎖定測試的雜湊、輪數，也會查安全開關有沒有被關掉 | — |
| 5 | 總監開的 `dw-blind-reviewer`（或 `run_workflow`） | 規格審查，照 `templates/SPEC_REVIEW.md`；Standard 也要做。總監在 DIRECTOR_LOG 記 `SPEC_REVIEW <WO> tokens=… issues=… adopted=…` | DIRECTOR_LOG |
| 6 | 總監 | 派工：`gate_check.py --hash` 的 `DISPATCH` 行寫進 DIRECTOR_LOG；透過 `invoke_subagent` 或 `swarm` 開隔離 worker（`workspace: "share" \| "branch"` 或獨立 worktree，`docs/11`） | DIRECTOR_LOG |
| 7 | worker | **計畫閘門**：只寫 `<WORK_DIR>/<WO>/PLAN.md`，格式是 `- [ ] 1.1 …`，每項對到一條 AC。用 `send_message` 或 Swarm 留言板交給總監，**等核准才寫程式** | PLAN.md |
| 8 | 總監 | 看計畫有沒有照 AC、有沒有超出 SCOPE。核准就跑 `safety_gate.py --plan-hash <PLAN.md> --order <工單>`，把印出的 `PLAN_APPROVED <WO> sha=<64>` 整行寫進 DIRECTOR_LOG，再通知 worker | DIRECTOR_LOG |
| 9 | worker | 照 PLAN.md 實作，做完一項打勾一項。不改鎖定的測試，不加項目（要改計畫先回報總監）。同一根因 1 輪沒進展就停並回報 | commit |
| 10 | worker → 總監 | 交件照 `docs/02`：`scope_check.py`（含大小上限）、`gate_check.py --match … --dispatch-log DIRECTOR_LOG.md --repo <專案>`、`evidence_record.py`，外加 `dw-blind-reviewer` 獨立審查 | TASK REPORT |
| 11 | 總監 | 收尾：最終端到端驗收、`agy_usage.py --out` 與 `gate_check.py --final`（`BOOTSTRAP`「收尾」） | 裁決 |

Direct 工單不走這套（純查詢、文件、沒有風險）。

## 程式會擋什麼

| 規則 | 誰查 | 什麼時候 | 開關（`director_work.json` 的 `"safety"`，預設全開） |
|---|---|---|---|
| 測試先寫且鎖定：Standard／Strict 要有 `TESTS_LOCKED`（docs-only 寫 `- NONE  # 理由`），檔案雜湊不符就**判不過** | `gate_check.py --order / --match` | 派工前、交件時 | `tests_first` |
| 規格審查：要有 `SPEC_REVIEW <WO> tokens=… issues=… adopted=…` 整行。沒給 `--dispatch-log` 也查：工單所在 DIRECTOR 的 DIRECTOR_LOG.md，或 `reviews/` 下檔名有工單編號和 SPEC_REVIEW 的檔。找不到就**判不過**；行裡缺 tokens／issues／adopted 印 note | `gate_check.py --match` | 交件時 | `spec_review_for_standard`（Strict 一律要） |
| 計畫閘門：DIRECTOR_LOG 要有 `PLAN_APPROVED <WO> sha=<64>`；`<WORK_DIR>/<WO>/PLAN.md` 和核准的版本比，只准多打勾，交件時要全部勾完（只有 `[x]` 算完成）；找不到 PLAN.md 也**判不過** | `gate_check.py --match --dispatch-log` | 交件時 | `plan_gate` |
| STOP_RULES 允許的輪數不能超過 1 | `gate_check.py --order` | 派工前 | `stop_after_rounds` |
| 至少一條 AUTO 驗收（只提示，不是閘門；關掉只印 note） | `gate_check.py --order` | 派工前 | `prefer_auto` |
| 安全閘門不得自行放寬：對 Standard／Strict 把 `tests_first`、`plan_gate`、`spec_review_for_standard` 設成 `false`，或把 `stop_after_rounds` 設成 0／`false`、調高到超過 1，要有 PO 的答覆；還在 open 或沒寫就**判不過**（下一節） | `gate_check.py --order` | 派工前 | — |
| 工單大小：最後結果最多 3 個檔、200 行（新增＋刪除）；測試檔（`test_*.py`、`*_test.py`、`tests/` 下）算檔數，行數另算，最多 400 | `scope_check.py` | 交件時 | 頂層 `"order_limits"`：`max_files_per_order`、`max_lines_per_order`、`max_test_lines_per_order`（0＝關） |
| 受保護的測試只准新增：`TESTS_LOCKED` 的檔和頂層 `"protected_tests"` 列的檔 | `scope_check.py`、`gate_check.py --match` | 交件時 | `"protected_tests"` |

沒寫的鍵一律用預設值（全開）。把下面這段併進專案的 `DIRECTOR/director_work.json` 就是完整寫法（`director_work.example.json` 已附上同一段）：

```json
"safety": {
  "tests_first": true,
  "plan_gate": true,
  "spec_review_for_standard": true,
  "stop_after_rounds": 1,
  "prefer_auto": true
},
"order_limits": {
  "max_files_per_order": 3,
  "max_lines_per_order": 200,
  "max_test_lines_per_order": 400
}
```

`scope_check.py` 的 `SIZE:` 行印三個數：`files= lines= test_lines= limit=3/200/400`。

工具通過只代表測試沒被改、計畫沒被偷換、審查有記錄、改動夠小。**不代表需求寫對了**。需求對不對，靠第 5 步的審查和總監自己讀。

## 誰能關（2.2.3-ds.3 起；2.2.5-ds.5 套到安全開關）

WO-DS-004 試驗：DSH lead 自己寫一筆 DD，把保護關掉，整層規則沒用上。所以：

- **lead 不得自行關閉或放寬安全閘門。** 要對 Standard／Strict 關掉 `tests_first`、`plan_gate`、`spec_review_for_standard`，或把 `stop_after_rounds` 設成 0／`false`、調高到超過 1（例如 2），先在 PO_PENDING 寫一項（寫明開關名稱、理由、建議），等 PO 回答。
- PO 答應後（STATUS answered／closed，有 ANSWER），`gate_check.py --order` 才會放行，只印 note。沒寫、或還是 open，`--order` 判不過。
- `prefer_auto` 只是提示，不是閘門：設成 `false` 不用 PO 答覆，`--order` 只印一行 note。

## 舊設定檔的 `"sdd"` 區塊

舊的 `"sdd"` 區塊照讀，不會報錯。工具會印 WARN：

- `tests_first`、`plan_gate`、`spec_review_for_standard`、`stop_after_rounds`、`prefer_auto` 照讀，印「請搬到 safety」。
- `"safety"` 也寫了同一個鍵時，以 `"safety"` 為準，`sdd` 那一個印「被忽略」。
- `openspec_bin`、`require_spec_for_*` 和其他鍵忽略，印「OpenSpec 已在 2.2.5-ds.5 移除，此鍵被忽略」。
- `sdd.max_files_per_order`／`sdd.max_lines_per_order` 照讀，印「請搬到 order_limits」（2.2.4-ds.4 起）。
- `bootstrap_repo.py` 時期的舊 OpenSpec 設定邏輯已不存在。
- 工單還有 `SPEC_CHANGE` 這一行時，只印 note，不檢查。
