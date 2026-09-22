# jev-data-policy-module

判斷一段內容的敏感度：裡面有沒有可識別到個人的資料、有沒有憑證。判斷交給
[Jev decision API](https://www.jevai.org/docs)，回傳的是一個**等級**，到此為止。

這個模組不認識任何收件對象。裡面沒有廠商、沒有模型、沒有目標、沒有白名單，
也沒有核准清單。「`confidential` 能不能送去某一處」是你的合約與風險的決定，
該住在那些東西旁邊，而不是住在讀文字的這顆裡面。那個切分就是這個模組的全部
重點：一件事——看內容，說出等級。之後發生什麼是呼叫端的事。

純 stdlib，零依賴。

## 用法

```bash
python3 -m jev_data_policy "客戶 A123456789 的帳單地址是..."
python3 -m jev_data_policy --json "..."
python3 -m jev_data_policy --classes        # 看梯子，不打網路
cat suspect.log | python3 -m jev_data_policy
```

```
backend:    openrouter  (from jev.json modules.jev_data_policy.backend)
content:    102 bytes
class:      confidential
confidence: 1.00
            confidential=1.00  internal=0.00  public=0.00  regulated=0.00
```

當成函式庫：

```python
from jev_data_policy import classify

c = classify("user wang.mei@example.com, phone 0912-345-678")
c.data_class            # 'confidential'
c.confidence            # 0.98
c.probabilities         # {'confidential': 0.98, 'internal': 0.02, ...}
c.at_least("internal")  # True
```

## 敏感度的梯子

四個等級，由低到高。每一級的定義都是內容裡**有什麼**，不是誰能看：

| 等級 | 裡面有什麼 |
|---|---|
| `public` | 不能識別到任何人，也不能拿來取得存取權 |
| `internal` | 沒有個資、沒有憑證，但不對外 |
| `confidential` | 能指認到特定的人，或能取得某個系統的存取權 |
| `regulated` | 受法規保護的個資，或能動錢、能進 production 的憑證 |

梯子住在 [`classes.json`](jev_data_policy/classes.json)，每一段描述會原封不動
交給 Jev 當成那一級的判準——**改描述就是改決策**。把 `JEV_DATA_POLICY`
（或 `DATA_POLICY_DATA_POLICY`）指到別的檔案就換一把梯子。有測試守著沒有任何
一段描述開始談核准、廠商或收件對象；這個模組是靠它維持只有一件事寬。

## 是機率，所以只往上、不往下

判斷是 Jev 的：一題型別化的 `choice` 決策，回傳一個等級與它的機率分布。
Jev 回型別化的決策、不回自由文字，所以沒有誠實的方式讓它列出**是哪一段**
文字洩漏的。能給的是等級，所以給的就是等級。

「這裡面有沒有個資」用機率回答是錯的形狀，所以不確定時絕不往下取整：

```python
c = classify(text, min_confidence=0.9)
c.data_class       # 'confidential'
c.escalated_from   # 'public' —— Jev 原本說的，留在紀錄裡
```

低於 `min_confidence` 時，答案會被抬到仍帶有機率的最高一級，而 Jev 原本說的
會被保留而不是覆蓋掉。升級永遠只往上。預設是 `0.0`：原樣回報 Jev 說的。

其他失敗也都往同一個方向倒。服務掛掉是往上抬，不是回 `public`。梯子以外的
等級是拒絕，不是照傳。超過 16 KiB 的內容是拒絕，不是截斷——只分類前半段，
正是一份 regulated 的內容乾乾淨淨回來的方式。

## 要分類就會揭露

這個模組會把那段文字送給 Jev。那是問問題的代價，設計不掉：如果內容本身不能
離開這台機器，傳一段描述而不是原文。

```python
classify("一張客訴單，裡面有客戶的身分證字號和地址")
```

每次呼叫前都會拿 OpenRouter 金鑰對 model slug 做檢查，所以它只到得了 Jev 的
決策模型，別的地方去不了——指向 `openai/*` 或 `anthropic/*` 會被拒絕。

## 設定

跟另外兩個模組同一套 resolver：`DATA_POLICY_<名稱>` 贏過 `JEV_<名稱>`，
贏過 `jev.json` 的 `modules.jev_data_policy`，贏過它的頂層，贏過內建預設。

| 設定 | 共用名 | 模組專屬名 | `jev.json` |
|---|---|---|---|
| 決策走哪條路 | `JEV_BACKEND` | `DATA_POLICY_BACKEND` | `backend` |
| 敏感度等級表 | `JEV_DATA_POLICY` | `DATA_POLICY_DATA_POLICY` | `data_policy` |
| Jev endpoint | `JEV_BASE_URL` | `DATA_POLICY_BASE_URL` | `base_url` |

金鑰只能從 `.env` 來；寫進 `jev.json` 會被拒絕，因為那個檔案會進版控。

## 驗證

```bash
python3 -m jev_data_policy.verify          # 32 個離線檢查
python3 -m jev_data_policy.verify --live   # 再加 6 筆真實分類
```

離線那層把 transport 上了 stub、不打網路，所以它不是綠的就是模組壞了。
兩層實際產出什麼，寫在 [result.md](result.md)。
