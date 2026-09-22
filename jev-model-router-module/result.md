# jev-model-router-module Review 結果

三輪：第一輪離線契約驗證，第二輪改用**真實 OpenRouter 後端**（`~typesafe/jev-latest`，
實際由 `typesafe/jev-1.13-20260917` 應答）實測路由品質並優化，第三輪用同一組輸入重跑
確認穩定。每一節都標明 **[live]** 或 **[離線]**。精簡版在 `VERIFY.md`。

- **[離線]** `python3 -m jev_model_router.verify` → 66 passed / 0 failed
- **[live]** `JEV_TEST_GAP=2 python3 -m jev_model_router.verify --live` → 77 passed / 0 failed / 2 skipped
- **[live]** `python3 -m jev_model_router.verify --models --no-smoke` → 78 passed / 0 failed
- **[live]** 15 筆評測集共跑 4 輪真實路由（優化前 / 中 / 後 / 穩定性重跑），約 120 次真實決策呼叫

真實呼叫的量測：單次決策 **0.23–0.7 秒**，一次 `select_model()`（評估＋挑選兩次呼叫）
**0.9–2.1 秒**；單次決策成本約 **$2e-05（評估，471 in / 84 out）** 到
**$4.3e-05（挑選，1031 in / 71 out）**。整份評測的 API 花費不到 $0.01。

## 一、分析依據

- **[離線]** 讀完 `README.md` 與 `jev_model_router/` 下所有 `.py` 與 `models.json`，整理出契約：兩段式路由、`(模型, effort)` variant、未發表指標視為「未知不是不及格」、`require_published` 的例外、`min_context_tokens` 硬過濾與長輸入改看 MRCR recall、成本只在門檻之後打平手、傳輸層只有 `cli` 且不帶任何 API key。
- **[離線]** 自寫三支腳本實跑：stub 掉 `router.post` 的路由邏輯 20 項；用假的 `claude` / `codex` 可執行檔真的開子行程測 `dispatch.serve()` 9 項；stub 驅動 `__main__.main()` 跑 11 組 CLI 參數。
- **[離線]** README 的示範輸出（difficulty 3 的 code 任務，floor 為 `tier>=deep, swe_bench_pro>=70, effort>=high`，Sol 被自己發表的 64.6 切掉）以 `registry.qualified(reg, 'code', 3.0)` 逐字比對，完全吻合。
- **[live]** 直接 dump 真實 OpenRouter 回應，逐欄比對模組的假設（見第二節）。
- **[live]** 對 `openrouter.ai/api/alpha/decisions` 實測五種 model slug 的接受／拒絕行為。
- **[live]** 用真的 `claude` / `codex` CLI 派工，確認 `models.json` 的 model id 真的存在、以及未知 id 的實際錯誤形狀。
- **[live]** 15 筆自訂評測集（附 ground truth）跑真實路由，並對 5 筆各重跑 3 次量測穩定度。

## 二、真實回應與模組假設的落差 **[live]**

真實 `/api/alpha/decisions` 回應（`state` 為 task，`questions` 為 `ASSESS_QUESTIONS`）：

```json
{"model": "typesafe/jev-1.13-20260917",
 "answers": {
   "difficulty": {"type":"score","score":2.7,"legend":{...},
                  "probabilities":{"0":0,"1":0,"2":0.3,"3":0.7},"confidence":0.7},
   "kind": {"type":"choice","choice":"code",
            "probabilities":{"code":1,"writing":0},"confidence":1},
   "ambiguous": {"type":"noul","noul":0.93}},
 "usage": {"input_tokens":471,"output_tokens":84,"cost":1.9782e-05},
 "id": "gen-dec-...", "provider": "TypeSafe"}
```

