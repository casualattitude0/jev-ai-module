# jev-data-policy-module Review 結果

三輪 review。**第一輪**離線（stub transport）找出並修正 4 個程式缺陷；
**第二輪**改打真實 OpenRouter 後端，驗證回應 schema 並實測判斷準確率，
發現並修正 3 個分類邊界缺陷；**第三輪**以同一組輸入完整重跑，確認結果穩定。
精簡版的逐筆結果另見 [VERIFY.md](VERIFY.md)。

| 輪次 | 方式 | 結果 |
|---|---|---|
| 一 | 離線，stub transport | 32/32 離線檢查綠；自訂 20 組輸入找出 **4 個缺陷**，全修 |
| 二 | **真實 OpenRouter 呼叫** | 38/38（含 6 筆 live）；29 筆 ground-truth 評測，準確率 83% → **93%**，FP 3 → **1**，FN 維持 **0** |
| 三 | **真實呼叫完整重跑** | 29/29 等級與第二輪完全一致，confidence 平均漂移 0.010；38/38 仍綠 |

實測環境：backend `openrouter`（來自 `jev.json modules.jev_data_policy.backend`）、
model slug `~typesafe/jev-latest`、實際應答版本 `typesafe/jev-1.13-20260917`、
provider `TypeSafe`。單次呼叫約 **0.45–0.70s**，成本約 **2.6e-05 USD**。

---

# 第二輪：真實後端實測

## 壹、真實回應與模組假設的落差

### 1. 實際回應 schema（真實抓取）

一筆真實回應的完整外層（`POST https://openrouter.ai/api/alpha/decisions`，0.51s）：

```json
{
  "model": "typesafe/jev-1.13-20260917",
  "answers": {
    "data_class": {
      "type": "choice",
      "choice": "public",
      "probabilities": { "regulated": 0, "internal": 0, "confidential": 0, "public": 1 },
      "confidence": 1
    }
  },
  "usage": { "input_tokens": 620, "output_tokens": 48, "cost": 2.604e-05 },
  "id": "gen-dec-1790092545-Ksy6efZT0Km3WkS5MCkt",
  "provider": "TypeSafe"
}
```

對照模組假設，逐項查核：

| 模組的假設 | 真實情況 | 判定 |
|---|---|---|
| 答案鍵為 `choice` 或 `decision`（`VALUE_KEYS`） | 真實回 `choice` | **相符** |
| `answers[QUESTION]` 巢狀結構 | 相符；`_openrouter_post` 取 `{"answers": ...}` 正確 | **相符** |
| `confidence` 是 float | 實際是 **int**（`1`、`0`），非 float | **落差，但無害**：`classify.py` 已 `float()` 轉換 |
| `probabilities` 值是 float | 實際是 **int**（`0`/`1`）混 float | **落差，但無害**：`_escalate` 用 `> 0` 比較、CLI 用 `:.2f` 格式化，int 皆可 |
| `probabilities` 涵蓋所有類別 | 相符，四個鍵齊全且鍵名與 `classes.json` 完全一致 | **相符** |
| 回傳類別必在梯子內 | 相符，29+6+2 筆實測從未出現梯子外的值 | **相符** |
| 額外欄位 `usage` / `id` / `provider` / `model` | 存在，但 `_openrouter_post` 全數丟棄 | 見「未修正的觀察」 |

### 2. 類別名稱確實由 `classes.json` 決定（live 驗證）

README 宣稱「指到別的檔案就換一把梯子」。以 `DATA_POLICY_DATA_POLICY` 指向一份
`green / amber / red` 的自訂梯子，真實呼叫：

- `"The weather in Taipei is mild today."` → `'green'` @1.00，`probs={'red':0,'green':1,'amber':0}`
- `"Employee 林建宏, jh.lin@ourcorp.com.tw, extension 2317."` → `'red'` @0.99

模型**原封不動回傳自訂 id**，機率鍵也與自訂梯子完全一致。宣稱屬實，
且 `decided not in ids` 的防線不會誤擋。

### 3. 真實錯誤路徑（live）

以無效 `OPENROUTER_API_KEY` 實打：

```
JevError after 0.25s (retries=1)
  status: 401
  message: HTTP 401: {"error":{"message":"User not found.","code":401}}
```

