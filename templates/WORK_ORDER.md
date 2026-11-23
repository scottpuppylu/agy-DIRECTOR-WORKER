# WO-<編號> — <短名>
**<一句話目標>**

```text
ISSUED_BY      = 總監  <YYYY-MM-DD HH:MM，讀系統時鐘>
REVISION       = 1    # 開工後每改一次 +1，並在 H 節加一列
TRACES_TO      = <這張工單服務的初衷卡、需求或決定編號，逗號分隔，例如 B-2, DD-03；有初衷卡時至少一個 B 編號（docs/12）；沒有寫 NONE>
BRIEF_REVISION        = <選用：初衷卡版次；不用就刪掉這兩行>
BRIEF_FINGERPRINTS_V1 = <選用：gate_check.py --brief BRIEF.md 印的 {"B-2": "<64 位雜湊>"}>
PRIMARY_WORKER = 子代理「<名稱>」（例：w1；名稱只能小寫英數和 -；總監用 invoke_subagent 搭配 dw-worker 或 swarm 開啟）
MODEL / EFFORT = <模型（例如 inherit / flash / pro）> / <low|medium|high|xhigh|max>（理由見 docs/05；開工後不隨意切換；派工與交件可用 agy_usage.py 追蹤用量）
FALLBACK       = <同 conversationId 以 send_message 從 RESUME_STATE 接續 | 開接班子代理>
GOVERNANCE     = <Direct | Standard | Strict>
BASE_REF       = <remote/branch @ commit，例：origin/main @ 1bfcb4a>   # 開工前先 git fetch，寫最新版本；落後時 gate_check --order 印 WARN BASE_REF（docs/02）
NEW_BRANCH     = <分支名>
WORKTREE       = <預設：invoke_subagent workspace="branch"（隔離 clone）或 git worktree add；多作者併行寫入可用 swarm workspace="share"；報告的 WORKTREE 行由 scope_check.py / gate_check.py 印出，不手填>
WORK_DIR       = <放報告、PLAN.md、RESUME_STATE.md、證據與暫存輸出的資料夾>
BUDGET         = <US$0 / 配額估計 | PO 核准的金額>
HEAVY_RESOURCE = <無 | 需先 TEST REQUEST：類型、預估分鐘>
WORKER_LIMIT   = <80 輪或 context 60%，先到先停（預設，可改）>   # 到上限就停：寫 HANDOFF.md、回報總監，總監開新的子代理接續（docs/02）
```

兩個 `BRIEF_` 欄位是選用的：寫了以後，工單依據的初衷卡列被改掉時，`gate_check.py --order / --match --brief BRIEF.md` 印 `~ BRIEF_STALE`（舊條件驗過，新條件待重驗）；只是提示，不改結果。不寫照樣通過。

## A. 背景
需求、已知事實（附證據等級：`VERIFIED_LOCAL` / `REPORTED` / `ESTIMATED` / `ASSUMPTION` / `UNKNOWN`）、限制。只寫 worker 需要知道的。

## B. 目標
- MUST HAVE：
- OPTIONAL：
- 不做（OUT OF SCOPE）：<這次明確不做的範圍；沒有就寫「無」，不可留空>
- 可呼叫的入口：<交付物是函式庫或工具時，一個公開、測試可直接呼叫的函式：輸入路徑或資料 → 輸出結果，例 `report.build(path) -> Report`；不能只能從命令列用。不是函式庫或工具就寫「不適用」>

可以改和不准碰的路徑（`tools/scope_check.py` 和 `PreToolUse` hook `agy_guard.py` 讀這兩份清單）：

```text
SCOPE:
- <相對 repo 根目錄的路徑或 glob，一行一個>
FORBIDDEN:
- <不准碰的路徑或 glob；沒有就寫 NONE>
```

大小上限（Standard／Strict）：最後結果 ≤3 個檔、約 200 行（新增＋刪除），`scope_check.py` 超過就印 `OVERSIZE`。上限寫在 `director_work.json` 頂層 `order_limits`；測試檔（`test_*.py`、`*_test.py`、`tests/` 下）算檔數，行數另算 `max_test_lines_per_order`（預設 400），不吃掉 200 行。估計會超過就拆成幾張工單。`FORBIDDEN` 加上 `TESTS_LOCKED` 的測試檔。

計畫閘門（Standard／Strict，`safety.plan_gate`，docs/13）：第一步只寫 `<WORK_DIR>/<本工單編號>/PLAN.md`（`- [ ] 1.1 …`，每項對到一條 AC），用 `send_message` 或交回給總監，**等總監回覆已核准（`DIRECTOR_LOG` 有 `PLAN_APPROVED`）才寫程式**。之後照 `PLAN.md` 做，做完一項打勾一項；要加、改、刪項目先回報總監。不改 `TESTS_LOCKED` 的測試。

`*` 不跨資料夾，`**` 跨資料夾，沒有萬用字元的名稱連底下全部都算。兩邊都符合時以 `FORBIDDEN` 為準。不要寫「相關檔案」這種模糊詞。