| 項目 | 真實行為 | 模組假設 | 判定 |
|---|---|---|---|
| answers 欄位名 | `score` / `choice` / `noul` + `confidence` | `router.assess()` 讀的正是這些 | 相符 |
| model-route 回程 | `answers.decision.choice` + `probabilities`（key 就是 variant id） | `_model_route_response` 正是這樣重組 | 相符 |
| 路由結果是否落在 registry 內 | 20 次真實挑選，回傳的 id **全部**在候選名單內 | `variant_by_id` 為 None 就拒絕 | 相符 |
| `guidance` | OpenRouter 沒有自由文字題型，恆為空 | 標記 `guidance_source: unavailable_on_openrouter`，不在本地編造 | 相符 |
| payload 大小 | 17 個候選 = 11.5 KiB | 本地 32 KiB 上限 | 相符，有餘裕 |
| 無效 model slug | HTTP 400 `{"error":{"message":"Model X does not exist","code":400}}` | `_request` 轉成 `JevError(status=400)`，不重試（非 5xx/429） | 相符 |
| 無效金鑰 | HTTP 401 `{"error":{"message":"User not found."}}` | `JevError(status=401)` | 相符 |
| 429 / 突發流量 | 連續 3 次、以及整份評測都**沒有**觸發 429；限流比 README 假設寬鬆得多 | 指數退避重試 3 次 | 相符（未被觸發，退避路徑未能實測） |
| **model slug 的拼法** | `~` 是浮動別名標記：`~typesafe/jev-latest` 可用，**釘死的版本沒有 `~`**（`typesafe/jev-1.13-20260917` 可用，`~typesafe/jev-1.13-20260917` 回 400） | 守門只收 `~typesafe/jev` 開頭 | **落差 1** |
| **`usage` / 成本** | 每次決策都回報 `input_tokens`、`output_tokens`、`cost` | 模組完全忽略，不對外揭露 | 觀察（未修） |
| **`difficulty.probabilities`** | 回報 0-3 四級的機率分布 | 只取 `score`，分布丟棄 | 觀察（未修） |
| CLI 派工（不經 OpenRouter） | `claude -p --model claude-sonnet-5/-opus-5` 與 `codex exec --model gpt-5.6-luna` 真的回 `pong`（5.1–7.3 秒） | `verified: true` | 相符，id 真的存在 |
| 未知／已下架 model id | `claude` exit 1「isn't described by this version's model catalog」；`codex` exit 1（訊息是 MCP transport 噪音，不好讀） | 包成 `DispatchError`，不 fallback 到 API | 相符（codex 的訊息品質受限於 CLI 本身） |

### 落差 1：釘版本被自己的守門擋住 **[live]**

`client.JEV_MODEL_PREFIXES` 只收 `~typesafe/jev`。實測五種 slug：

| slug | OpenRouter | 修正前的模組 |
|---|---|---|
| `~typesafe/jev-latest` | 200，由 `typesafe/jev-1.13-20260917` 應答 | 接受 |
| `typesafe/jev-1.13-20260917` | 200 | **拒絕** |
| `~typesafe/jev-1.13-20260917` | **400 does not exist** | 接受 |
| `openai/gpt-5.6-sol` | — | 拒絕（正確） |
| `typesafe/jev-9.9-nonexistent` | 400 | 拒絕（正確） |

也就是說 README 寫的「要釘 Jev 的版本，用 `JEV_OPENROUTER_MODEL`」**在修正前做不到**：
唯一能用的拼法會被本地擋下，而 `verify.py` 斷言「可以釘」用的又正好是 OpenRouter 會回 400 的拼法。
兩邊互相掩護，所以離線測試看不出來。

## 三、路由品質評測 **[live]**

### 評測集與 ground truth

GT 以「合理的 tier 區間」表示；`forbidden` 是該輸入下明確不該選的模型。
判定：低於區間 = **UNDER**（品質風險），高於區間 = **OVER**（成本浪費），
選到 forbidden = **FAIL**。

