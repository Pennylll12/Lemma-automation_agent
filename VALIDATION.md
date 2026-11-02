# 匯入驗證紀錄 — 2026-11-02

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
