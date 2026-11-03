# 叻媽 AI Flow Lab — v0.1

可執行的 Python FastAPI + SQLite + 獨立背景 Worker，以及繁體中文工作台。

## Google Calendar 唯讀查詢

已加入真實Google日曆忙碌查詢、工作台日期／領匙／需時表單，以及依規則產生的候選時段。設定及驗收步驟見 [Google Calendar設定指南](docs/GOOGLE_CALENDAR.md)。需要你自行配置Google Service Account及分享指定日曆，尚未在本帳戶完成真實日曆端到端驗收。

## 已完成與限制

- 公開 GPT Sites 頁面提供**瀏覽器模擬**，不執行 Python、呼叫 DeepSeek 或發送 WhatsApp。紀錄存在 localStorage，關頁後不繼續執行。網站預設為擁有者私人存取。
- Python 後端提供持久任務、租約領取、同客人序列化、跨客人多 Worker、去重、基本個資遮蓋、人工接管、24 小時窗口檢查、安全手動重試及操作日誌。
- 可選接入 DeepSeek。預設 `AI_PROVIDER=mock`，日曆預設仍為mock固定測試資料；配置 `CALENDAR_MODE=google` 及憑證後可唯讀查真實日曆，不會建立預約。
- `/api/test/messages` **永不發送 WhatsApp**，即使啟用真實發送。
- Webhook 格式是本專案明確定義的 normalized contract，**不是宣稱 SleekFlow 原生 webhook 的實際結構**。請用 Flow HTTP request 自行映射欄位，或依實際原生 payload 新增轉換器。
- SleekFlow 發送 URL、認證 header 及 body schema 尚未完成官方端點核對。提供可配置 HTTP adapter，只有核實後配置及啟用才可以實測發送。未作任何真實外部 API 端到端測試。
- 僅基本遮蓋常見香港電話、電郵、部分姓名及門牌。這不是完整匿名化或 DLP；請只用虛構資料測試。

本次匯入及本機／Codespaces 實測結果見 [VALIDATION.md](VALIDATION.md)。

## GitHub Codespaces（Python 3.12）