| id | 類別 | Input | GT tier | forbidden | 理由 |
|---|---|---|---|---|---|
| T1 | 瑣碎 | 修 README.md 的錯字 `recieve`→`receive` | fast..fast | — | 機械性單字編輯 |
| T2 | 瑣碎 | 對 src/ 跑 formatter 統一風格 | fast..fast | — | 一道指令，無需判斷 |
| T3 | 瑣碎 | 寫一行 shell 數 src/ 下每個 .py 的行數 | fast..fast | — | 單一慣用法 |
| M1 | 中等 | 寫 ISO-8601 duration 解析成 timedelta 的函式＋單元測試 | fast..balanced | — | 規格清楚但要真的寫碼 |
| M2 | 中等 | 測試 expected 90 got 100，折扣算在稅後而非稅前，修掉 | fast..balanced | — | 錯因已在回報裡診斷完 |
| M3 | 中等 | 為 GET /orders 加 cursor 分頁並更新測試 | fast..balanced | — | 一般功能開發 |
| H1 | 高難度 | 跨 40 檔重構 authentication，需求模糊且不能斷既有 session | deep..deep | — | 模糊的多檔重構 |
| H2 | 高難度 | 設計 5 億文件近似去重演算法，p99 < 200ms，並論證取捨 | deep..deep | — | 硬約束下的演算法設計 |
| H3 | 高難度 | 決定是否拆微服務並寫 ADR（6 人團隊、18 個月 runway） | deep..deep | — | 高後果的架構決策 |
| L1 | 長上下文 | 從生產 log dump 找出根因（`input_tokens=600000`） | balanced..deep | haiku, luna | 視窗＋recall 門檻 |
| L2 | 長上下文 | 摘要整個 repo dump 並列五大架構風險（`input_tokens=900000`） | balanced..deep | haiku, luna | 200K 視窗與 41.3 recall 出局 |
| E1 | 看似簡單實則難 | 把預設時區從 local 改成 UTC | balanced..deep | — | 一句話，但跨切面＋資料遷移 |
| E2 | 看似難實則一行 | 啟動崩潰 `ModuleNotFoundError: No module named 'requests'` | fast..fast | — | 聽起來是崩潰，其實是裝套件 |
| E3 | 模稜兩可 | 「把 dashboard 弄快一點。」 | balanced..deep | — | 完全沒有驗收標準 |
| E4 | 小改動但不可逆 | 寫 migration 砍掉正式環境的 users_old 表 | balanced..deep | — | diff 很小，後果不可逆 |

> GT 修正記錄：L2 起初把 opus / sonnet 也列為 forbidden，理由寫「只有 1.05M 視窗放得下」。
> 這是我標錯了——Claude 兩款的視窗是 1,000,000，放得下 900,000。三輪成績都以修正後的 GT
> 重新計分，沒有重打 API。

### 逐筆 Input -> Output 與優化前後對照 **[live]**

`d` 是真實回傳的 difficulty，`kind` 是真實分類結果。

| id | 優化前 | 中段（優化 1+2） | 最終（優化 1+2+3） |
|---|---|---|---|
| T1 | OK `luna@low` d=0 writing | OK `luna@low` d=0 writing | OK `luna@low` d=0 writing |
| T2 | **OVER** `sonnet-5@low` d=1 code | **OVER** `sonnet-5@low` d=0.97 code | **OK** `haiku@low` d=0.94 code |
| T3 | OK `luna@low` d=0.13 code | OK `luna@low` d=0.15 code | OK `luna@low` d=0.13 code |
| M1 | OK `sonnet-5@medium` d=1.11 | OK `sonnet-5@medium` d=1.07 | OK `sonnet-5@low` d=1.08 |
| M2 | OK `sonnet-5@medium` d=0.75 | OK `sonnet-5@low` d=0.76 | OK `sonnet-5@low` d=0.76 |
| M3 | OK `sonnet-5@medium` d=1.68 | OK `sonnet-5@medium` d=1.65 | OK `sonnet-5@medium` d=1.66 |
| H1 | OK `opus-5@xhigh` d=3 code | OK `opus-5@xhigh` d=3 code | OK `opus-5@xhigh` d=3 code |
| H2 | OK `opus-5@high` d=2.43 **writing** | OK `opus-5@high` d=2.46 **code** | OK `opus-5@xhigh` d=2.5 **code** |
| H3 | OK `opus-5@xhigh` d=2.74 **writing** | OK `opus-5@xhigh` d=2.73 **code** | OK `opus-5@xhigh` d=2.73 **code** |
| L1 | OK `terra@medium` d=2.01 research | OK `terra@medium` d=2.01 code | OK `terra@medium` d=2.01 code |
| L2 | OK `terra@medium` d=2.04 | OK `terra@medium` d=2.03 | OK `sonnet-5@medium` d=2.03 |
| E1 | OK `opus-5@high` d=2.32 | OK `opus-5@high` d=2.3 | OK `opus-5@high` d=2.31 |
| E2 | OK `luna@low` d=0.12 | OK `luna@low` d=0.14 | OK `luna@low` d=0.11 |
| E3 | OK `sonnet-5@medium` d=2 | OK `sonnet-5@medium` d=2 | OK `sonnet-5@medium` d=2 |
| E4 | OK `opus-5@high` d=1.86 | OK `opus-5@high` d=1.97 | OK `opus-5@high` d=1.95 |

