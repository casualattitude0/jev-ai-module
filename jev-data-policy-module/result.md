# Results

Data class in : allow-list out. Last updated 2026-09-22, 6 targets and 1 service,
all shipped at `public`.

這個模組跟另外兩個不一樣:它**不打任何 API**,也不回機率。同一份 `policy.json`
配同一個問題,永遠得到同一個答案。所以下面的依據全部是可重跑的實際輸出,
沒有取樣、沒有準確率。

## 出廠狀態 — 全部只核准 public

    $ python3 -m jevpolicy

    classes: public < internal < confidential < regulated
    external floor: confidential  (at or above this, outbound text must be redacted)

    targets:
      gpt-5.6-luna                 up to public
      gpt-5.6-terra                up to public
      gpt-5.6-sol                  up to public
      claude-opus-5                up to public
      claude-sonnet-5              up to public
      claude-haiku-4-5-20251001    up to public

    services:
      jevai.org                    up to public

6 個 target 的 `approved_by` 全是 `null`。這不是還沒填完,是**預設就不該有人
被放行** — 往上放行是法務與資安的決定,這個 repo 不可能知道你跟供應商簽了什麼。
驗證項 `no target is pre-approved above public` 與 `approvals above public are
attributed` 把這件事釘死:任何高於 public 的核准都必須留下簽核人。

## 單一判斷 — 拒絕時說得出怎麼補

| 指令 | 輸出 |
|---|---|
| `--class internal` | `targets cleared for 'internal': none` |
| `--check jevai.org public` | `ALLOW jevai.org may handle public` |
| `--check jevai.org internal` | `DENY` + 理由 + 兩條出路 |

那句 DENY 的完整樣子:

    DENY  'jevai.org' receives the task text passed to the router, and is not
    approved for data class 'internal'. Pass redacted text and redacted=True,
    or classify the call lower and keep the payload local.

它講的是**這個對象會看到什麼**(`receives`),而不只是「不准」。

## 失敗一律往關的方向倒

| 情況 | 結果 |
|---|---|
| 對象不在 policy 裡 | public-only,不是全開 |
| 對象有列但沒有 `approved_classes` | public-only |
| class 不在 `classes` 清單裡 | `PolicyError`,不是猜最近的 |
| 重複的 target id / 缺 id | 載入時就拒絕 |
| `external_class_floor` 不在 class 清單裡 | 載入時就拒絕 |
| 送出的文字達到或超過 floor(`confidential`) | `ExternalLeakError` |
| 同上但 `redacted=True` | 放行,連最敏感的 class 也放行 |

## 組合 — 在對方看到選項之前就先移除

`guarded_call` 把獲准清單當成 `allow` 傳給消費端,兩個驗證項說明它的邊界:

    PASS  guarded_call narrows the consumer's allow-list  (allow=['gpt-5.6-luna', 'gpt-5.6-terra'])
    PASS  guarded_call refuses before calling the consumer  (consumer never ran)

第二項是重點:不獲准時消費端**根本沒被呼叫**,所以敏感度不會變成 router 評分裡
的一個權重 —— 選項在被看到之前就已經不在了。

## Counts

    python3 -m jevpolicy.verify                  22 passed, 0 failed

22 項全離線,沒有網路呼叫,不受限流影響,所以這一層必須永遠是綠的 ——
沒有像另外兩個模組那樣的 per-model 或 live 分層可言。

## Not verified

- **`policy.json` 以外的政策檔。** `JEV_DATA_POLICY` 指到別的檔案這條路徑有測,
  但測的是模組自己造出來的暫存政策,不是任何真實組織簽核過的檔案。
- **真實簽核流程。** `approved_by` / `approved_on` 只被驗證「高於 public 就必須
  有值」,至於那個值是否對應到真的有人審過,這個模組無從得知。
