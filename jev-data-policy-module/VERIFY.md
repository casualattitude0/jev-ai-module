# jev-data-policy 驗證結果

29 筆自擬的真實使用者輸入，每筆先標註預期等級，再以真實 OpenRouter 後端（`~typesafe/jev-latest` → `typesafe/jev-1.13-20260917`）逐筆分類。
重跑指令：`python3 -m jev_data_policy.verify --live`；括號內為模型回傳的 confidence。

| Input | 預期 | 實際 | 判定 |
|---|---|---|---|
| `Production key AKIA4NPRODEXAMPLE7QZ with secret wJalrXUtnFEM…` | `regulated` | `regulated` (0.99) | PASS |
| `Patient A123456789, born 1978-04-02, diagnosed with type 2 d…` | `regulated` | `regulated` (1.00) | PASS |
| 「客戶資料：陳怡君，身分證字號 F221304567，健保卡號 000123456789，投保單位為台北市某貿易公司。」 | `regulated` | `regulated` (1.00) | PASS |
| `Stripe live secret sk_live_51H8xPq…[截斷] is set …` | `regulated` | `regulated` (0.99) | PASS |
| 「三年二班學生名冊：王小明 2016-03-11 住新北市板橋區文化路一段 45 號，李小華 2016-07-02 住同區…」 | `regulated` | `regulated` (0.64) | PASS |
| `User wang.mei@example.com, phone 0912-345-678, lives at 3F N…` | `confidential` | `confidential` (0.99) | PASS |
| `Debug dump from the staging box: session cookie sid=8f3c1ad9…` | `confidential` | `confidential` (1.00) | PASS |
| `-----BEGIN RSA PRIVATE KEY-----` + base64 私鑰內容 | `confidential` | `confidential` (0.96) | PASS |
| `DATABASE_URL=postgres://svc_reports:Tr0ub4dor&3@db-internal:…` | `confidential` | `confidential` (0.93) | PASS |
| 「員工通訊錄：林建宏（工號 A0412，分機 2317，jh.lin@ourcorp.com.tw）、張雅婷（工號 A05…」 | `confidential` | `confidential` (1.00) | PASS |
| `How do I vertically centre a div with flexbox? I keep gettin…` | `public` | `public` (1.00) | PASS |
| `Our open-source parser supports UTF-8 and ships under the MI…` | `public` | `public` (1.00) | PASS |
| 「今天台北下了一整天的雨，晚餐想吃點熱的，你有推薦的店嗎？」 | `public` | `public` (1.00) | PASS |
| `Rate limits: 60 requests per minute per key, bursting to 120…` | `public` | `public` (0.94) | PASS |
| `Q3 roadmap, not announced yet: we cut the co-op mode and mov…` | `internal` | `internal` (1.00) | PASS |
| `The ingest tier runs three stateless workers behind an inter…` | `internal` | `internal` (0.98) | PASS |
| `def merge_windows(spans): spans.sort() …` 一段合併區間的程式碼 | `internal` | `public` (1.00) | **FAIL** |
| `Set the header as 'Authorization: Bearer sk-xxxxxxxxxxxxxxxx…` | `public` | `public` (0.99) | PASS |
| `In tests/conftest.py we hardcode PASSWORD = "hunter2" for th…` | `public` | `public` (0.26) | PASS |
| `For billing questions write to support@example.com, or see t…` | `public` | `public` (0.90) | PASS |
| `Of 1,240 surveyed users, 62% chose dark mode and 19% never c…` | `internal` | `internal` (0.86) | PASS |
| `[build] …` 10 行建置日誌，中段夾一行 `AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/…` | `confidential` | `confidential` (0.46) | PASS |
| 「本週客服週報…平均首次回覆 11 分鐘…該客戶身分證字號 F221304567…」（中段） | `regulated` | `regulated` (0.99) | PASS |
| 「我們應該把 API 金鑰的輪替週期從 90 天縮短到 30 天，並且要求所有服務改用短期憑證。」 | `internal` | `internal` (0.50) | PASS |
| `Export sample (redacted): user_id 8f3c***, email j***@***.co…` | `internal` | `confidential` (0.16) | **FAIL** |
| `The finance minister said at Tuesday's press conference that…` | `public` | `public` (0.98) | PASS |
| `Nginx log line from the lab VLAN: 192.168.1.42 - - [12/Mar/2…` | `internal` | `internal` (0.98) | PASS |
| `The SDK crashes when the key is malformed, e.g. I passed the…` | `public` | `public` (0.97) | PASS |
| `Cohort table, k-anonymised at k=25: age band 30-39, region N…` | `internal` | `internal` (0.98) | PASS |

準確率 27/29（93%）；**false negative（該擋沒擋）0 筆**，false positive 1 筆（已遮蔽紀錄，confidence 0.16，偏安全方向）。
另一筆 FAIL 是那段無祕密的程式碼落在 `public`：內容本身沒有任何跡象顯示它是私有的，屬預期標註過嚴。
同一組輸入連跑兩輪，29/29 等級完全一致，confidence 平均漂移 0.010（最大 0.06）；`verify.py` 離線 32 項 + live 6 筆共 38/38 通過。
