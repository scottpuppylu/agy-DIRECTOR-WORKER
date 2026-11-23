<!-- PO 待決帳（docs/03）。新項目加在「未關閉」最上面；關閉後移到「已關閉」。 -->
# PO 待決帳 — <專案名稱>
UPDATED: <YYYY-MM-DD HH:MM ±TZ，讀系統時鐘>
回覆方式：總監優先用 `ask_question` 提問（Antigravity UI 彈出選項，支援多選與自訂回答），或於對話直接回覆「OD-01：A」。本檔是唯一的長期紀錄：總監每一輪先讀這頁和對話尚未登記的回答（`docs/03`），並把 PO 回答原文寫進 `ANSWER`。

<!-- 「初衷卡確認 + 授權開工」（docs/12 第三節第 6 點）需補充紀錄：CARD_REV（卡版次）、ORDER / ORDER_REV / ORDER_SHA256（首批工單與雜湊）、LANES（開幾位子代理或 Swarm 成員、模型選擇）、SCOPE（影響檔案與工作區模式 workspace）、COST（Antigravity 配額估計，標明「估計」）。回覆後依「只鎖卡 / 先不動工 / 鎖卡且照問」處理。 -->

## 未關閉

ID: OD-01（要 PO 決定）或 ACT-01（要 PO 動手）
CREATED: <YYYY-MM-DD HH:MM，讀系統時鐘>
STATUS: open | answered | closed
QUESTION: <一句話>
QUESTION_TOOL: <ask_question 問的題號或標題；只在對話問的寫 NONE>
OPTIONS: <A / B / C，各一句影響；推薦項置頂標示 (Recommended)>
RECOMMENDED: <總監建議與一句理由>
IMAGES: <要 PO 看畫面判斷時附圖路徑或 Artifact 連結，同一視角的前後對照放一起並標明方案；沒有寫 NONE>
SOURCE: <回覆從哪來：ask_question / 聊天；沒有回覆寫 NONE>
ANSWER: <PO 填，或總監把回覆原文貼上>
RESOLUTION: <總監處理結果、時間>

## 待預覽（看過真實畫面才選；不發待答、不編 B 號）

- <配色／字型／版面…>：<要等什麼畫面（至少兩種真實畫面，或待 `dw-visual-qa` / `browser` 截圖對照）>

## 已關閉