如果派工採用 `workspace: "inherit"`（共用工作副本，未開 `branch` / `worktree`），`SCOPE` / `FORBIDDEN` 與 Single Writer 原則是唯一的寫入邊界；總監交件時用 `scope_check.py` 對 diff，再看實際改動。多工同時寫碼優先改用 `workspace: "branch"`（或 `swarm` 的 `workspace: "share"`）。

選用：一定要改到的路徑。`scope_check.py` 會查每一條都有對到改動，沒對到就印 `MISSING` 並判越界；每一條都要在 `SCOPE` 裡、不在 `FORBIDDEN` 裡（`gate_check.py --order` 會查）。不需要就整段不寫。比的是最後結果：改了又改回的不算；總監審查時用 `scope_check.py --order 本工單 --repo <repo> --candidate <候選 commit>`。

```text
REQUIRED_CHANGES:
- <一定要改到的路徑或 glob，例如 src/parser.py>
```

## C. 執行前檢查
repo 與分支、工作樹乾淨、未提交修改、相關規範、相依、實際行為、能否重現、基準測試。路徑不存在先搜尋，不假設。
製作類工單先做既有資產盤點：未合併分支（`git log --all -- <路徑>`）、需求方提供過的檔案、舊報告；結果寫進 TASK REPORT 的 `DONE`（docs/02）。

## D. 實作要求
沿用現有架構；最小但完整；處理例外；必要的向後相容；補上直接相關的測試；不做無關重構。每項修改做成開關，方便批次驗證（docs/02）。

- 行數上限：<每個檔或函式的行數上限，或「無」>；worker 自檢時自己量，結果寫進 TASK REPORT。
- 新增任何上限（大小、筆數、深度）都寫理由，並拿真實資料的最大值確認擋不到。
- 程式最上層攔下所有例外，使用者看不到 Traceback；錯誤訊息不回顯控制字元、不顯示原始 Python 例外文字，該報的問題照報。

## E. 驗收條件
| ID | 類型 | 要求 | 驗證方法 | 預期 |
|---|---|---|---|---|
| AC-1 | <AUTO / REVIEW / PO> | | | |

- `AUTO`：有指令可以重跑。總監自己重跑後判定；驗證指令用 `evidence_record.py` 留紀錄。核准的指令寫在下面的 `CHECKS:`（選用，Strict 建議寫），一條 AUTO 一行，JSON 字串陣列、不經 shell；` #` 之後是註解，要保留 ` #` 就把整個項目用反引號包起來；派工雜湊會鎖住它，總監用 `evidence_record.py verify --rerun --order` 跑的就是這一行，worker 用同一個指令記錄（`evidence_record.py run --order <本工單>`）：

```text
CHECKS:
- AC-1: ["python", "-B", "-m", "unittest", "tests.test_parser"]
```

  路徑一律用正斜線（`D:/ws/tools/x.py`，Windows 也認得）：JSON 裡的反斜線不是合法字元（`\w`），或會悄悄變成控制字元（`\t`、`\n`），`gate_check.py --order` 派工前就擋下並指出第幾行。
  `CHECKS` 可以放任何可重跑的指令，包括本規則包的檢查工具（路徑照專案實際位置寫）：`["python", "-I", "-X", "utf8", "<WS>/DIRECTOR/tools/round_check.py", "--resume", "<WS>/DIRECTOR/RESUME_STATE.md"]`。放進 `CHECKS` 就等於核准它在 `--rerun` 時被執行。
- `REVIEW`：要有人看成品。開工前寫明誰看、從哪個角度看、看多大範圍、看哪些常識閘門項目（docs/07）。程式碼／文件盲審交給唯讀子代理 `dw-blind-reviewer`（YAML 僅授予 `read_file`）；網頁或即時介面視覺檢查交給 `browser` 或 `dw-visual-qa`（總監必須核對系統自動產生的 `BROWSER_HOST_FACTS`）。worker 最多填 `READY_FOR_REVIEW`。
- `PO`：PO 保留的判定，例如最終驗收。worker 和總監都只填 `PENDING_PO`。同一個 commit 給 PO 看兩份以上成品時，報告寫 `CANDIDATE_ID: <PO_ACCEPTANCE 的 candidate_id>`。
- 接手演練、要用 `metrics_report.py --run --compare` 比較的工單：run 資料夾的 `run.json` 要寫 `sampling` 標記（`complete`、`scenario`），否則比較結果是 `UNKNOWN`。
- 邊界與壓力清單（寫 AUTO 條件時逐項想過，用得到的寫成 AC 或測試案例）：
  - 遞迴／巢狀深度（例如 1000 層括號）
  - 輸入長度（空字串、極長輸入）
  - 重複的一元運算子或符號（`--5`、`+++`、`((((`）
  - 極大數字、溢位、除以零
  - 不是字串的輸入（None、數字、list）
  - Unicode 與全形字元（全形數字、全形運算子）
  - 錯誤訊息不漏出程式語言的原生訊息（traceback、RecursionError）或英文訊息，照工單規定的語言與格式
  - 巢狀深度（含 JSON）：讀檔或解析 JSON 時，深層巢狀（例如 1000 層 `[[[…]]]`）也回清楚的錯誤，不透出 RecursionError
  - 預設輸出要列出哪幾筆有問題：批次輸入有錯時，不加參數的預設輸出就列出是哪幾筆（第幾筆或哪個鍵）；問題物件用 dict 或中文名稱，不要用 `XxxError` 類別名當使用者看到的字
  - 需求寫「不當掉／不崩潰」時，至少一條 AC 用極端輸入驗證（例如上面的深度或長度）
  - 怪檔案：讀檔的工具拿 `templates/weird_inputs.py <資料夾>` 產生的空檔、BOM、控制字元、超長行、深層巢狀、錯誤編碼逐一餵過
  - 期望值由獨立的 oracle 或手算表產生，不由寫程式的人邊寫邊抄