### 統計

| | 優化前 | 中段 | 最終 | 穩定性重跑 |
|---|---|---|---|---|
| OK | 14 / 15 | 14 / 15 | **15 / 15** | **15 / 15** |
| OVER（成本浪費） | 1 | 1 | **0** | **0** |
| UNDER（品質風險） | 0 | 0 | 0 | 0 |
| FAIL（選到禁用模型） | 0 | 0 | 0 | 0 |

**一次 UNDER 都沒有出現**——四輪 60 次路由，沒有任何一次把高難度任務交給不夠力的模型。
所有偏差都偏向過殺，方向上是安全的。

### 單筆輸入的重複性 **[live]**

5 筆輸入各重跑 3 次（`assess()` 與 `select_model()` 各 3 次）：

| id | difficulty 三次 | 全距 | kind | 最終 variant |
|---|---|---|---|---|
| T1 | 0, 0, 0 | 0.00 | writing ×3 | `luna@low` ×3 |
| T2 | 0.99, 0.90, 0.92 | 0.09 | code ×3 | `sonnet-5@low` ×3 |
| H1 | 3, 3, 3 | 0.00 | code ×3 | `opus-5@xhigh` ×3 |
| H2 | 2.46, 2.46, 2.45 | 0.01 | writing ×3 | `opus-5@high` ×3 |
| E3 | 2, 2, 2 | 0.00 | code ×3 | `sonnet-5@medium` ×3 |

difficulty 全距 ≤ 0.09，kind 與最終選擇 15/15 完全一致。**評分穩定，不會跳**；
上面那些偏差是系統性的，不是雜訊。

### 第三輪：整組評測集重跑的穩定性 **[live]**

優化完成後，用同一組 15 筆輸入對真實後端再完整跑一次，與「最終」那一欄比對：

- **判定完全一致：15/15 PASS，OVER 0、UNDER 0。**
- **kind 分類 15/15 相同**，difficulty 最大飄動 **0.05**，**11/15 選出完全相同的 variant**。
- 4 筆有飄動，全部仍 PASS：

| id | 最終輪 | 第三輪 | 飄動性質 |
|---|---|---|---|
| M1 | `sonnet-5@low` d=1.08 | `sonnet-5@medium` d=1.08 | 只換 effort，同模型 |
| M2 | `sonnet-5@low` d=0.76 | `haiku@low` d=0.76 | 換模型，balanced→fast，仍在 GT 區間 |
| H2 | `opus-5@xhigh` d=2.50 | `opus-5@high` d=2.46 | 只換 effort；difficulty 跨過 2.5 級距邊界所致 |
| L2 | `sonnet-5@medium` d=2.03 | `terra@medium` d=2.04 | 換模型，兩者同為 balanced |

飄動集中在低 confidence 的平手案例（0.14–0.25），沒有任何一次跨出 GT 區間。H2 是
difficulty 2.50 對 2.46 剛好壓在 `level_for()` 的四捨五入邊界上，屬於可預期的邊界效應
而非不穩定。L2 從 Sonnet 換成 Terra 其實選得更好——Terra 的 MRCR 89.6 正是長上下文該看的指標。

## 四、修正項目

### 第一輪（離線）

**1. `verify.py` — `live()` 的 `min_context_tokens` 檢查永遠不可能通過**
取「最大視窗 + 1」當門檻再斷言倖存者視窗為 `None`，預設登錄檔裡有模型沒指定視窗；
但六個模型全都指定了，於是所有候選被濾光，`select_model()` 在斷言前就拋 `ValueError`，
斷言根本執行不到。因為只在 `--live`（需要金鑰）才跑，離線測試看不出來。改用
「最小視窗 + 1」當門檻，斷言改成「視窗未指定或不小於門檻」。**[live]** 實跑選出
`terra@medium`，Haiku 被正確切掉。

