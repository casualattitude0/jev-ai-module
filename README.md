# jev-ai-module

> 一組自足的後端模組:決定要用哪個模型、請求該進哪個工作流,
> 以及一段內容裡有沒有個資與資安問題。

每個模組都是自足的:複製單一資料夾到目標專案即可使用,不互相依賴。

## 模組

### 1. jev-model-router-module — 根據任務選擇模型

依任務的複雜度與風險,決定該用哪個模型、以及該用多少推理力度(effort),
然後實際把輸入送過去執行。判斷由 [Jev decision API](https://www.jevai.org/docs)
負責,它回傳的是型別化的決策與機率,不是自由文字。

一個「選項」是一組 **(模型, effort)** 配對,id 形如 `claude-opus-5@high`。
Effort 與模型同時決定,因為「Sonnet 深思」和「Opus 快答」在成本與延遲上
是兩個真正不同的選擇,不該事後才補。

```bash
cp .env.example .env          # 整個 repo 一份,填入 JEV_API_KEY
cd jev-model-router-module
python3 -m jev_model_router "把 billing 的重試邏輯重構掉"
```

```
model:      Claude 5 Opus  (claude-opus-5)
effort:     xhigh
variant:    claude-opus-5@xhigh
tier:       deep via anthropic  cost=high latency=high context=1,000,000
confidence: 0.63
```

當成函式庫:

```python
from jev_model_router import select_model, serve

sel = select_model("port the parser to async", stakes="high")
print(sel.variant_id, sel.confidence)
print(serve(sel, "port the parser to async, here is the file: ..."))
```

也提供 function-calling 介面(Anthropic / OpenAI 兩種格式),
讓 agent 自己決定要不要換模型:`select_model` 只做決策,
`route_and_serve` 決策後直接執行。

決策本身有兩個 backend,回傳的形狀相同:`native` 走 Jev API(`JEV_API_KEY`),
`openrouter` 走 OpenRouter 上的同一個 Jev 模型 `~typesafe/jev-latest`
(`OPENROUTER_API_KEY`)。OpenRouter 只提供通用的 `{state, questions}` 端點、
沒有 Jev 的 preset,所以每個 preset 在這裡被寫成它本來的那組問題,回程再組回
原本的扁平欄位;唯一的差別是 OpenRouter 沒有自由文字題型,`guidance` 會是空的。

決策之外,實際執行模型只有一條路:該模型自己的 CLI(`claude`、`codex`),
用它自己登入的 session。**絕不會用 API key 呼叫 Claude 或 GPT**,三道保證:
這裡沒有任何通往各家 API 的 HTTP 路徑、也不讀任何金鑰;呼叫 CLI 前會把環境裡
所有 API key 清掉(CLI 找得到 `ANTHROPIC_API_KEY` 就會拿來認證,那等於繞路做
同一件被禁止的事);`OPENROUTER_API_KEY` 只能打 Jev,每次呼叫前都會檢查 slug,
`JEV_OPENROUTER_MODEL` 指向 `openai/*` 或 `anthropic/*` 會被拒絕。

三個模組的套件目錄同一套命名:`jev-<名稱>-module/jev_<名稱>` ——
`jev_model_router`、`jev_workflow`、`jev_data_policy`。環境變數的模組前綴
就是套件名去掉共用的 `jev_`:`MODEL_ROUTER_*`、`WORKFLOW_*`、`DATA_POLICY_*`,
這樣有前綴的名字不會看起來像共用的 `JEV_*`。

每個模組走哪一條路,寫在根目錄的 [`jev.json`](jev.json) —— 這是專案決定,
不是機密,所以放在看得到、進得了版控的地方,而不是藏在 `.env` 裡:

```json
{
  "backend": "openrouter",
  "modules": {
    "jev_model_router": { "backend": "openrouter" },
    "jev_workflow":     { "backend": "openrouter" },
    "jev_data_policy":  { "backend": "openrouter" }
  }
}
```

目前三個模組都走 `openrouter`;把任一行改成 `native` 就只換那一個模組。

每個模組的每一個設定都走同一個 resolver,不只是 backend:

| 設定 | 共用名 | 模組專屬名 | `jev.json` |
|---|---|---|---|
| 決策走哪條路 | `JEV_BACKEND` | `MODEL_ROUTER_BACKEND` / `WORKFLOW_BACKEND` / `DATA_POLICY_BACKEND` | `backend` |
| Jev endpoint | `JEV_BASE_URL` | `MODEL_ROUTER_BASE_URL` … | `base_url` |
| 模型登錄檔 | `JEV_MODELS` | `MODEL_ROUTER_MODELS` | `models` |
| 工作流目錄 | `JEV_MODULES` | `WORKFLOW_MODULES` | `modules` |
| 敏感度等級表 | `JEV_DATA_POLICY` | `DATA_POLICY_DATA_POLICY` | `data_policy` |
| CLI 逾時 | `JEV_CLI_TIMEOUT` | `MODEL_ROUTER_CLI_TIMEOUT` | `cli_timeout` |

金鑰是唯一的例外,只能從 `.env` 讀:名字裡有 `key`/`token`/`secret`/`password`
的鍵寫進 `jev.json` 會被拒絕,因為那個檔案會進版控。

優先順序:參數 > `MODEL_ROUTER_BACKEND` / `WORKFLOW_BACKEND` /
`DATA_POLICY_BACKEND` >
`JEV_BACKEND` > `jev.json` 的 `modules.<模組>` > `jev.json` 頂層 > 內建預設。
環境變數贏過檔案,所以臨時跑一次不必去動一個已經 commit 的檔案;
金鑰則相反,寫在 `jev.json` 會被拒絕(那個檔案會進 git),只能放 `.env`。

每次執行都會印出這次走的是哪一條、以及這個決定從哪裡來:

```
backend:    openrouter  (from jev.json modules.jev_model_router.backend)
```

```bash
OPENROUTER_API_KEY=sk-or-v1-...   # 放在 repo 根目錄的 .env
JEV_BACKEND=openrouter python3 -m jev_model_router "把 billing 的重試邏輯重構掉"
MODEL_ROUTER_BACKEND=openrouter python3 -m jev_model_router "..."   # 只有這個模組
```

純 stdlib,零依賴。詳見 [jev-model-router-module/README.md](jev-model-router-module/README.md)。

### 2. jev-data-policy-module — 判斷內容有沒有個資與資安問題

只做一件事:看一段內容,說出它的敏感度等級 — `public` / `internal` /
`confidential` / `regulated`。等級的定義是**內容裡有什麼**(可識別到個人的
資料、憑證、受法規保護的資料),不是誰能收。

它不認識模型、不認識廠商、沒有核准清單:「confidential 能不能送去某處」是
你的合約與風險的決定,該住在那些東西旁邊,而不是住在讀文字的這顆裡面。

```bash
python3 -m jev_data_policy "客戶 A123456789 的帳單地址是..."
python3 -m jev_data_policy --json "..."
python3 -m jev_data_policy --classes        # 看梯子,不打 API
cat suspect.log | python3 -m jev_data_policy
```

```
backend:    openrouter  (from jev.json modules.jev_data_policy.backend)
content:    102 bytes
class:      confidential
confidence: 1.00
            confidential=1.00  internal=0.00  public=0.00  regulated=0.00
```

```python
from jev_data_policy import classify

c = classify("user wang.mei@example.com, phone 0912-345-678")
c.data_class            # 'confidential'
c.at_least("internal")  # True
```

判斷由 Jev 的 `choice` 決策負責,回的是等級與機率 —— Jev 回型別化的決策,
不回自由文字,所以沒有誠實的方式讓它列出「是哪一段文字洩漏的」。能給的是
等級,所以給的就是等級。

但「這裡面有沒有個資」用機率回答是錯的形狀,所以不確定時**只往上、不往下**:

```python
c = classify(text, min_confidence=0.9)
c.data_class       # 'confidential'
c.escalated_from   # 'public' —— Jev 原本說的,留在紀錄裡
```

其他失敗也都往同一個方向倒:Jev 掛掉會拋例外而不是回 `public`;回一個梯子上
沒有的等級會被拒絕;超過 16 KiB 的內容是拒絕而不是截斷 —— 截斷正是一份
regulated 的內容變成 public 的方式。

要分類就得把那段文字送給 Jev,這是問問題的代價,設計不掉:內容本身不能出門的
話,傳一段描述而不是原文。詳見
[jev-data-policy-module/README.md](jev-data-policy-module/README.md)。

### 3. jev-workflow-module — 決定請求該進哪個工作流

一個請求進來，決定它該交給哪個工作流 — 從 `/brainstorm`、`/game-design`、
`/coding-game` 到 `/asset-audit-*`、`/*-generate`、`/playtest`、`/build-release`。
回傳的是**目標的接口**（指令、entry point、system prompt 路徑、需要的輸入、
下一步是誰），讓主程式收到後自己執行 — 這個模組只決策，不執行。

一個工作流是一個資料夾，被發現而不是被註冊：`jev_workflow/workflows/<id>/module.json`
宣告接口，之後的 system prompt、skill、tool 都住在同一格路徑裡，可以單獨維護。
目前 33 個工作流分布在 10 個階段，全部只有接口，`implemented: false` 會一路顯示出來。

```bash
cd jev-workflow-module
python3 -m jev_workflow "角色的待機動作還沒做完"
python3 -m jev_workflow --list
```

```
route:      audit (1) -> asset-audit-anim (0.97)
workflow:   Audit animation needs  (asset-audit-anim)
command:    /asset-audit-anim
entry:      skill (unimplemented)
next:       /anim-generate
```

目錄大到一次問不完時，路由會自動切成兩段（先選階段、再選階段內的工作流）：

```python
from jev_workflow import select_workflow

route = select_workflow("粒子特效要做一輪", context={"asset-audit-vfx": "missing"})
route.interface["command"]       # '/vfx-generate'
route.interface["next_steps"]    # ['apply-assets']
route.blocked_by                 # ['asset-audit-vfx'] — 你說還沒做的前置
route.interface["implemented"]   # False -> 還沒有東西可跑
```

用 Jev 的 native `/api/v1/decisions` 問 `choice` 問題，候選的 `criteria`
就是每個工作流自己的 description。純 stdlib，零依賴。詳見
[jev-workflow-module/README.md](jev-workflow-module/README.md)。

## 共通慣例

- **自足**:每個模組把設定檔(如 `models.json`)放在 package 內,用相對路徑解析。
- **設定優先序**:環境變數 > 從 cwd 一路往上找到的第一個 `.env` > package 內的
  `.env`。往上找,所以 `cd` 進模組資料夾跑也讀得到 repo 根目錄那份;package 內
  那層墊底,所以宿主專案的設定永遠蓋得過模組預設。
- **零依賴優先**:能用 stdlib 就不引入套件,避免把 transitive dependency
  帶進宿主專案。
- **一份 `.env`、一份 `.gitignore`**:都在 repo 根目錄,模組不各自留一份。
  金鑰只放 `.env`(不進版控),範本放 `.env.example`。
- **模組不互相 import**:每個模組只回答自己那一個問題,把結果交給呼叫端;
  真的需要組合時用依賴注入(把對方的函式當參數傳),
  讓每個模組都能單獨複製、單獨測試。

## 開發

```bash
cd <module>
python3 -m jev_model_router.verify           # 離線,47 項,不打 API
python3 -m jev_model_router.verify --all     # 含實際 API 與 CLI 呼叫
python3 -m jev_workflow.verify                 # 離線,35 項
python3 -m jev_workflow.verify --live          # 再加真實路由(對限流敏感)
python3 -m jev_data_policy.verify              # 離線,32 項,transport 上 stub
python3 -m jev_data_policy.verify --live       # 再加 6 筆真實分類
```

每個模組都把自己的驗證依據寫在 `result.md` 裡 —— 實際輸入、實際輸出、
以及**沒有**驗過的部分:
[model router](jev-model-router-module/result.md)、
[data policy](jev-data-policy-module/result.md)、
[workflow](jev-workflow-module/result.md)。

每個模組的測試分成三層:離線(純邏輯)、per-model(對每個模型發真實呼叫)、
live(打 Jev API 驗證決策行為)。離線那層必須永遠是綠的;
後兩層會受外部服務的限流與可用性影響。