- 選用的 `追溯` 欄（加在表的最後一欄）：這條 AC 驗證 `TRACES_TO` 的哪些編號，例如 `DD-03`；沒有寫 `—`。不加這欄也照樣通過；加了之後 `trace_report.py` 每個編號一列，不必猜哪條 AC 服務哪個需求。

- 驗收優先寫 `AUTO`：能用指令判定的就不要寫成 REVIEW／PO。
- 測試先寫並鎖定（Standard／Strict 必填，`safety.tests_first`）：驗收測試在派工前寫好並 commit 到 `BASE_REF`，`python -I -X utf8 <RULES>/tools/safety_gate.py --lock-tests <測試檔> --repo <專案>` 印的清單貼在下面；worker 不得改，改了 `gate_check.py` 判不過。沒有測試的文件類工單寫 `- NONE  # 理由`。`scope_check.py` 也把它們當受保護的測試。
- 受保護的測試只准新增：`TESTS_LOCKED` 的檔和 `director_work.json` 頂層 `protected_tests` 列的檔，worker 只能新增檔案或在檔尾加測試；改到原有內容或刪檔，`scope_check.py` 判 `PROTECTED` 越界。`scope_check.py` 印的 `PROTECTED_CHANGED:` 行照抄進報告；寫「無」而 diff 有改，`gate_check.py --match` 判不過。

```text
TESTS_LOCKED:
- <相對專案根目錄的測試檔> sha256=<64 位>
```

結果不寫在這裡，寫在 TASK REPORT 的 `ACCEPTANCE` 表。成品類交件另附常識閘門表（docs/07）。

## F. 安全與版控
照 docs/08。驗收條件和規格不准改，有問題回報 `BLOCKED` 由總監裁決（docs/02）。總監派工時記下本檔雜湊，交件時比對；`agydw` 的 `PreToolUse` hook（`agy_guard.py`）會依 `--mode remind|ask|block` 提醒或攔截破壞性指令、憑證讀取及 `--protect` 受保護路徑，交件時仍由 `gate_check.py` / `scope_check.py` 再次稽核。本工單特別禁止：<...>

## G. 交付與停止規則
- DELIVERABLES：<檔案、commit / tag、審查包>
- 完成：一則完整的 TASK REPORT（`templates/TASK_REPORT.md`）送回總監（`send_message` 或子代理回報／Swarm 看板）。
- STOP_RULES：同一根因 2 輪沒進展就停並回報（或同一錯誤達 2 次先寫明根因再決定是否重試）；超出 `SCOPE` 先問；超過測試上限 10 分鐘先停下回報。
- 先用 `agydw` 現有工具：檢查、比對、等待、狀態整理先找 `tools/README.md` 與 `tools/AGY-TOOLS.md`，自己寫的要說明為什麼現有工具不夠。
- 根因研究：`ROOT_CAUSE_STUDY.md` 每一節先放「測試條件 vs 參考條件」表，再寫 WHERE / ALTERNATIVES / DECISION。
- 放寬量測標準前先做一次盲測；確認前舊標準照用。
- 宣告一組做完前，核對資料夾、程式、提示詞是同一組。
- 和真實畫面／影片對照：每一種情境類型在成品內並排；設計原創，不複製他人的素材或標誌。
- 打轉與回報：同一呼叫失敗兩次，先寫一行「為什麼失敗、下一步改什麼」再試；派出的子工作逾時沒有結果或出錯，回報總監，不可靜默等待；宣告完成前，本輪跑過驗證指令並附 `RAN` 行（`ran_line.py`），可用 `agy_audit.py loops` 檢查。
- 到 `WORKER_LIMIT` 就停：寫 `HANDOFF.md`（`templates/HANDOFF.md`：做完什麼、還剩什麼、下一步、檔案路徑），回報總監開啟接續子代理。

## H. 變更紀錄
只有總監能改本工單。`REVISION 1` 不用寫；之後每改一次加一列，同步 `BRIEF.md`、`WORK_DIR/tasks.md`、`WORK_DIR/RESUME_STATE.md`，再把新的雜湊告訴 worker（`gate_check.py --order` 對比舊的 `tasks.md`／`RESUME_STATE.md` 印 `REVISION_SYNC`）。

| 版本 | 時間 | 誰改 | 原因 | 影響的 AC |
|---|---|---|---|---|
