# Google Calendar 唯讀查詢與工作台設定

這一版以 `lemma@supermama.homes` 為指定日曆。先讓工作台查到真實忙碌時間與候選場次；不新增、修改、取消或保留預約，不啟用 WhatsApp 真實發送，也不需要把日曆設為公開。

## 1 在 Google 建立唯讀連接

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 建立或選擇專案，在 APIs & Services 啟用 **Google Calendar API**。
2. 在 IAM & Admin → Service Accounts 建立專用服務帳戶，例如 `supermama-calendar-reader`。它不需要專案 Editor／Owner 權限，也不需要全網域委派。
3. 進入服務帳戶 → Keys → Add key → Create new key → JSON，下載官方 JSON。請由帳戶擁有者完成，不把私鑰貼到聊天、前端或 Git。若組織政策禁止建立金鑰，請先聯絡管理員，這版尚未提供使用者 OAuth 登入流程。
4. 複製服務帳戶的電郵（以 `iam.gserviceaccount.com` 結尾）。
5. 用可管理驗樓日曆的 Google 帳戶登入 Calendar，指定日曆 → 設定及共用 → 與特定使用者或群組共用，加入該服務帳戶電郵，權限選 **僅查看有空／忙碌狀態，隱藏詳細資料**。只分享指定日曆，不改公開權限。
6. 確認 Calendar ID 為 `lemma@supermama.homes`。若 Workspace 政策限制外部分享，需要管理員處理，不能只靠建立 API key 存取私人日曆。

後端只請求 `https://www.googleapis.com/auth/calendar.freebusy` 範圍，呼叫 FreeBusy API，不下載事件標題、地址、描述或參加者。