CLI 對應輸出 `FAIL: HTTP 401: ...`、exit 1。**401 不重試**（非 429/5xx），正確；
失敗往上拋、絕不回 `public`，與 README 一致。

另註：OpenRouter 的錯誤走的是 HTTP 狀態碼路徑（`_request` 的 `HTTPError` 分支），
而非 `_openrouter_post` 裡 `parsed.get("error")` 的 200-帶-error-body 分支；兩條都在。

### 4. 延遲與 retry

- 單筆 0.45–0.70s（29 筆平均 0.58s），長文件（5.4 KB）同樣 0.57s，無明顯長度懲罰。
- retry 僅對 429 與 5xx，backoff `2**attempt * 5` = 5s、10s。預設 `retries=3`、
  `timeout=30`，最壞情況約 105s 才放棄。此為既有設計，未更動。

---

## 貳、判斷準確率評測（29 筆，真實呼叫）

### 評測設計

判準是「給一段真實使用者輸入，有沒有正確判斷出有無敏感資料」。因此每筆標註兩層 ground truth：

- **binary（主要）**：是否 ≥ `confidential`，即呼叫端該不該當成帶敏感資料。
  - **FN（假陰性）= 該擋沒擋**，最嚴重。
  - **FP（假陽性）= 過度敏感**，傷可用性。
- **exact（次要）**：是否落在我標註的那一級。

集合涵蓋：明確敏感（R1–R5 regulated、C1–C5 confidential）、
明確不敏感（P1–P4 public、I1–I3 internal）、
以及 12 筆灰色地帶（G1–G12），灰色地帶是重點。

### 逐筆 Input -> Output（全部為真實 OpenRouter 呼叫）

`OK*` = binary 正確但 exact 差一級（皆非安全問題）。
| ID | ground truth | 敏感? | v2 判斷 | v2 conf | v2 | v3 判斷 | v3 conf | v3 | 這筆在測什麼 |
|---|---|---|---|---|---|---|---|---|---|
| R1 | `regulated` | 是 | `regulated` | 0.99 | OK | `regulated` | 0.98 | OK | live prod credential that moves money |
| R2 | `regulated` | 是 | `regulated` | 1.00 | OK | `regulated` | 1.00 | OK | health + card |
| R3 | `regulated` | 是 | `regulated` | 1.00 | OK | `regulated` | 1.00 | OK | national ID + health |
| R4 | `regulated` | 是 | `regulated` | 0.98 | OK | `regulated` | 0.99 | OK | live payment credential + bank account |
| R5 | `regulated` | 是 | `regulated` | 0.69 | OK | `regulated` | 0.69 | OK | children's data |
| C1 | `confidential` | 是 | `confidential` | 0.98 | OK | `confidential` | 0.99 | OK | contactable person |
| C2 | `confidential` | 是 | `confidential` | 1.00 | OK | `confidential` | 1.00 | OK | session credential |
| C3 | `confidential` | 是 | `confidential` | 0.96 | OK | `confidential` | 0.97 | OK | private key |
| C4 | `confidential` | 是 | `confidential` | 0.80 | OK | `confidential` | 0.93 | OK | connection string with password |
| C5 | `confidential` | 是 | `confidential` | 1.00 | OK | `confidential` | 1.00 | OK | staff records |
| P1 | `public` | 否 | `public` | 1.00 | OK | `public` | 1.00 | OK | generic tech Q |
| P2 | `public` | 否 | `public` | 1.00 | OK | `public` | 1.00 | OK | marketing/licence copy |
| P3 | `public` | 否 | `public` | 1.00 | OK | `public` | 1.00 | OK | small talk |
| P4 | `public` | 否 | `public` | 0.93 | OK | `public` | 0.95 | OK | published API docs |
| I1 | `internal` | 否 | `internal` | 1.00 | OK | `internal` | 1.00 | OK | unreleased plans + margin |
| I2 | `internal` | 否 | `internal` | 1.00 | OK | `internal` | 0.98 | OK | architecture detail |
| I3 | `internal` | 否 | `public` | 0.99 | OK* | `public` | 1.00 | OK* | private source, no secrets |
| G1 | `public` | 否 | `public` | 0.70 | OK | `public` | 0.99 | OK | placeholder, grants nothing |
| G2 | `public` | 否 | `confidential` | 0.50 | FP | `public` | 0.32 | OK | fake password in a test fixture |
| G3 | `public` | 否 | `public` | 0.78 | OK | `public` | 0.90 | OK | role alias on a reserved domain, identifies nobody |
| G4 | `internal` | 否 | `public` | 0.83 | OK* | `internal` | 0.89 | OK | aggregate, anonymised |
| G5 | `confidential` | 是 | `confidential` | 0.80 | OK | `confidential` | 0.51 | OK | NEEDLE: one live secret buried mid-way in a boring build log |
| G6 | `regulated` | 是 | `regulated` | 0.99 | OK | `regulated` | 0.99 | OK | NEEDLE: one national ID buried mid-way in a Chinese report |
| G7 | `internal` | 否 | `internal` | 0.90 | OK | `internal` | 0.48 | OK | talks ABOUT credentials, contains none |
| G8 | `internal` | 否 | `confidential` | 0.97 | FP | `confidential` | 0.20 | FP | redacted, not re-identifiable |
| G9 | `public` | 否 | `public` | 0.98 | OK | `public` | 0.98 | OK | named public figure in published news, no contact data |
| G10 | `internal` | 否 | `confidential` | 0.65 | FP | `internal` | 0.98 | OK | RFC1918 private IP identifies nobody |
| G11 | `public` | 否 | `public` | 0.66 | OK | `public` | 0.96 | OK | obvious placeholder in a bug report |
| G12 | `internal` | 否 | `internal` | 0.68 | OK | `internal` | 0.98 | OK | k-anonymised cohort |

