# 執行日誌與管理員郵件

## 保存與查看

新版每次Worker處理任務及人工重試都建立獨立執行編號，不覆寫上次結果。人工接管而跳過的任務亦留報告。識別預覽、直接日曆預覽會保存日誌，但不寄任務通知。事件去重不新增報告或重寄。

主記錄保存在原SQLite資料庫的`audit_runs`表；每個主要階段即時更新，即使Worker中斷，已完成步驟仍可核對。正常結束另輸出同名三種檔案：

- `data/audit/<執行編號>.txt`：中文報告。
- `data/audit/<執行編號>.json`：結構化完整資料。
- `data/audit/<執行編號>.eml`：已配置寄收件地址時產生的郵件預覽，並非已寄送證明。

實際預設路徑是`DATABASE_PATH`所在資料夾內的`audit/`，Docker使用`/app/data/audit`，與SQLite共用持久volume。可用`AUDIT_LOG_DIR`修改。`data/`已被Git忽略，不提交日誌、資料庫、私鑰或`.env`。

在工作台左側點「執行日誌」：可讀中文報告、下載TXT／JSON／郵件預覽，並看每次通知狀態。任務詳情亦有各次報告入口。列表目前為最近100次，資料庫及檔案仍保留更早記錄；尚未實作自動清理，需按你的資料保留要求另設。

## 內容是甚麼

記錄可核對的執行事實，而非模型隱藏推理：

1. 基本遮蓋後的客人訊息、任務來源、是否人工覆寫／重置上下文。聯絡人用雜湊參考值，避免在郵件列出電話。
2. 傳給DeepSeek的提示詞及遮蓋後訊息、模型名稱，以及通過驗證的原文片段；提取失敗只記錄安全備援提示，不存未驗證模型內容或原始供應商錯誤。
3. 日期基準、可沿用的同客人任務ID、最終意圖、日期、追問與限制。
4. 成交日工作天假設、估算領信／領匙範圍。
5. 實際Google查詢日曆ID、範圍、HTTP狀態，以及返回Python的忙碌起止區間（已正規化香港時間）。不存事件標題、地址或參加者。
6. 每一個候選場次的通過／排除原因，例如時段偏好、平日晚場未啟用、不足領匙一小時、Google忙碌／交通衝突、飯點衝突、領匙窗口不符。
7. 完整最終回覆、任務結果、安全錯誤及通知結果。

中文報告頂部列各日通過／排除數，並突出一天以上的連續忙碌，方便發現「無關驗樓的全天活動」造成誤擋。這只是提醒，不能自動判斷活動是否無關；目前FreeBusy沒有活動類型可供分類。

Google查詢失敗會留下查詢條件和錯誤，沒有忙碌清單就不會虛構回傳。舊版本已完成任務不能補回當時未保存的Google資料；需發送新測試。

## SMTP配置

寄件人和收件人按管理員指定，在Codespaces現有`.env`填入，保留原有Google／DeepSeek／ADMIN_TOKEN設定：

```dotenv
AUDIT_EMAIL_ENABLED=true
ADMIN_EMAIL_TO=管理員收件電郵
SMTP_FROM=寄件電郵
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USER=寄件帳戶完整電郵
SMTP_PASSWORD='填入該帳戶的Google應用程式密碼'
```

不要把尖括號、中文提示或範例字樣當真實值；密碼只放`.env`或Codespaces Secrets，不貼聊天。不要使用日常Google登入密碼或日曆Service Account JSON。

Google Workspace帳戶若允許應用程式密碼，先登入寄件帳戶並完成兩步驟驗證，再到[應用程式密碼](https://myaccount.google.com/apppasswords)建立專用密碼，將Google顯示的16字元填入`SMTP_PASSWORD`（去掉顯示用空格）。若沒有此選項，可能受帳戶／管理員政策限制；請由管理員確認可用的SMTP或OAuth方案，不降低帳戶安全設定。此版實作SMTP TLS＋密碼認證，尚未提供Gmail OAuth登入。

Google官方說明：[應用程式密碼](https://support.google.com/mail/answer/185833)、[Gmail SMTP連線](https://developers.google.com/workspace/gmail/imap/imap-smtp)。程式支援587 STARTTLS或465 SSL，驗證TLS憑證。

配置後API和Worker均需重啟並載入同一`.env`。在「執行日誌」點「寄送設定測試郵件」，Worker會寄不含客人資料的測試報告；確認收件匣後，再發一筆新的驗樓測試任務。

每次任務／重試各寄一封，不是每一個階段寄一封。寄信由Worker另行處理，不依賴`LIVE_SEND_ENABLED`，所以可保持WhatsApp真實發送關閉。

## 通知可靠性

| 狀態 | 意義與處理 |
|---|---|
| 未啟用 | 日誌已存，未排隊寄信；以後啟用不自動補寄這些舊記錄 |
| 待寄 | 等待Worker領取 |
| 寄送中 | 已被領取；若Worker中斷，先查收件匣，不自動重新領取 |
| 伺服器已接受 | SMTP已接受，不保證收件匣送達；亦需查看垃圾郵件 |
| 待配置 | 在提交郵件前設定／連線／認證失敗；修正後點「重試通知」 |
| 結果未確認 | 提交郵件途中失敗，可能已寄出；先核對收件匣，禁止直接重試 |

通知失敗不會把已完成任務改為失敗，不會重做日曆查詢或重發WhatsApp。兩個Worker會用SQLite原子領取同一封通知，避免重複寄送。SMTP無法保證端到端exactly-once；結果不明時保守交人工核對。

獨立Worker或內嵌Worker需要保持運行，Codespace休眠時不會寄信。SMTP密碼從環境讀取，不進日誌、資料庫報告或前端設定摘要。

## API

全部需要Admin Bearer，不把Token放URL：

- `GET /api/audit/runs`：最近100次摘要。
- `GET /api/audit/runs/{id}?format=text|json|eml`：中文／JSON／郵件預覽。
- `POST /api/audit/runs/{id}/retry-email`：僅重試明確未提交的配置錯誤通知。
- `POST /api/audit/test-email`：明確要求寄一封設定測試郵件，需先啟用通知，等待Worker執行。

任務API回傳`audit_runs`，包含每次執行編號及郵件狀態。通知信含任務ID、執行編號、中文報告及JSON附件，可回工作台核對。
