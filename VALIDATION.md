# 匯入驗證紀錄 — 2026-11-02

## SleekFlow原始時間兼容 — 2026-11-04

- 125項pytest通過。新增Unix秒數字串、UTC ISO及時區偏移ISO接收測試，核對原訊息時間不被改成接收時間。
- 拒絕缺少時區、日/月顯示格式、毫秒、布林、空值、無效值及未來時間；過期ISO訊息仍被24小時窗口阻擋。
- 本機HTTP測試，未在SleekFlow帳戶執行真實端到端測試；需核對變數展開後的實際值。

## 執行日誌及管理員郵件 — 2026-11-04

- 本機隔離Python3.13環境從原requirements-lock.txt安裝依賴，114項pytest通過；保留原Starlette TestClient棄用警告。pip依賴、JavaScript語法及diff檢查通過。本次未改鎖定依賴。
- 覆蓋完整輸入／意圖／查詢／Google忙碌／逐場排除／回覆日誌、中文全天忙碌提醒、Admin認證、去重不重寄、重試獨立留檔、預覽不寄信、人工接管留檔、錯誤／中斷階段檢查點、檔案匯出失敗仍保留SQLite資料。
- SMTP使用測試替身驗證STARTTLS、寄收件欄位、JSON附件、設定／認證錯誤安全重試、結果不明不重寄、雙Worker原子領取，以及寄信失敗不重做客人任務。未連線真實SMTP或發送郵件。
- 本機瀏覽器實測新「執行日誌」入口、中文報告及逐日篩選摘要。示例使用忙碌區間測試替身，沒有真實Google或DeepSeek呼叫。
- 尚需擁有者在Codespaces配置SMTP應用程式密碼、啟用通知並驗收收件。本次未在Codespaces重驗，也未部署AWS或啟用WhatsApp發送。

## 自動識別及成交日規劃 — 2026-11-03

- 95項pytest通過；JavaScript語法、pip依賴及diff格式檢查通過。新增意圖提取、逐字來源核對、相對日期、日期矛盾、跨聯絡人隔離、24小時上下文、新情境重置、人工覆寫及事件去重測試。
- 星期一至六排除香港公眾假期已按擁有者確認配置；假期日期由政府1823官方JSON取得，涵蓋2025–2027。成交翌日起算為顯示給客人的估算口徑，仍須律師樓／管理處確認。
- 模擬Google忙碌回應，驗證成交日查空位後倒推領匙、星期日不領匙、09:00驗樓不假設08:00可以領匙、假期及年份邊界、無空位不捏造、測試入口不真實發送。DeepSeek回應使用替身，核對憑空日期會被拒絕。
- 本機瀏覽器＋Python API實測：自然語言自動填入11/5及11:00、人工修改啟用覆寫、缺少領匙時間時Worker追問、成交11/5自動規劃查詢起日11/10。只用虛構資料，無Google或DeepSeek真實憑證。
- 未在Codespaces重驗本更新，未執行本更新的真實Google／DeepSeek聯合端到端測試；保留mock預設與WhatsApp發送關閉。

## 客人回覆調整 — 2026-11-03

- 54項pytest通過、JavaScript語法及diff格式檢查通過。新增回覆測試：最多三個已查證候選、內部提醒分離、無空位不捏造、只有成交日先追問領匙。
- 保留完整calendar_result及人工核對提示。未呼叫真實Google或DeepSeek、未發送WhatsApp。

## Google Calendar 增量驗證 — 2026-11-03