### Confusion matrix（binary：≥ confidential 視為「敏感」）

**v2（優化前）**

|  | 判為敏感 | 判為不敏感 |
|---|---|---|
| **實際敏感**（12） | 12 (TP) | **0 (FN)** |
| **實際不敏感**（17） | 3 (FP)：G2, G8, G10 | 14 (TN) |

**v3（優化後）**

|  | 判為敏感 | 判為不敏感 |
|---|---|---|
| **實際敏感**（12） | 12 (TP) | **0 (FN)** |
| **實際不敏感**（17） | 1 (FP)：G8 | 16 (TN) |

| 指標 | v2 | v3 |
|---|---|---|
| exact 準確率 | 24/29 (83%) | **27/29 (93%)** |
| binary 準確率 | 26/29 (90%) | **28/29 (97%)** |
| **FN（該擋沒擋）** | **0** | **0** |
| FP（過度敏感） | 3 | **1** |
| 平均延遲 | 0.53s | 0.58s |

### 失誤分析與優化

**v2 的 3 筆 FP 同源**：`confidential` 的描述以「email addresses、IP addresses、
passwords」這類**識別子型別**平鋪直述地列舉，反而蓋過它自己的標題
「Identifies a specific living person」。於是只要出現該形狀的字串就中：

- **G10** `192.168.1.42`（RFC1918 私網位址）→ `confidential` @0.65。描述字面就寫了
  「IP addresses」，模型只是照辦；但私網位址指不到任何人。
- **G8** 已遮蔽的 `j***@***.com` → `confidential` @**0.97**（高信心誤判）。
- **G2** 測試 fixture 的 `PASSWORD = "hunter2"` → `confidential` @0.50（模型自己在搖擺）。

**優化手段**：只動 `classes.json`（README 明言「改描述就是改決策」，這正是設計好的旋鈕），
程式碼一行未改。`version` 2 → 3：

1. `instructions` 新增 placeholder / 遮蔽值的 carve-out，並補上兩條防呆：
   「不確定是不是真值就當成真值」、「一個真識別子或一把能用的鑰匙就足以定級，
   不論它埋在多平凡的文字裡」。
2. `confidential`：識別子列舉加上「必須真的指向某個人」的限定，並明確排除
   「不指名任何人的共用信箱別名」與「私網位址」；密碼加上「真的能打開東西」的限定。
3. `internal`：明確收納「彙總／匿名化數字」、「識別欄位已遮蔽的紀錄」、
   「指向機器或私網而非人的位址」。
4. `public`：明確收納 placeholder 與「只描述而不攜帶祕密」的敘述。
5. `regulated`：補一句「一個這種值就夠，即使埋在例行文字裡」。

