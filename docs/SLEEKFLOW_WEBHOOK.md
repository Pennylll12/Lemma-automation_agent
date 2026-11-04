# SleekFlow 入訊與回覆 Webhook

## 資料流與時間基準

客人 WhatsApp → Flow A（測試電話＋關鍵詞）→ POST `/webhooks/sleekflow` → SQLite 排隊 → Worker 識別／查日曆／生成回覆 → POST Flow B 的 Webhook → SleekFlow 發送 WhatsApp。

API 在進入入訊處理函式時記錄接收時間，存為 `received_at`，`time_source=server_received`。意圖識別使用其香港日期；工作台及執行日誌明示這個來源。入訊不再要求 `timestamp`；若舊流程仍提供此欄位，後端忽略，不猜測格式。去重的原始訊息 ID 再次收到時，不更新原先接收時間，也不再提交回覆。

延遲／重放的訊息會以現在的接收時間理解「明天」等字眼，不代表客人原來發訊的日期。系統的 24 小時檢查只根據已記錄的接收時間；**不能藉此證明真實 WhatsApp 客服窗口有效**，由 Flow B／SleekFlow 核對真正的最近入訊及 WhatsApp 發送規則。此版本不會自動選擇 WhatsApp 範本。

`channel` 仍必填，可暫用 `sleekflow-whatsapp-test` 記錄來源；它不是已驗證的 WhatsApp 渠道 ID。Flow B 的 Send message 節點需選擇真正的測試 WhatsApp 渠道，不要把這個固定標籤當渠道 ID。

## Flow A：傳入訊息（建議表單，解決多行文字）

在原有 Send HTTP request 節點：

- Method：POST。
- URL：目前 Codespace 的 8000 轉發網址，加 `/webhooks/sleekflow`。
- Header：`X-Webhook-Secret`，值必須等於 `.env` 的 `WEBHOOK_SECRET`，不附外層引號。
- Body type：**x-www-form-urlencoded**，使用逐項 Key／Value 欄位。
- 刪除手動設定的舊 `Content-Type: application/json`；確認實際使用 `application/x-www-form-urlencoded`。

| Key | Value |
| --- | --- |
| message_id | 變數選擇器：觸發客人訊息的 Message ID |
| contact_id | 變數選擇器：客人 Phone number（含國家碼） |
| message | 變數選擇器：Message content |
| channel | 固定 `sleekflow-whatsapp-test` |
| direction | 固定 `inbound` |

**不再傳 timestamp**。表單 Value 直接選變數，不手動增加 JSON 雙引號或括號。保存並 Publish。密鑰、電話白名單、Body 64KB 上限仍保留，重複欄位名稱會被拒絕。`message_id` 最長150字，訊息最長4000字。

後端仍相容 `application/json`，但 raw JSON 的換行與引號必須由上游正確轉義；刪除 timestamp 並不能修復無效 JSON。若傳送合法 JSON，最少為：

```json
{
  "message_id": "unique-message-id",
  "contact_id": "85290000001",
  "message": "想約驗樓\n4/11成交",
  "channel": "sleekflow-whatsapp-test",
  "direction": "inbound"
}
```

API 回傳 `accepted: true` 只代表持久排隊，不會等待 AI 回覆。`duplicate: true` 是已收過同一事件；`ignored: true` 必須查看 reason（電話白名單／非入訊）。422 的安全原因會寫在 API 終端，不列印完整客人資料或密鑰。

## Flow B：收到回覆後向測試電話發送 WhatsApp

1. 新建獨立 Flow，例如 `TEST_INSPECTION_REPLY_CALLBACK`。
2. Trigger 選 **Webhook payload received**（介面也可能顯示 Webhook received）。生成 URL／Webhook trigger ID／Webhook token。此功能按官方文件須 Premium 或 Enterprise。
3. 將它產生的 **Webhook sample POST request** 的 URL 及 Header 名稱／值填入下面 `.env`。Header 名稱以你帳戶產生的範例為準，不要使用入訊端的 `X-Webhook-Secret` 代替，不要把 Token 貼到聊天或 Git。
4. Customer identifier 選 **Phone number**，Contact payload key 設 **`$.phone`**。關閉 New customer enrollment，使用既有測試聯絡人。
5. Variable 分頁貼入下方 sample JSON，點 Parse data。sample 用來建立欄位，不會自己呼叫後端或寄出訊息。
6. 加上實際測試電話白名單條件；確認只有你的測試電話可繼續。
7. 動作新增 **Send message**，選真正的測試 WhatsApp 渠道。訊息內容選來自 Webhook 的 **reply** 變數。設定有效的 WhatsApp 客服窗口條件；窗口不適用時交人工核對，不用自由文字強行發送。
8. Flow B 不要再 POST `/webhooks/sleekflow`。Flow A 僅觸發客人入訊，不觸發公司出訊，避免迴圈。
9. Publish Flow B。

Sample（僅示範結構，請換成你的測試聯絡人）：

