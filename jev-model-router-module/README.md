# jev-model-router-module

依任務的複雜度與風險，決定該用哪個模型、以及該用多少推理力度（effort）。判斷
交給 [Jev decision API](https://www.jevai.org/docs)，選完之後可以直接把輸入送
過去執行，回傳答案。

一個路由選項是一組 **(模型, effort)** 配對，稱為 variant，id 形如
`claude-opus-5@high`。Effort 與模型同時決定，而不是事後才補，因為「Sonnet 深思」
和「Opus 快答」在成本與延遲上是兩個真正不同的選擇。

這個模組選模型、然後執行它。它**不**判斷一個任務安不安全、一次工具呼叫該不該
放行、一個 agent 的工作做完了沒有——那些是別人的決策，把它們塞進選模型的這顆
裡面，正是路由器長成政策引擎的方式。它唯一負責的資安性質是它自己造出來的：
它會開子行程，所以它要為子行程的環境負責（見[傳輸層](#傳輸層選中的模型怎麼被呼叫)）。

純 stdlib，零依賴。所有東西都住在 `jev_model_router/` 底下，包含 `models.json`：
把那一個資料夾複製到任何專案，`import jev_model_router` 就能用。

## 用法

```bash
cp .env.example .env    # repo 根目錄那一份，填入 JEV_API_KEY
```

`.env` 是從呼叫端的工作目錄一路往上找，找完才看 package 旁邊那份——所以宿主
專案自己的 `.env` 贏過這裡帶的任何東西，而這個模組自己不帶任何一份。

決策需要負責回答的那個 backend 的金鑰：`native` 要 `JEV_API_KEY`，`openrouter`
要 `OPENROUTER_API_KEY`。執行完全不需要金鑰，它走的是模型自己的 CLI。

```bash
python3 -m jev_model_router "把 billing 的重試邏輯重構掉，橫跨 40 個檔案"
python3 -m jev_model_router --stakes low --priorities cost,latency "改個變數名"
python3 -m jev_model_router --allow claude-opus-5@high,claude-sonnet-5 "..."
python3 -m jev_model_router --efforts low,medium "..."
python3 -m jev_model_router --min-context 400000 "摘要這個 repo"
python3 -m jev_model_router --input-tokens 600000 "摘要這份 dump"
python3 -m jev_model_router --kind code --difficulty 3 "..."   # 跳過第一段
python3 -m jev_model_router --no-assess "..."                  # 一次呼叫，沒有門檻
python3 -m jev_model_router --serve "解釋這段 stack trace：..."
python3 -m jev_model_router --list
python3 -m jev_model_router --json "..."        # 機器可讀
```

`--allow` 收 variant id（`claude-opus-5@high`）或裸的 model id，後者會保留那個
模型的所有 effort。

當成函式庫：

```python
from jev_model_router import select_model, serve

sel = select_model("把 parser 改成 async", stakes="high")
print(sel.variant_id, sel.effort, sel.confidence)
print(sel.assessment.difficulty, sel.assessment.kind)
print(sel.floor, sel.excluded)      # 這次決策的稽核軌跡
print(serve(sel, "把 parser 改成 async，檔案在這：..."))
```

`serve()` 用的是 Jev 選的那個 effort，所以被選中的力度就是實際送出去的力度。

## 難度決定，成本只用來打平手

路由分成兩段：

```
輸入 -> Jev 判斷難度與類型 -> 能力門檻（本地）-> Jev 挑選
                                成本不參與        成本才出手
```

第一段問 Jev 這個任務有多難（0-3）、是哪一類的工作。那個答案定出一道**能力
門檻**，在 `registry.qualified()` 本地套用。只有過得了門檻的模型才進得了第二段，
所以成本永遠不可能跟難度交換——它是已經做得來這件事的候選之間的平手決勝點，
不是一個互相競爭的目標。`default_priorities` 把 `cost` 排在最後，也是同一個理由。

```
$ python3 -m jev_model_router "refactor authentication across 40 files; requirements are ambiguous"
difficulty: 3  (code, ambiguous 0.93)
floor:      tier>=deep, swe_bench_pro>=70, effort>=high
  excluded  gpt-5.6-sol: swe_bench_pro 64.6 below 70
  excluded  claude-sonnet-5: tier balanced below deep
model:      Claude 5 Opus  (claude-opus-5)
effort:     high
```

門檻讓查來的 benchmark 變成承重的，而不是裝飾用的：Sol 是被它自己發表的數字
切掉的。

自己把 `kind` 與 `difficulty` 傳進來就能跳過第一段，或用 `assess_first=False`
一次呼叫路由完、完全不設門檻。過門檻的 variant 剛好只剩一個時會直接回傳，
不發第二次呼叫。

### 關於缺資料的兩條規則

沒有任何一個程式碼 benchmark 橫跨兩個模型家族——OpenAI 與 Anthropic 發表的
不是同一套，Terminal-Bench 的版本也不同。所以一個模型沒有發表的受管制指標會被
當成**未知，不是不及格**，跟沒有指定 context window 一樣。

例外是 `require_published`，用在有專屬 benchmark 的任務類型上。做瀏覽器的工作時，
一個從來沒有回報過 BrowseComp 的模型就不是瀏覽器模型，這時沉默真的就是缺席的
證據。Opus 5 明明 tier 夠高卻被排除在瀏覽器任務之外，就是因為這條。

Context window 是路由訊號，`min_context_tokens` 是硬過濾，但視窗大小不等於可用
的 context：過了長 context 的門檻之後，門檻改看實測的 recall。Luna 標榜 1.05M，
MRCR 卻只有 41.3，對上 Terra 的 89.6，所以大輸入會把它切掉。傳 `input_tokens`
進來，視窗需求與 recall 門檻會自動幫你推導出來。

## Function calling

`jev_model_router.tools` 提供兩個工具給 agent 呼叫：

- `select_model` —— 只路由，回傳選中的 id 與機率分布
- `route_and_serve` —— 路由完直接呼叫選中的模型，回傳它的答覆

兩種格式都接：

```python
from jev_model_router import as_anthropic_tools, as_openai_tools, call

tools = as_anthropic_tools()          # 或 as_openai_tools()
result = call("select_model", {"task": "...", "stakes": "high"})
```

`call()` 自己接住例外、回傳 `{"error": ...}`，所以一個 tool-use 迴圈不會因為
傳輸失敗就死掉。

## 傳輸層：選中的模型怎麼被呼叫

只有一條路，而且不需要任何金鑰：

| 傳輸層 | 怎麼做 |
|---|---|
| `cli`（預設，也是唯一一個） | 呼叫模型自己的 agent CLI —— `claude -p --model X --effort Y`、`codex exec` |

**絕不會用 API key 呼叫 Claude 或 GPT。** 三道保證，每一道後面都有一個檢查：

- 這裡沒有任何通往 Anthropic、OpenAI 或它們前面任何 gateway 的 HTTP 路徑。
  `dispatch.py` 不 import 任何 HTTP client、不讀環境裡任何憑證；
  `serve(..., api_key=...)` 直接被拒絕。
- 跑 CLI 之前，環境裡**所有 API key 都會被清掉**（`STRIPPED_KEY_ENV`）。
  Agent CLI 找得到 `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY` 就會拿來認證，
  一把留在 shell 或 `.env` 裡的金鑰等於繞一條長路變成計費的 API 呼叫。
  CLI 改用它自己登入的 session。Jev 的金鑰也一起清掉——CLI 用不到它，
  而一把從來沒進過子行程的憑證，也就不可能被子行程記錄或轉送。
- `OPENROUTER_API_KEY` 只到得了 Jev：每次決策前都檢查 model slug，所以
  `JEV_OPENROUTER_MODEL` 不可能被指向 `openai/gpt-5.6-sol` 或
  `anthropic/claude-opus-5`。釘一個 Jev 版本可以，其他都拒絕。

`resolve_transport` 與 `JEV_DISPATCH` 設定留著，所以要加別的傳輸層是一個刻意的
動作，不會是預設就發生。

## models.json 的欄位

`models.json` 就是登錄檔。每一筆：

| 欄位 | 意思 |
|---|---|
| `id` | 執行時送給供應商的 id |
| `provider` | `anthropic` 或 `openai` —— 決定 `dispatch.py` 用哪個 adapter |
| `tier` | `fast` \| `balanced` \| `deep` |
| `efforts` | 這個模型提供哪些 effort —— 一個一個選項 |
| `base_cost`、`base_latency` | `low` \| `medium` \| `high`，effort 位移之前的值 |
| `context_tokens` | 路由訊號與 `min_context_tokens` 過濾；`null` = 未指定，永遠不會被濾掉 |
| `description` | Jev 選的時候讀的就是這段 |
| `jev_role` | 在一個 Jev 路由的系統裡，這個模型是拿來幹嘛的 |
| `avoid_when` | 會被折進 Jev 看到的那段描述裡 |
| `benchmarks` | 實測分數；能力門檻看的就是這些 |
| `enabled` | `false` 把它拿出路由，但不刪掉 |
| `verified` | `false` 表示這個 id 還沒跟供應商對過 |

`thresholds` 放能力門檻，先依類型再依難度分級（`min_tier`、`min_effort`、
`require`、`require_published`）。難度四捨五入到最近的級距，所以 2.49 還留在
第 2 級，要 2.5 才進得了第 3 級。

一個 variant 的成本與延遲，是 base 值被那個 effort 的 `step`（來自 `efforts` 表）
位移後、再夾回 low/medium/high。路由至少需要兩個 variant，不夠時
`registry.load()` 會拋錯。

指到別的登錄檔：`JEV_MODELS=/path/to/models.json`。

## 目錄結構

```
jev_model_router/
  models.json        登錄檔／契約
  __main__.py        CLI（python -m jev_model_router）
  client.py          Jev 傳輸：native + openrouter 兩個 backend、429 backoff
  registry.py        載入與驗證 models.json、展開 effort variant
  router.py          select_model() —— 兩段式的路由決策
  dispatch.py        cli 傳輸 —— 呼叫選中的 variant，不帶金鑰
  tools.py           function-calling schema 與執行器
  verify.py          測試套件（python -m jev_model_router.verify）
```

## 設定

同一個決策有兩條路可以走，回傳的形狀相同：

| backend | 走哪裡 | 金鑰 |
|---|---|---|
| `native`（預設） | `www.jevai.org` 上的 Jev API | `JEV_API_KEY` |
| `openrouter` | OpenRouter 上的 Jev 模型 `~typesafe/jev-latest` | `OPENROUTER_API_KEY` |

常設的選擇寫在專案根目錄的 `jev.json`，會進版控，所以這個路由決定是看得到、
審得到的，而不是藏在 `.env` 裡：

```json
{"backend": "openrouter",
 "modules": {"jev_model_router": {"backend": "openrouter"},
             "jev_workflow": {"backend": "openrouter"}}}
```

臨時跑一次要覆蓋它，不必去動一個已經 commit 的檔案：

```bash
JEV_BACKEND=openrouter python3 -m jev_model_router "..."          # 所有模組
MODEL_ROUTER_BACKEND=openrouter python3 -m jev_model_router "..." # 只有這個
```

每一個 `JEV_*` 設定都可以只針對這個模組回答，前面加上套件名即可：
`MODEL_ROUTER_API_KEY`、`MODEL_ROUTER_BASE_URL`、`MODEL_ROUTER_BACKEND`。

優先順序：參數 > 模組專屬環境變數 > 共用環境變數 > `jev.json` 的
`modules.<模組>` > `jev.json` 頂層 > `native`。每次執行都會印出這次走的是哪一條、
以及這個決定從哪裡來：

```
backend:    openrouter  (from jev.json modules.jev_model_router.backend)
```

金鑰寫進 `jev.json` 會被拒絕——那個檔案會進版控，金鑰留在 `.env`。要釘 Jev 的
版本，用 `JEV_OPENROUTER_MODEL`，不要用 `-latest`。

OpenRouter 只提供通用的 `{state, questions}` 決策端點，沒有 Jev 的具名 preset，
所以這個模組用到的那一個 preset（`model-route`）在這裡被寫成它本來的那組問題，
回程再把扁平的答案組回來。有一個檢查會在這個模組呼叫到沒有對應翻譯的端點時讓
整套測試失敗，所以兩個 backend 不會默默走岔。

**一個刻意的差別：** OpenRouter 的決策題型只有 `choice | score | noul`，沒有
自由文字，所以 `guidance` 在那個 backend 上會是空的，並標記
`guidance_source: "unavailable_on_openrouter"`。不會在本地編一個出來填。

## 驗證

```bash
python3 -m jev_model_router.verify              # 離線，不打 API
python3 -m jev_model_router.verify --models     # 每個模型＋effort，各發一次真實 CLI 呼叫
python3 -m jev_model_router.verify --live       # 真實的 Jev 路由檢查
python3 -m jev_model_router.verify --all        # 全部
```

`JEV_TEST_GAP` 設定兩次真實 Jev 呼叫之間的間隔（預設 45 秒）。

Jev API 會對突發流量限流，而且不送 `Retry-After`。`client.post()` 遇到 429 會用
指數退避重試三次；持續的突發還是會失敗。