**過程中踩到並修掉的回歸（重要）**：第一版改寫把 carve-out 寫成
「obviously fictional or **example** value」，結果 **G5 直接變成 FN**——
埋在 build log 中段的 `AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/...EXAMPLEKEY`
因為字面含 `EXAMPLE` 被判成 `public` @0.86。同時 R1 信心從 0.99 掉到 0.56、
C1 從 0.98 掉到 0.35（`wang.mei@example.com` 也踩到同一條）。
這正是「放寬 FP 會買到 FN」的活教材。修法是把 carve-out 收窄成**語法形狀**
（`xxxx`、`REPLACE_ME`、角括號、星號遮蔽）**加上下文條件**（文件／教學／談輸入格式的提問），
並補上「賦值到 log、config、環境傾印或原始碼裡的值一律視為真值，
不管它的字面拼出什麼」。收窄後 FN 歸零、信心全數回升（R1 0.98、C1 0.99、G5 0.51）。

### 殘留的 2 筆不 exact（均非安全問題）

- **G8**（已遮蔽紀錄）仍是唯一 FP，但信心從 **0.97 掉到 0.20**——模型從「自信地錯」
  變成「明說自己不確定」。且 FP 是安全方向。
- **I3**（一段沒有任何祕密的通用 interval-merge 程式碼）判 `public`，我標 `internal`。
  `classes.json` 的 instructions 要求「只看內容本身」，而那段程式碼內容上確實沒有
  任何跡象顯示它是私有的——**這是我 ground truth 的模糊，不是模組的錯**。

### 穩定度（同輸入重跑 3 次）

| 案例 | 三次結果 | 穩定 |
|---|---|---|
| R5 | regulated ×3 (0.57/0.63/0.60) | 是 |
| C4 | confidential ×3 (0.93/0.93/0.92) | 是 |
| G5 | confidential ×3 (0.50/0.50/0.48) | 是 |
| G6 | regulated ×3 (0.99) | 是 |
| G7 | internal ×3 (0.46/0.45/0.46) | 是 |
| G10 | internal ×3 (0.98/0.99/0.98) | 是 |
| **G2** | public / **confidential** / public (0.24–0.29) | **否** |
| **G8** | internal / internal / **confidential** (0.16–0.17) | **否** |

**關鍵發現：會跳動的兩筆，信心值都 < 0.30；穩定的六筆都 ≥ 0.45。**
`confidence` 在這組資料上是有效的可信度訊號，不是裝飾。

### `min_confidence` 門檻掃描（以 v3 實測資料重算）

| `min_confidence` | FN | FP | 被升級的筆數 |
|---|---|---|---|
| 0.0（預設） | 0 | 1 | 0 |
| 0.3 | 0 | 1 | 1 |
| 0.5 | 0 | 2 | 2 |
| 0.7 | 0 | 2 | 3 |

因為 FN 本來就是 0，**升門檻在這組資料上買不到任何安全性，只會增加 FP**。
預設 `0.0` 是合理的；把 `min_confidence` 當成對抗「低信心跳動」的保險時，
0.3–0.5 是有意義的區間（剛好蓋住上面兩筆 < 0.30 的不穩定案例）。

### 額外的 live 觀察：未標註的識別碼會低一級

把中文標籤拿掉、只寫 `id F221304567` 夾在長文件裡：

- needle 在**開頭** → `confidential` @0.99（0.53s）
- needle 在**結尾** → `confidential` @0.98（0.57s）
- 有 `身分證字號` 標籤時（G3/G6）→ `regulated` @0.99–1.00

亦即**位置不影響偵測**（頭、中、尾都抓得到），但**沒有標籤的純英數識別碼會被讀成
一般 user ID，落在 `confidential` 而非 `regulated`**。binary 安全，
但以 `data_class == "regulated"` 做精確比對的呼叫端要知道這件事。

### 大小與版面

v3 描述變長後重新量測：instructions + criteria 共 **3035 bytes**；
內容取滿 `MAX_CONTENT_BYTES`（16 KiB）時，整個 body 為 **19442 bytes**，
距 32 KiB 上限尚有 **13326 bytes** 餘裕。未觸及上限。

