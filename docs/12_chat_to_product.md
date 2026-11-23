# 12 對話到成品（一般模式與小白模式）

不管 PO 是老手還是第一次使用 AI，都適用本流程：**先選定模式 → 再談需求寫專案初衷卡（`DIRECTOR/BRIEF.md`）→ 鎖卡後才派工 → 用對話與選擇題取代技術盯場。**

## 一、兩種模式怎麼選

1. **啟動第一步先問模式**：新專案開局（不論從 Plugin starter prompt 或啟動提示詞開始），總監第一則就用 `ask_question` 問 PO 選「一般模式」還是「小白模式」，記在 `DIRECTOR/PO_PENDING.md`、`DIRECTOR/director_work.json`（`"mode"`）與 `DECISION_REGISTER.md`。
2. **兩者安全底線完全相同**：`git push`、合併到 `main`、公開發布、付費、刪除、帳號與全域設定，一樣都要 PO 核准（`docs/08`）；差別只在問法：小白模式全用白話（例如「要把成品放到網路上讓別人看嗎？」），技術選型由總監決定並登記 `DD`。

| BOOTSTRAP 步驟 | 一般模式 | 小白模式（快速路徑） |
|---|---|---|
| 建置 `DIRECTOR/` 與 `.agents/` | 照 `BOOTSTRAP.md` 一步步做完並跑 `agy_doctor` | 先在專案外建最精簡 `PO_PENDING.md` / `BRIEF.md`，談完需求、開第一位 worker 前再完整建置 `.agents/` 與 `DIRECTOR/` |
| 安全模式預設 | 問 PO 選 `remind` 或 `ask`（`force_ask`） | 採預設 `remind` 安全帶，不問設定細節 |
| 派工編制 | 總監提出工作線草案請 PO 拍板 | 總監依 `docs/05` 決定首條工作線與模型，登記 DD，將開工授權合併在初衷卡確認句 |
| 第一份回報 | 完整六點（含 `agy_doctor` 與基準數據） | 三句白話：準備好了、接下來聊需求、請先回答哪一題；技術細節寫檔 |

## 二、談需求與鎖定專案初衷卡（`templates/BRIEF.md`）

1. 開工前一定先有 `DIRECTOR/BRIEF.md`（若已有 `LOCKED` 卡則跳過）。
2. 一次問 1–3 題，從大到小：**做給誰用？第一版做到哪一件事就算有用？一定要有什麼？絕對不要什麼？做完長什麼樣子？** 小白模式避免術語，給 2–3 個生活化選項讓 PO 挑。
3. 談妥後把整張初衷卡用白話貼出確認；PO 回覆同意後才將狀態改為 **`LOCKED`**，登記時間與 `DECISION_REGISTER.md`，並跑一次 `gate_check.py --brief BRIEF.md --snapshot brief-history` 將 `BRIEF_LOCKED` 寫進 `DIRECTOR_LOG.md`。
4. **鎖卡後只有 PO 能改**：發現需求矛盾或不可行時，透過 `ask_question` + `PO_PENDING.md` 請 PO 裁決後改版（版次 +1，重鎖）。
5. **待預覽項目（不編 B 號）**：配色、字型、版面等沒看到真實畫面無法決定的偏好，放進 `BRIEF.md` 與 `PO_PENDING.md` 的「待預覽」區，避免 `trace_report.py` 誤報未指派工單。做出第一個可預覽畫面（至少兩種真實選擇）後再提問。

## 三、一句話同時確認初衷卡與授權開工

把初衷卡摘要與開工說明放在同一則貼出，清楚交代：
- 第一批交付範圍與驗收點；
- 開幾條 worker、使用現有 Antigravity 方案額度、額外費用（0 元就寫 0 元；時間估計要標「估計」）；
- 會改動哪個資料夾與工作副本、本機 `.agents/hooks.json`／`.agents/plugins.json` 與 `.git/info/exclude` 變更；
- 公開、推送、付費仍需另行同意。

PO 回覆判讀表：

| PO 回覆 | 判讀 | 總監動作 |
|---|---|---|
| 只確認卡片對（未說開工） | `LOCK_ONLY` | 鎖卡；開工另案詢問 |
| 卡仍為草稿只喊開工 | `NO_START` | 不開線；請 PO 先確認初衷卡 |
| 明確確認卡片並同意開工 | `LOCK_AND_USER_ASKED` | 鎖卡 → 完成 `agy_setup.py --apply`（小白模式補齊完整建置）→ 跑 `gate_check --order` 與追溯核對 → 派工 |

## 四、待決互動方式（無雲端 Artifact 依賴）

1. **主要提問通道（`ask_question` + `PO_PENDING.md`）**：
   - 所有需 PO 拍板的選擇題先寫入 `DIRECTOR/PO_PENDING.md`（狀態 `open`，含 `OPTIONS` 與 `RECOMMENDED`），再用 **`ask_question`** 在對話框跳出清楚選項；PO 也可直接輸入「`OD-05：A`」。
2. **視覺對照與互動審閱（Artifacts / `generative_ui`）**：
   - 需要 PO 看真實畫面挑設計、比對前後差異時，使用 Antigravity 本機預覽（`/browser` 截圖放進工作區或 Artifact 目錄）或 **`generative_ui` / Markdown 圖片並排展示**，同題寫清方案 A/B，再用 `ask_question` 收取回答並原文回寫 `PO_PENDING.md` `ANSWER`。
   - **不依賴第三方雲端託管服務**：不把未經同意的專案資料上傳至外部服務。
3. **成品驗收與修正**：
   - 嚴格區分工程完成與成品可交付；呈現成品前必經 `dw-blind-reviewer`（及 UI 類由 `browser` subagent 實測並核對 `BROWSER_HOST_FACTS`）。PO 說哪裡不對即開修正工單，不推卸或自行宣布結案。
