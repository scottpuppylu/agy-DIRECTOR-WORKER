# TEST REQUEST（送總監／排程管理；單人執行時寫進 RESUME_STATE 自己守）

```text
TEST REQUEST: <FUNC|BENCH> <內容> <上限分鐘>
FROM: <worker 名稱與 conversationId>
PURPOSE: 這次測試要回答哪一個決定或驗收項
MIN_SCOPE: 最小範圍，以及為什麼不能更小
TIER: 1 批次（預設） | 2 單項（寫理由：P0 / PO 在等 / 下一步依賴 / 耦合無法用開關分 / 單一根因驗證）
CHANGES: 這次修改包含的每一項變更，以及對應的開關
SWITCHES: 要跑的開 / 關組合
EXCLUSIVE_MIN: 需要獨佔重資源／安靜時段的分鐘數（沒有寫 0）
NORMAL_MIN: 一般時段的分鐘數
WRITE_GB: 預計寫入的空間
TIER0_DONE: 第 0 級（離線、純邏輯）檢查已做完的項目
```

收到總監核准後寫 `ACK` 並記進 `RESUME_STATE.md`；需要背景執行與定時回報時，使用 `run_command` 背景任務 + `manage_task`（不阻塞輪詢）或 `schedule`；結束回報 `DONE <內容> <實際分鐘>`；超過預估上限 10 分鐘先停下並回報總監。