[開啟 Codespaces](https://codespaces.new/Pennylll12/Lemma-automation_agent)，選 `main`，使用本 repository 的 devcontainer。建立後會安裝 `requirements-lock.txt`、執行 pytest 及 JavaScript 語法檢查；不自動啟動 API 或 Worker。Node 24 僅供 JavaScript 檢查，Python 服務本身不依賴 Node。

在 GitHub Settings → Codespaces → Secrets 為本 repository 設定 `ADMIN_TOKEN` 與 `WEBHOOK_SECRET`，兩者使用不同的長隨機值；可選 `TEST_PHONE_ALLOWLIST`（只用本人測試電話）。新增或變更 Secrets 後停止並重新啟動 Codespace，確認新終端可用，勿印出值。不要 source `.env.example` 覆蓋 Secrets；Python 不會自動讀取 `.env`。

此開發容器預設 `AI_PROVIDER=mock`、`LIVE_SEND_ENABLED=false`、`EMBEDDED_WORKER=false`、`DATABASE_PATH=data/flow.db`。基本模擬不需要外部 key；要查真實日曆請按上方 Google Calendar 指南配置。保留真實發送關閉。

兩個終端均在 repository 根目錄執行。終端 A：

```bash
.venv/bin/python -c 'import os; assert os.getenv("ADMIN_TOKEN") and os.getenv("WEBHOOK_SECRET"), "Configure Codespaces Secrets first"'
.venv/bin/python -m backend.app init
.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

終端 B（獨立 Worker）：

```bash
.venv/bin/python -m backend.app worker
```

Ports 面板開啟標籤 `SuperMama Flow Lab` 的 8000 埠。GitHub 新轉發埠預設 Private；確認保持 Private，不要改 Public。設定只轉發 8000，不修改可見性。同源工作台在「連接設定」填瀏覽器目前 origin 與 `ADMIN_TOKEN`，點「測試並連接」。無須 CORS；管理 Token 不是模型 API key。手動驗收包括測試訊息、去重、故障重試、接管及重啟 Worker 後的紀錄保存。

重新驗證：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
node --check dist/app.js
```

Codespace 休眠時 API、Worker、webhook 都會停止，不能當正式客服主機。這次未部署 AWS、未啟用 WhatsApp 真實發送。只有未來明確進行外部 webhook 測試時，才考慮 Public 埠並保留應用認證與電話白名單。參閱 [GitHub 埠轉發說明](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)。

## 快速啟動（Python 3.12+）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt
cp .env.example .env
```

編輯 `.env`：更換 `ADMIN_TOKEN`、`WEBHOOK_SECRET` 為不同的長隨機值，設定測試電話白名單。不要把 key 貼進聊天、前端或提交 Git。

在可信本機 shell 載入 `.env`（這是 shell 格式檔案，只使用你自己寫的內容）：

```bash
set -a
. ./.env
set +a
python -m backend.app init
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

開另一個終端，同樣啟用虛擬環境並載入 `.env`，執行：

```bash
python -m backend.app worker
```

開 `http://127.0.0.1:8000`，於「連接設定」輸入此網址及 `ADMIN_TOKEN`。測試訊息會寫入 SQLite，由獨立 Worker 執行。FastAPI API 文件位於 `/docs`，受保護 API 須提供 `Authorization: Bearer <ADMIN_TOKEN>`。OpenAPI 頁面只揭示 API 結構，不提供資料或秘密。

可選單程序模式：設定 `EMBEDDED_WORKER=true` 後只啟動 API；不要同時再啟動內嵌 Worker 的多個 Web worker。

## Docker Compose

建立 `.env` 後執行 `docker compose up --build -d`。API 及 Worker 為分開容器，使用同一 named volume 保存 SQLite。此指令未在本開發環境實測 Docker，請部署端驗證。

伺服器部署必須在 HTTPS reverse proxy 後運行，保留持久磁碟，設定健康檢查、程序重啟、磁碟監控及備份。SQLite volume 只可放在同一主機的可靠本機磁碟，不適合多主機或臨時檔案系統。大流量／多節點上線再改 PostgreSQL 與專用任務佇列。

若用 GPT 託管頁面連接後端，後端必須可由瀏覽器存取，使用 HTTPS，並將頁面**精確 origin**填入 `CORS_ORIGINS`，逗號分隔多個來源。開發本機 HTTP 服務時請從本機服務打開工作台，避免混合內容或私人網絡限制。Token 只存於該分頁 sessionStorage，但仍應只在可信裝置使用。Sites 私人存取不會代替 Python API 的 token 認證。

## 測試流程

1. 連接後端，發「星期六有冇驗樓位？」；應顯示 `queued → processing → simulated`。
2. 點「重送同一事件」；任務總數不增加。
3. 勾選一次故障；任務應失敗，按重試後完成。
4. 人工接管測試客人；新任務進入 `handoff`。
5. 關閉 API／Worker，再啟動；任務紀錄仍存在。Worker 租約到期前任務可能暫時維持 processing（180 秒租約）。
6. 執行 `python -m pytest -q`。

## 接入 DeepSeek

模型名稱範例與 Python fallback 統一為 `deepseek-flash`（2026-11-02 核對 [DeepSeek 官方文件](https://api-docs.deepseek.com/quick_start/pricing/)）。

設定 `AI_PROVIDER=deepseek`、`DEEPSEEK_API_KEY` 及帳戶實際支援的 `DEEPSEEK_MODEL`，重啟 API／Worker。mock日曆模式傳送已遮蓋訊息及同客人最近四個先前回合，固定時段不代表真實查詢。Google模式則只讓DeepSeek根據當前已遮蓋訊息選擇固定語氣；日期及時段由後端查詢日曆後組裝。

只有在確認供應商適用 API 條款、資料保存及處理安排後才可考慮傳真實客戶資料。本版不處理附件、付款或銀行流水。

## 接入 SleekFlow（先保持 LIVE_SEND_ENABLED=false）

於 Flow Builder 使用 Incoming message received，限定測試電話，再用 Send HTTP request：

- Method: POST
- URL: `https://your-backend.example.com/webhooks/sleekflow`
- Header: `X-Webhook-Secret` 為 `.env` 裏的 `WEBHOOK_SECRET`
- Content-Type: application/json
- Body: 從實際事件動態選擇對應值，映射到以下 schema。不要把範例 message_id 當常數。

```json
{
  "message_id": "unique-original-message-id",
  "contact_id": "85290000001",
  "message": "星期六有冇驗樓位？",
  "channel": "original-channel-reference",
  "timestamp": 1793606400,
  "direction": "inbound"
}
```

`timestamp` 必須為客人該則訊息的 Unix 秒數，不能用流程執行時間或毫秒。`contact_id` 在這條 webhook contract 指客人電話，會標準化為純數字及核對白名單。原生 webhook 若欄位不同，需要依實際 payload 寫 mapping；本端點不直接假設相容。範例時間為固定示例，測試時請換成真正訊息時間。

先保持 `LIVE_SEND_ENABLED=false` 測通。確認後填入 Send Message 官方文件核實的：

- `SLEEKFLOW_SEND_URL`：HTTPS endpoint。
- `SLEEKFLOW_AUTH_HEADER`／`SLEEKFLOW_AUTH_VALUE`：完整認證格式。
- `SLEEKFLOW_BODY_TEMPLATE`：單行 JSON，正確欄位內使用 `{{recipient}}`、`{{channel}}`、`{{reply}}`。channel placeholder 是 normalized contract 傳入值，不自動把 channel ID 轉成電話或其他類型。

本版不提供猜測出的 SleekFlow body 範本。使用 `Send HTTP request` 把訊息交後端後，**不要再另設一個 Send message 發相同答案**。後端採非同步發送；HTTP 接收成功不會自動發 WhatsApp。

最後才設定 `LIVE_SEND_ENABLED=true` 並重啟，白名單仍然生效。SleekFlow HTTP 成功僅標記 `accepted`，不是已送達；需要送達 webhook 才可進一步核實。24 小時窗口已關閉會標記 `window_closed`，本版未自動選範本。

## 任務可靠性與已知邊界

- Message ID 全域唯一，重送不會延長窗口；相同 ID 配不同內容會拒絕。獨立 SQLite transaction 原子領取、租約及 claim token 防止失效 Worker 改寫已被重新領取任務。
- 發送前寫入 outbox。發送結果不明或 Worker 在發送中中斷，標記 `needs_review`，禁止直接重試。不可保證跨外部服務 exactly-once；請先在 SleekFlow 核對。
- 人工接管能停止排隊任務，處理中任務發送前再檢查；已進入外部 HTTP 發送中的訊息不能撤回。
- 本版沒有 webhook 簽章協議假設；使用 HTTPS＋共享秘密 header，實際簽章能力待核實。公開網關須增加請求大小／頻率限制、日誌遮蓋及存取監控。
- API raw request body 上限於程式讀取後檢查；反向代理亦須設定實際傳輸大小限制（例如 64 KB）。
- 未包含短訊合併、Google Form登記查詢、預約寫入、付款、每週額度、附件、WhatsApp 模板、自動資料刪除及客服帳戶權限分級。請勿當作正式生產系統。
- 工作台可查看已載入的最近 100 筆任務，匯出亦只包括目前載入資料。原訊息於寫入 jobs 前做基本遮蓋，聯絡電話仍在後端受權限資料庫內；備份及資料保留期限需部署時另設。

## API

| Method | Path | Auth | 用途 |
|---|---|---|---|
| GET | /health | 無 | 健康檢查 |
| GET | /api/config | Admin Bearer | 安全設定摘要，不傳回 secrets |
| GET | /api/jobs | Admin Bearer | 最近100筆任務及日誌 |
| POST | /api/calendar/availability | Admin Bearer | 唯讀查Google日曆候選，不建立預約 |
| POST | /api/test/messages | Admin Bearer | 只測試、不發外部訊息 |
| POST | /api/jobs/{id}/retry | Admin Bearer | 只重試 failed 且未開始發送任務 |
| POST | /api/contacts/{id}/takeover | Admin Bearer | {"human":true/false} |
| POST | /webhooks/sleekflow | X-Webhook-Secret | normalized 入訊、白名單限制 |

專案內不包含任何真實 API key 或客戶資料。