```json
{
  "event": "inspection.reply.ready",
  "event_id": "supermama-reply-example",
  "message_id": "example-inbound-message-id",
  "contact_id": "85290000001",
  "phone": "85290000001",
  "channel": "sleekflow-whatsapp-test",
  "reply": "你好呀！暫時可考慮以下時段，確認後再落實預約。",
  "received_at": "2026-11-04T17:20:48+08:00",
  "processed_at": "2026-11-04T17:20:51+08:00",
  "time_source": "server_received"
}
```

`event_id` 對每個原始 message_id 固定，亦放在 `Idempotency-Key` HTTP Header。它方便接收端去重，但**不宣稱 SleekFlow 自動支援該 Header 或跨服務 exactly-once**。同一訊息只嘗試提交一次；提交後若結果不明，不會自動再發。

## .env 配置

```dotenv
# Flow A 的入訊設定
WEBHOOK_SECRET='你的入訊密鑰'
TEST_PHONE_ALLOWLIST=852你的測試電話
EMBEDDED_WORKER=false

# 舊的直接 Send API 路徑保持關閉，避免兩條發送路徑同時啟用
LIVE_SEND_ENABLED=false

# Flow B 的回覆入口（以下 URL 和 Header 名稱都要用生成範例中的真正值）
SLEEKFLOW_REPLY_WEBHOOK_URL='https://替換成SleekFlow生成的Webhook網址'
SLEEKFLOW_REPLY_WEBHOOK_HEADERS='{"COPY_TRIGGER_HEADER_NAME":"COPY_TRIGGER_ID","COPY_TOKEN_HEADER_NAME":"COPY_WEBHOOK_TOKEN"}'

# 確認 Flow B 及測試白名單配置後才啟用。
# 注意：即使 LIVE_SEND_ENABLED=false，這個開關仍可觸發 Flow B 發 WhatsApp。
SLEEKFLOW_REPLY_WEBHOOK_ENABLED=true
```

**回傳 Webhook 與直接 Send API 為兩條互斥路徑**。若兩者同時啟用，任務在提交前失敗，不會兩邊都發。工作台的 `/api/test/messages` 任務永遠只模擬，即使回傳開關為 true；必須透過 Flow A 真正入訊測試。

URL 及 Headers 只由伺服器管理員配置，不從來訊指定；客人不能選擇任意回覆網址。回傳 HTTP 使用 JSON 序列化，可保留回覆的換行／引號；不傳送 Google 日曆繁忙明細、密鑰或完整執行日誌。禁止跟隨回傳端的 HTTP redirect，避免把 Token 傳到另一個網址。

## 更新、啟動與驗收

API 終端先 Ctrl+C，再執行（若8000被舊API佔用，先正常停止舊程序，不另開新端口）：

```bash
cd /workspaces/Lemma-automation_agent
git pull --ff-only origin main
set -a
source .env
set +a
.venv/bin/python -m backend.app serve
```

Worker 終端也先 Ctrl+C，再執行（**不要在 source 後再 export 關閉剛設定的回傳開關**）：

```bash
cd /workspaces/Lemma-automation_agent
set -a
source .env
set +a
.venv/bin/python -m backend.app worker
```

兩個終端必須使用相同 DATABASE_PATH 和相同測試白名單。SQLite 只做新增欄位遷移，保留舊任務與資料；新版本不會重送舊的模擬完成任務。請發新訊息測試。

驗收順序：

1. 回傳開關 false 時，從測試電話發多行訊息，確認 Flow A HTTP 200、工作台建立新任務、日期基準為「後端接收時間」。
2. 配好並啟用 Flow B 和回傳開關，重啟 Worker，重新發新訊息。
3. 工作台應顯示「回覆 Webhook 已接受」，Flow B Logs 應出現對應聯絡人及 reply。
4. 核對 WhatsApp 實際收到一次；HTTP 2xx 只代表 SleekFlow 接受，並不證明 Flow B 的 Send message 成功或 WhatsApp 送達。
5. 重複提交同一 message_id 不新增任務、不再回傳。人工接管後不提交回覆。
6. 回傳配置錯誤在送出前標為 failed，修正後可人工重試。任何已開始 HTTP 提交的錯誤、逾時或中斷，標為 needs_review；先在 SleekFlow 核對，不能直接重試。

要停止自動回覆，將 `SLEEKFLOW_REPLY_WEBHOOK_ENABLED=false` 並重啟 Worker，同時停用 Flow B。API 及 Worker 執行依賴 Codespace 保持啟動，本次沒有部署 AWS。

## 官方參考

- [Webhook payload received 觸發器、聯絡人匹配及 Variable 設定](https://sleekflow.helpjuice.com/en_US/configuring-webhook-trigger-nodes-afb)
- [Send HTTP request：raw／x-www-form-urlencoded Body 選項](https://sleekflow.helpjuice.com/tindakan-advanced-flow-builder/send-http-request-action-node)

本功能的自動測試以替身驗證 HTTP、去重、錯誤及序列化；尚未取得或呼叫你的 Flow B 真實 URL，也未代你發出 WhatsApp。