### 第三輪：同組輸入完整重跑（真實呼叫）

為確認優化後的結果不是單次運氣，同一組 29 筆在 v3 之下完整重跑一次，與第二輪逐筆比對：

| 比對項 | 結果 |
|---|---|
| 等級改判的筆數 | **0**（29/29 與第二輪完全一致） |
| exact 準確率 | 27/29 (93%)，與第二輪相同 |
| binary 準確率 | 28/29 (97%)，與第二輪相同 |
| FN / FP | **0** / 1（G8），與第二輪相同 |
| confidence 平均漂移 | **0.010** |
| confidence 最大漂移 | **0.06**（G2 0.32→0.26） |

漂移最大的五筆：G2 0.32→0.26、G5 0.51→0.46、R5 0.69→0.64、G8 0.20→0.16、G4 0.89→0.86。
全部是小數點後第二位的抖動，沒有任何一筆接近會改變 argmax 的距離。

值得一提：第二輪測穩定度時會跳動的 G2 與 G8，這次都落回同一邊，但它們的 confidence
依然是全場最低（0.26、0.16）。低信心那兩筆是模型在明說自己不確定，不是判斷退化。
第三輪 `verify.py --live` 同樣 38/38。

---

## 參、未修正的觀察

- `_openrouter_post` 丟棄真實回應中的 `usage`（含 `cost`）、`id`、`provider`
  與**實際應答版本** `model`（`typesafe/jev-1.13-20260917`）。
  以模組「只回一個等級」的單一職責而言可以接受，且 README 未承諾這些；
  但要稽核「這個判斷是哪一版模型做的」時目前拿不到。未改，因為那會擴張回傳契約。
- retry 最壞情況約 105s（3 次 × 30s timeout + 5s/10s backoff）。既有設計，未更動。

---

# 第一輪：離線程式缺陷（保留紀錄）

> 本輪以 stub transport 進行、不打網路。第二輪已將其中每一條路徑改用真實呼叫重跑，
> 結果見上（真實 schema、真實錯誤碼、真實 live 樣本皆通過）。

## 一、分析依據

- 讀完 `README.md` 與 `jev_data_policy/` 下全部檔案：`__init__.py`(7)、`__main__.py`(77)、
  `classify.py`(210)、`client.py`(283)、`verify.py`(467)、`classes.json`。
- 契約：輸入一段文字 + 選用 `context`/`min_confidence`/`backend`；
  輸出 `Classification`（`data_class`、`confidence`、`probabilities`、
  `escalated_from`、`content_bytes`）。只看內容說等級，不認識收件對象。
- 核心邏輯出處：`classify.py:78-90`（排序）、`150-158`（`_escalate` 只往上）、
  `177-182`（>16 KiB 拒絕）、`193-196`（梯子外拒絕）；
  `client.py:232-240`（slug 前綴檢查）、`149-164`（設定 resolver）、`118-134`（拒收金鑰）。
- 執行：`python3 -m jev_data_policy.verify`（32/32）、`--classes`、各式錯誤輸入的 CLI 行為，
  以及自寫 stub 腳本驅動 `main()` 與 `classify()`。

## 二、Input -> Output（離線）