官方參考：[建立服務帳戶金鑰](https://docs.cloud.google.com/iam/docs/keys-create-delete)、[共用日曆](https://support.google.com/calendar/answer/37082)、[FreeBusy API](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query)。

## 2 把憑證放入 Codespaces

建議用檔案方式，避免在終端貼私鑰：

1. Codespaces 左側檔案區，在專案根目錄新增 `secrets` 資料夾。
2. 把剛下載的 JSON 上傳至該資料夾，命名 `google-calendar.json`。
3. 最終路徑為 `secrets/google-calendar.json`。`secrets/` 已被 Git 忽略，切勿使用 `git add -f`。
4. 編輯現有 `.env`，**保留 ADMIN_TOKEN、DeepSeek 等既有設定**，新增或修改以下幾行：

```dotenv
CALENDAR_MODE=google
GOOGLE_CALENDAR_ID=lemma@supermama.homes
GOOGLE_APPLICATION_CREDENTIALS=secrets/google-calendar.json
LIVE_SEND_ENABLED=false
EMBEDDED_WORKER=false
```

憑證檔案不是 DeepSeek key，也不是 ADMIN_TOKEN；不要把完整 JSON 填到工作台 Token 欄位。

另一種方式是 Codespaces Secret `GOOGLE_SERVICE_ACCOUNT_JSON`，值為完整官方 JSON。兩種方式擇一；如果兩者都有值，程式優先使用這個 Secret。Secret 更新後需停止並重新啟動 Codespace。不要把多行 JSON 直接放進 shell 格式 `.env`。

本指南的檔案路徑適用直接在Codespaces執行Python。Docker Compose不會自動把主機的secrets資料夾帶入容器；若日後改用Docker，需另設唯讀憑證掛載並確認非root使用者可讀，或由部署環境注入上述JSON環境變數。本次未驗證Docker日曆連線。

## 3 更新並啟動

先到 API 和 Worker 終端各按 Ctrl+C 停止舊程序。在普通終端、專案根目錄執行：

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements-lock.txt
```

API 終端逐行執行：

```bash
cd /workspaces/Lemma-automation_agent
set -a
source .env
set +a
.venv/bin/python -m backend.app serve
```

Worker 終端逐行執行同一組環境載入，再啟動 Worker：

```bash
cd /workspaces/Lemma-automation_agent
set -a
source .env
set +a
.venv/bin/python -m backend.app worker
```

`serve` 是新增的簡單啟動指令，避免手打 `backend.app:app` 的冒號。程式不自動載入 `.env`；只 source 自己建立的可信檔案，不 source `.env.example` 覆蓋 Secrets。API 和 Worker 都要從同一根目錄、載入同一設定，避免 API 顯示 Google 模式但 Worker 仍在 mock。

新增資料庫欄位採加欄位方式，不刪除舊 jobs、events、outbox。建議更新前備份自己的測試資料庫，但不要提交資料庫到 Git。

## 4 在工作台驗收

1. 保持 8000 Private，打開 Ports 面板提供的工作台網址，重新整理頁面。
2. 用原本 ADMIN_TOKEN 連接後端。日曆能力顯示 Google 唯讀模式只是配置摘要，**不代表已成功授權**。
3. 流程測試 → 展開 **Google 日曆查詢條件**。
4. 選查詢開始日、1／7／14天、預留120／150／180分鐘；填已確認領匙時間（香港時間）。未知面積先用180分鐘是保守查詢值，不是面積與時長的正式對照。
5. 點 **只查日曆候選時段**。這個按鈕直接測試 API 的 Google 連線，不經 Worker、不呼叫 DeepSeek。
6. 成功時顯示「已查Google日曆」、快照時間及候選列表。可以選一個已知忙碌的日期核對該時段沒有出現；沒有符合時段也是有效查詢結果，不代表連線失敗。
7. 要測完整 Worker／DeepSeek 流程，勾選 **把以下條件交給 Worker 查真實日曆**，輸入測試訊息，再點原本的 **發送測試訊息**。
8. 任務詳情顯示 Google 真實查詢快照來源；DeepSeek 可選擇固定回覆語氣，但日期、時段、規則及聲明由程式組裝，模型不能新增時段或宣稱已預約。

日曆查詢條件以明確表單／API欄位為準。這版不從文字自動抽取「下星期二」或沿用舊對話日期；只提供成交日期時會要求先確認領匙，不自動猜工作天。Google模式沒有查詢欄位時會提示補資料，不使用固定測試空位。

## 5 已實作的排程規則及邊界

- 固定候選開始時間為09:00、12:30、15:45、18:00，結束時間由本次120／150／180分鐘計算。
- 開始時間至少為領匙後1小時；等於1小時可以，早於1小時會被排除。
- 領匙當日及週末候選優先，再按時間排序；不強行認定兩者誰必定優先。
- 平日18:00預設不列入，勾選後才作人工評估；週末18:00可作候選，仍不代表已落實。
- 星期日領匙與目前規則有矛盾，先確認；星期日驗樓不會被排除。
- 日曆忙碌、已設定用餐區間、已設定交通緩衝會排除衝突。Google回應不完整、沒有權限或網絡失敗時，整次查詢失敗，不當作空白日曆。
- 只查忙碌區間，無法得知同一日曆是否代表多位驗樓師、已接幾場或事件是否已含交通；所以全部結果標示為 **待人工核實的候選**。
- 多個候選是替代選項，不能把整個列表全部預約。15:45場若需3小時便與18:00場重疊；需人工處理。
- 只有成交日期時，顯示「最快第二個工作天領律師信 → 致電管理處 → 一般兩個工作天後領匙」及10工作天規則，但不自動計算截止日。星期六、假期、起算口徑尚未確認。
- Google Form有效登記查詢及正式預約寫入，仍未開發；回答明確標示尚未核對登記。

### 可選用餐及交通配置

未確認時留空；工作台會明確提示尚未套用，而不是聲稱已滿足休息及交通要求。

```dotenv
CALENDAR_TRAVEL_MINUTES=
CALENDAR_MEAL_BREAKS=
```

確認後，交通填0至180分鐘；程式會在Google忙碌區間前後各留該分鐘數。若Google事件已包含交通，避免重複保留。

`CALENDAR_MEAL_BREAKS` 需兩段每日固定區間，各30至50分鐘。格式例如 `'12:00-12:30,19:00-19:40'`，**只是格式示例，不是已批准安排**。目前不自動搬動飯點；沒有配置時所有候選仍須人工安排午晚飯。設定不合法會拒絕查詢。

## 6 錯誤排查

| 畫面 | 檢查 |
|---|---|
| 未配置Google憑證 | 兩個終端都 source 了正確 .env；JSON 路徑及檔名存在 |
| 憑證載入或授權失敗 | JSON 是官方 Service Account 格式、金鑰有效，伺服器可連Google |
| HTTP 403 或沒有完整日曆資料 | API已啟用，日曆分享給正確服務帳戶，Workspace允許分享 |
| 查詢未完成 | 不會用假空位替代；修好權限或網絡後再查 |
| 工作台可查，但任務仍固定模擬 | Worker仍是舊程序或沒有載入CALENDAR_MODE=google |
| 模型失敗 | 日曆快照可能已保存，但回答任務失敗；檢查DeepSeek，修復後重試 |

程式不輸出Google access token、私鑰或原始供應商錯誤內容。排查時只分享錯誤提示，不分享JSON或`.env`內容。

## 7 API 合約

新增 `POST /api/calendar/availability`，需要 Admin Bearer，回傳結構化候選或補資料提示。成功查詢也不代表預約確認。最多查14天，開始日限制今天起一年內。

```json
{
  "start_date": "2026-11-05",
  "days": 7,
  "duration_minutes": 180,
  "pickup_at": "2026-11-05T11:00:00+08:00",
  "preference": "any",
  "include_weekday_evening": false
}
```

日期只為合約示例，實測需換成今天起的日期。可選 `completion_date`；`pickup_at` 必須有時區。

同一物件可放入 `/api/test/messages` 或標準化 `/webhooks/sleekflow` 的 `schedule` 欄位，由Worker處理。其餘 webhook 驗證、電話白名單及發送關閉規則保留。相同Message ID但不同查詢條件會拒絕，避免重複事件悄悄換日期。