**2. `registry.py` — 被視窗切掉的模型不進稽核軌跡**
`qualified()` 把 `min_context_tokens` 交給 `variants()` 事先過濾，出局的模型既不在
`keep` 也不在 `cut`，等於從 `Selection.excluded` 無聲消失。README 把 `floor` 與
`excluded` 稱為「這次決策的稽核軌跡」，而同一個 `input_tokens` 推導出來的另一半門檻
（MRCR recall）是會列出理由的——同一道自動推導的門檻，一半有紀錄一半沒有。連帶後果是
視窗切光所有候選時，錯誤訊息會印出自相矛盾的 `no model clears the floor ...; excluded: {}`。
把過濾移進 `qualified()` 的迴圈裡，切掉時寫進 `cut`，理由形如
`context 200,000 below 600,000`。`variants()` / `candidates()` 本身不動，所以不走門檻的
`--no-assess` 路徑行為不變。

### 第二輪（真實後端）

**3. `client.py` — 釘 Jev 版本被守門擋住**（落差 1）
`JEV_MODEL_PREFIXES` 從 `("~typesafe/jev",)` 放寬為 `("~typesafe/jev", "typesafe/jev")`。
`~` 只是 OpenRouter 的浮動別名標記，釘死的版本沒有它。守門的資安性質不變：實測
`openai/gpt-5.6-sol`、`anthropic/claude-opus-5`、`typesafe/chat-9` 仍全部拒絕，
金鑰依然只到得了 Jev 家族。`verify.py` 的對應檢查改成兩種拼法都要收、同 vendor 的
非 Jev 模型要拒。

**4. `verify.py` — `bad_key` live 檢查是後端盲的**
它換掉 `JEV_API_KEY`（native 的憑證），但作用中的後端是 openrouter，讀的是
`OPENROUTER_API_KEY`——呼叫照樣成功，斷言失敗。若 `JEV_API_KEY` 根本沒設，
`os.environ["JEV_API_KEY"]` 還會直接 KeyError。改成先問 `resolve_backend()`，
換掉當前後端真正讀的那把。**[live]** 現在正確回報
`401 surfaced as JevError on the openrouter backend`。

**5. `verify.py` — `per_model_routing` 對 Sol 的探測與門檻自相矛盾**
它按 tier 發任務，deep 一律發「跨服務重設計交易帳本」這種 code 任務；但 code 難度 3
的門檻要求 `swe_bench_pro >= 70`，Sol 是 64.6，**依設計永遠不可能被選中**——這條檢查
測的其實是門檻，不是 Sol 的可達性。改成先用 `registry.qualified()` 找一個該模型真的
過得了的 kind（Sol 落在 browser），再挑一個同樣過得了同一道門檻的異 tier 對照組；
找不到對照組就 SkipCheck 而不是假 PASS。**[live]** 修正後 0 failed。

### 優化（針對評測中選錯的案例）

**優化 1：`router.py` 的 `kind` 判準——技術決策不是散文**
H2「設計近似去重演算法並論證取捨」與 H3「寫 ADR」，真實回傳的 kind 是 **writing**
（3/3 穩定），因為交付物是文字。但 `thresholds` 的 writing 第 3 級是
`min_tier: balanced`，**沒有** `swe_bench_pro` 閘門——一個前沿工程問題會落在為散文
設計的門檻底下。原本的判準只寫「code: Reading or writing code in a repository」與
「writing: Producing prose」，把設計／除錯／架構決策整個漏掉了。改寫成 code 明確涵蓋
「designing or debugging software, including algorithm and architecture decisions,
even when what gets handed back is an explanation rather than a diff」，writing 則
限縮為「judged as writing — style, clarity, argument — rather than on technical
correctness」。**[live]** 效果：H2、H3 都從 writing 轉成 code（H3 的 d=2.73 因此拿到
`deep + swe_bench_pro>=70 + effort>=high` 的正確門檻，Sol 被正確排除）；T1 仍然是
writing，這是對的——改 README 的錯字本來就是文字工作。