- 本機 Python 3.12：51項pytest通過；依賴檢查、JavaScript語法及git diff格式檢查通過。保留原有Starlette TestClient棄用警告。
- 新增測試涵蓋Google FreeBusy請求格式、部分／錯誤回應拒絕、憑證不外洩、管理API認證、時區、候選四場、領匙後一小時、週末優先、平日晚場選項、忙碌／用餐／交通排除、成交日待確認、舊資料庫遷移及雙程序啟動。
- 驗證Worker保存日曆結果、相同事件不同條件拒絕、DeepSeek不能篡改時段，以及真實發送開關開啟時測試入口仍不發送。
- 本機瀏覽器連接真實Python API，檢查新增查詢表單、缺少Google憑證的安全錯誤、只有成交日期時要求確認領匙。沒有使用真實Google憑證。
- Google API及模型回應使用測試替身。尚未完成使用者日曆真實授權、真實Google端到端驗收或本次更新的Codespaces重驗；需按[設定指南](docs/GOOGLE_CALENDAR.md)完成。
- 保留mock預設、獨立Worker及WhatsApp發送關閉；沒有Google事件寫入或AWS部署。

以下為原匯入版本的歷史驗證紀錄。

來源附件 SHA256：`fd2aa6d69f9e7ed8823494aad94ef114dff58c898e78b51cebf39db40bba90e5`。

程式碼驗證版本：`4648542f7a2cc9e3f93ba667153047da20e4648a`。後續僅補充本驗證紀錄及 README 連結。

## 實際執行

| 項目 | 本機 macOS arm64 | GitHub Codespaces Linux |
|---|---|---|
| Python | 3.12.16 | 3.12.11 |
| 鎖定依賴安裝 | 通過，未更改原 lock | 官方 Python 3.12 devcontainer 建置成功，建立 `.venv` |
| `.venv/bin/python -m pytest -q` | 17 passed, 1 warning（2.10s） | 17 passed, 1 warning（3.30s） |
| `.venv/bin/python -m pip check` | No broken requirements found | No broken requirements found |
| `node --check dist/app.js` | 通過 | 通過 |
| `git rev-parse HEAD` | 上述驗證版本 | 與上述驗證版本一致 |

警告：Starlette TestClient 使用 httpx 的方式已棄用，建議 httpx2；目前測試仍通過。為保留移交環境，未升級鎖定套件。

已實際建立 Codespace `fantastic-capybara-jj7pv5g49jvphvxj`（2-core，Southeast Asia）。Ports 面板確認 8000 為 **Private**。未啟用 Public webhook，未在 Codespaces 啟動長駐 API／Worker，日常啟動前仍須由擁有者設定 `ADMIN_TOKEN`、`WEBHOOK_SECRET` Codespaces Secrets。

## 本機瀏覽器與真實 Python 後端驗收

API 與獨立 Worker 使用同一個暫存 SQLite 資料庫，固定 mock 模型及關閉真實發送，測試資料未提交 Git。

- 工作台由 FastAPI 提供，同源連接 `/api/config` 成功；顯示 mock、獨立 Worker、真實發送關閉。
- 從 UI 提交訊息，獨立 Worker 完成並保存 `simulated` 結果。
- 從 UI 重送相同事件，畫面提示已攔截，任務總數維持不變。
- 從 UI 注入一次故障，任務失敗；人工重試後 `simulated`，attempts=2。
- 從 UI 人工接管後，新任務進入 `handoff`。
- HTTP `/health` 成功；管理 API 無 Token／錯誤 Token 回傳 401。
- 停止 API 與 Worker，再以全新 SQLite 連線核對：兩筆 `simulated`、一筆 `handoff` 仍存在。
- pytest 另外涵蓋租約恢復、發送結果不明禁止重送、24 小時窗口、遮蓋、白名單、獨立 Worker 子程序持久化及 test 入口不發送。

## 提交內容檢查及範圍

只匯入附件程式碼及必要設定；沒有複製同層舊有專案、`.env`、Sites 設定或 `.git`。提交前核對檔案白名單、常見憑證格式及忽略規則。未提交真實秘密、客戶資料、SQLite 或虛擬環境。

`AI_PROVIDER=mock`、`LIVE_SEND_ENABLED=false`、`EMBEDDED_WORKER=false` 保持開發預設。沒有部署 AWS，沒有真實 WhatsApp 發送。

DeepSeek／SleekFlow／Google 真實端到端測試未執行。Google 授權、真實日曆／預約、SleekFlow 原生欄位映射與發送格式核實、送達 webhook 仍屬下一階段；Docker Compose 亦未實測。
