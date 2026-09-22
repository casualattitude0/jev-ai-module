# Results

內容進、等級出。最後更新 2026-09-22，4 個等級，32 項離線檢查 + 6 筆實際分類，
全綠。下面每一段都是實際跑出來的輸出。

這個模組在這次重構前做的是另一件事：維護一份「誰獲准接收哪一級資料」的核准
登錄簿。那是法務與商務的決定，不該住在讀文字的那顆東西裡。現在它只做一件事：
看內容，說出等級。

## 實際分類 — 6 筆，全中

    $ python3 -m jev_data_policy.verify --live

    live classification (6 samples, backend openrouter from jev.json modules.jev_data_policy.backend)
      PASS  public        Our open-source parser supports UTF-8 and sh...  (public at 1.00)
      PASS  internal      Q3 roadmap, not announced yet: we cut the co...  (internal at 1.00)
      PASS  confidential  User wang.mei@example.com, phone 0912-345-67...  (confidential at 0.98)
      PASS  confidential  Debug dump from the staging box: session coo...  (confidential at 1.00)
      PASS  regulated     Production key AKIA4NPRODEXAMPLE7QZ with sec...  (regulated at 0.99)
      PASS  regulated     Patient A123456789, born 1978-04-02, diagnos...  (regulated at 1.00)

    38 passed, 0 failed

六筆刻意包含兩組相鄰的難題：**個資 vs 憑證**（都在 `confidential`），以及
**憑證的兩級**（staging 的 session cookie 是 `confidential`，正式環境、可動
帳的長效金鑰是 `regulated`）。

第二組是被 Jev 糾正出來的，不是設計時就想到的。原本的 fixture 把
`AKIA...  full write access` 寫成 `confidential`，Jev 回 `regulated` (0.98)。
回去讀 `regulated` 的描述——「a credential that moves money or reaches
production」——錯的是期望值，不是模型。fixture 改掉了，另外補一筆真正只到
`confidential` 的非正式環境憑證。

六筆不是準確率。它證明的是這六組相鄰概念分得開，不是這個模組在你的資料上有
多準。

## 一筆實際輸出

    $ python3 -m jev_data_policy "幫我看一下這段 log：user 王小美 (wang.mei@example.com) 登入失敗 3 次，IP 203.0.113.44"

    backend:    openrouter  (from jev.json modules.jev_data_policy.backend)
    content:    102 bytes
    class:      confidential
    confidence: 1.00
                confidential=1.00  internal=0.00  public=0.00  regulated=0.00

每次都印出這次走哪一條、這個決定從哪裡來。

## 不確定時往上，不往下

`SELECT * FROM users WHERE id = ?` 是一個真的模稜兩可的輸入——它長得像個資
查詢，但裡面一筆個資也沒有。Jev 給 `public`，但信心只有 0.73–0.75：

    $ python3 -m jev_data_policy --json "SELECT * FROM users WHERE id = ?"

    {
      "data_class": "public",
      "confidence": 0.75,
      "probabilities": { "public": 0.81, "internal": 0.14,
                         "confidential": 0.04, "regulated": 0 },
      "escalated_from": null,
      "content_bytes": 32
    }

同一句話，要求信心 0.9：

    $ python3 -m jev_data_policy --min-confidence 0.9 "SELECT * FROM users WHERE id = ?"

    class:      confidential
    confidence: 0.73
                escalated from 'public': below --min-confidence 0.9
                public=0.79  internal=0.16  confidential=0.05  regulated=0.00

往上取到「還有機率的最敏感等級」，而 Jev 原本說的那個答案留在
`escalated_from`，沒有被蓋掉。離線檢查另外釘住兩件事：升級**只會往上**
（低信心的 `regulated` 不會被降成 `public`），以及機率全空時直接取最頂——
沒有任何證據，是最不該挑梯子底端的時刻。

預設 `min_confidence=0.0`，也就是原封不動報 Jev 說的。

## 超長內容是拒絕，不是截斷

    $ python3 -c "print('a'*17000)" | python3 -m jev_data_policy

    FAIL: content is 17001 bytes; this module classifies up to 16384. Split it
    and classify each part -- a truncated payload is how a regulated one comes
    back public
    (exit 1)

截斷會把一份 regulated 的內容變成 public，所以寧可拒絕。離線檢查確認它在
**發出請求之前**就擋下來（`stub.seen` 是空的）。

## 失敗一律往安全的方向倒

離線層把 transport 換成 stub，所以這些是實測而不是推論：

| 情況 | 行為 | 檢查項 |
|---|---|---|
| Jev 掛掉 (503) | 拋 `JevError` | `a failed call fails closed` |
| 回一個梯子上沒有的等級 | 拋 `PolicyError` | `an answer outside the ladder is refused` |
| 回空答案 | 拋 `PolicyError` | `an empty answer is refused` |
| 內容是空白 | 拋 `PolicyError` | `empty content is refused` |

沒有任何一條路會在出事時回 `public`。

## 這個模組只做一件事 —— 有測試釘住

兩項離線檢查專門防止舊職責長回來：

- `the module only classifies` — 原始碼裡不得出現 `approved_targets`、
  `assert_target`、`assert_service`、`guarded_call`、`require_targets`、
  `approved_classes`、`external_class_floor`。
- `classes describe content, not recipients` — `classes.json` 的任何一條
  描述都不得出現 approved / vendor / target / cleared / model / allow-list。
  等級是「內容裡有什麼」，不是「誰能收」。

加上 `the module stays standalone`：`classify.py` 不得提到
`jev_model_router`、`jev_workflow`、`anthropic`、`openai`。

## 金鑰只到得了 Jev

`the OpenRouter key can only reach Jev`：每次呼叫前檢查 model slug，
`openai/gpt-5.6-sol` 與 `anthropic/claude-opus-5` 都被拒絕。這件事在這個模組
特別重要——送去分類的那段文字，正是有人懷疑它帶著個資的那段文字。

## 沒有驗過的部分

- **native backend 沒有跑過。** `jev.json` 設的是 `openrouter`，這次全部實測
  都走 OpenRouter。試打 native 時拿到 `HTTP 429 Too many requests`（重試三次
  後仍然），那是共用金鑰的限流，不是程式的問題——但結論是：native 這條路徑在
  這次重構後**沒有被實際執行過**。
- **準確率沒有量測。** 6 筆樣本是概念分界的證明，不是 benchmark。真正要知道它
  在你的資料上多準，得拿你的資料跑。
- **沒有 recall 的證據。** 沒有測「藏得很深的個資會不會被漏掉」——例如混在
  base64、log 雜訊或外文裡的身分證號。`min_confidence` 是對這件事的部分防護，
  不是解法。
- **`classes.json` 改了會怎樣，沒有測。** 描述就是判斷依據，換一份梯子等於換
  一個決策，這裡沒有任何檢查能告訴你新的描述分得開不開。