**優化 2：`models.json` + `registry.priorities_for()`——平手怎麼判，跟著門檻的判決走**
`default_priorities` 把 `cost` 固定排最後。難度 2-3 時這是對的（門檻已經篩過一輪）。
但難度 0-1 時門檻放行整個登錄檔，quality 排第一就沒有東西好權衡，只會預設買最貴的，
形成系統性過殺。新增 `models.json` 的 `priorities_by_level`：第 0 級
`["cost","latency","quality"]`、第 1 級 `["cost","quality","latency"]`、第 2-3 級維持
`default_priorities`。呼叫端明確傳 `priorities` 永遠優先，`assess_first=False` 沒有
難度可循則仍走 `default_priorities`。**[live]** 效果：瑣碎任務的 confidence 大幅上升
（T1 0.59→0.86、T3 0.59→0.90、E2 0.43→0.82），M2 從 `@medium` 降到 `@low`。

**優化 3：`registry.variants()`——成本格只升不降**
優化 2 之後 T2 仍然過殺。追出根因：**四個低 effort 的 variant 全部回報 `cost=low`**，
儘管真實價差 10 倍。

| variant | 修正前 cost | 真實價格 |
|---|---|---|
| `gpt-5.6-luna@low` | low | $0.2 / $1.2 |
| `gpt-5.6-terra@low` | low | $2.0 / $12.0 |
| `claude-sonnet-5@low` | low | $2.0 / $10.0 |
| `claude-haiku-4-5-20251001@low` | low | $1.0 / $5.0 |

`_shift(base_cost, step)` 把 `low-1` 與 `medium-1` 一起夾到 `low`。那一格正是
README 指定用來打平手的軸，壓平了就等於沒有東西可比——**[live]** 實測：即使強制
`priorities=['cost']`，Jev 仍然選 $2/$10 的 Sonnet 而不是 $0.20/$1.20 的 Luna，
因為被告知要優化的那個欄位說它們一樣便宜。改成 `_shift(m["base_cost"], max(0, step))`：
effort 只能讓 variant 變貴，不能變便宜——想得少確實花得少，卻不會換到比較便宜的計價表。
latency 維持雙向位移（少思考確實比較快）。**[live]** 效果：T2 從 `sonnet-5@low` 改為
`haiku@low`，評測集達到 15/15。

配套：`verify.py` 新增兩項離線檢查（「平手判準跟著門檻的判決」「呼叫端明確傳的
priorities 永遠優先」）；`README.md` 的平手段落、成本位移段落、以及 `JEV_OPENROUTER_MODEL`
的拼法說明都同步更新，文件與行為一致。

## 五、附帶觀察（未修、留給使用者決定）

- **`max_output_tokens` 沒有算進視窗需求** **[live]**：`input_tokens=900000` 時
  Sonnet（1M 視窗）過得了硬過濾，但 900K 輸入 + 它自己宣告的 128K 最大輸出 = 1,028K，
  其實**塞不下**。`min_context_tokens` 只比對輸入。要修得訂一個輸出保留量的政策，
  任何常數都是我憑空挑的，所以留給你決定，沒有動。L2 在「最終」那一輪選到 Sonnet
  （confidence 僅 0.15）就是踩到這個邊界。
- **`usage` 被丟掉**：每次真實決策都回報 `input_tokens` / `output_tokens` / `cost`，
  模組完全沒有往外傳。要做路由成本歸因的話，這是現成的資料。
- **`difficulty.probabilities`（0-3 四級分布）也被丟掉**，只取了 `score`。像 H2 的
  2.46 / 2.50 這種貼著級距邊界的分數，分布會比單一數字更有訊息量——第三輪 H2 的
  effort 飄動正是這個邊界造成的。
- **`registry.load()` 不驗證 `thresholds`**：`meets()` 用 `TIER_ORDER.get(min_tier, 0)`
  與 `efforts_table.get(min_effort, {})` 查表，所以 `thresholds` 裡打錯一個 tier 或
  effort 名稱，那道門檻會靜靜失效而不是報錯。目前資料全部合法。
- **`efforts` 表裡 `high` 與 `xhigh` 的 step 都是 1**，所以 `min_effort: "xhigh"` 會
  連 `high` 一起放行。目前沒有任何門檻用 `xhigh`，不影響現行行為。
- **codex 對未知 model 的錯誤訊息品質差** **[live]**：回的是 MCP transport 噪音而不是
  「model not found」。模組正確包成 `DispatchError`，但可讀性受限於 CLI 本身。
- **429 退避路徑未能實測**：連續 3 次與整份評測都沒觸發限流，真實限流比 README
  假設的寬鬆得多，所以 `_request` 的指數退避沒有機會被真實觸發驗證。