| # | 測項 | 預期 | 實際 | 判定 |
|---|---|---|---|---|
| T1 | `verify`（離線 32 項） | 0 failed | `32 passed, 0 failed` | PASS |
| T2 | `--classes` | 列四級、不打網路 | 四級與來源路徑齊全 | PASS |
| T3 | README 函式庫範例 | `confidential`/0.98/`at_least('internal')=True` | 完全相符 | PASS |
| T4 | 升級往上 | `internal`→`regulated`，記錄 `escalated_from` | 相符，`confidence` 保留 0.4 | PASS |
| T5 | 升級不往下 | 低信心 `regulated` 維持 | 維持且未標記升級 | PASS |
| T6 | 傳輸失敗 | 往上拋，不回 `public` | `JevError` 傳出 | PASS |
| **T7** | `--context` 缺 `=` | `FAIL: ...`, rc=1 | **裸 `ValueError` traceback** | **FAIL → 已修** |
| **T8** | `--backend` 覆寫後的顯示 | 顯示實際走的路 | **顯示 `openrouter`，實際走 `native`** | **FAIL → 已修** |
| **T9** | `confidence` 非數值 | `PolicyError` | **裸 `ValueError`，CLI 攔不到** | **FAIL → 已修** |
| **T10** | `probabilities` 非 dict | 往上倒到梯子頂 | **`AttributeError` 崩潰** | **FAIL → 已修** |
| **T11** | `retries=0` | 明確拒絕 | **回傳 `None` → `AttributeError`** | **FAIL → 已修** |
| T12 | 大小邊界 16384 / 16385 / 中文 | 通過 / 拒絕 / 位元組計 | `16384` / `PolicyError` / `6` | PASS |
| T13 | `min_confidence` 0.0/0.5/1.0/-0.1/1.5 | 邊界為嚴格小於；界外拒絕 | 全數相符 | PASS |
| T14 | 空內容與非字串 | 一律 `PolicyError` | 全數 `nothing to classify` | PASS |
| T15 | 梯子外類別 / 缺答案 | 拒絕 | `unknown data class` / `no answer` | PASS |
| T16 | `context` 進 body | `state` 內含 `context` | `{'content':'x','context':{'source':'ticket'}}` | PASS |
| T17 | CLI 升級顯示 | 標示 escalated from + 機率排序 | 相符 | PASS |
| T18 | slug 前綴檢查 | 拒絕 `openai/*`、`anthropic/*` | `not a Jev decisions model` | PASS |
| T19 | `jev.json` 拒收金鑰 | 拒絕並要求放 `.env` | 相符 | PASS |
| T20 | 設定解析順序 | scoped env > shared env > file > default | 兩條路徑皆相符 | PASS |

## 三、修正項目

### 第一輪（程式，4 + 1 項）

1. **`__main__.py` — `--context` 畸形輸入拋裸 traceback**
   `dict(kv.split("=",1) ...)` 丟 `ValueError`，而 CLI 只攔 `(PolicyError, JevError)`;
   `PolicyError` 是 `ValueError` 的子類別，反向攔不到。
   新增 `parse_context()` 先驗證，改拋 `PolicyError`。

2. **`__main__.py` — `--backend` 覆寫後輸出報錯來源**
   `backend:` 一行無條件重讀 resolver，忽略 `args.backend`，等於在說謊。
   改為有 `--backend` 時用 `(args.backend, "--backend")`。

3. **`classify.py` — `confidence` 非數值時例外型別錯誤**
   `float("high")` 丟裸 `ValueError`，同樣穿過 CLI 攔截。
   包 `try/except`，改拋 `PolicyError`。

4. **`classify.py` — `probabilities` 非 dict 時崩潰**
   `or {}` 只擋 falsy；字串會帶進 `_escalate()` 的 `.get()` 炸掉。
   改 `isinstance` 檢查，非 dict 歸零成 `{}`，走既有的「無證據 → 抬到頂端」路徑。

5. **（附帶）`client.py` — `retries=0` 回傳 `None`**
   `for attempt in range(retries)` 一次都不跑，隱含回傳 `None`。
   進迴圈前 `if retries < 1: raise JevError(...)`。

### 第二輪（分類邊界，1 個檔案）

6. **`classes.json` v2 → v3 — 類別描述以識別子型別列舉，蓋過自己的定義**
   造成 3 筆 FP（私網 IP、已遮蔽欄位、測試 fixture 密碼）。
   重寫 `instructions` 與四級描述（詳見上方「失誤分析與優化」）。
   **程式碼一行未改**——README 說「改描述就是改決策」，這是設計好的旋鈕。

---

## 四、備註

- 第二輪所有分類結果均為**真實 OpenRouter 呼叫**（backend `openrouter`，
  `~typesafe/jev-latest` → `typesafe/jev-1.13-20260917`），三輪合計約 130 次呼叫，
  總成本約 0.004 USD。第一輪的 T1–T20 為離線 stub。
- `verify.py` 未改動，v3 描述仍通過它的
  `descriptions_are_about_content` 守門（無 approved / vendor / allowlist 等字眼）。
- 只動了 `jev-data-policy-module/` 內的檔案：
  `__main__.py`、`classify.py`、`client.py`、`classes.json`、`result.md`。
  未觸及其他兩個模組、repo 根目錄檔案，亦未 git commit。
