# jev-ai-module

> 一組自足的後端模組:決定要用哪個模型,以及哪些資料准許送出去。

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
python3 -m jevagentrouter "把 billing 的重試邏輯重構掉"
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
from jevagentrouter import select_model, serve

sel = select_model("port the parser to async", stakes="high")
print(sel.variant_id, sel.confidence)
print(serve(sel, "port the parser to async, here is the file: ..."))
```

也提供 function-calling 介面(Anthropic / OpenAI 兩種格式),
讓 agent 自己決定要不要換模型:`select_model` 只做決策,
`route_and_serve` 決策後直接執行。

純 stdlib,零依賴。詳見 [jev-model-router-module/README.md](jev-model-router-module/README.md)。

### 2. jev-data-policy-module — 決定誰能接收哪種敏感度的資料

確定性的資料分級過濾:哪些對象獲准接收 `public` / `internal` / `confidential` /
`regulated` 的資料。不認識模型、不認識路由、不認識任何特定廠商。

敏感度不該是交給別的系統權衡的偏好 — 「個資能不能送出去」用機率回答是錯的工具。
所以它在對方看到選項之前就先移除,而不是變成評分裡的一個權重。

```bash
python3 -m jevpolicy                             # 看整份政策
python3 -m jevpolicy --class internal            # 誰獲准
python3 -m jevpolicy --check jevai.org internal  # 單一判斷
```

兩個模組互不 import。政策模組算出獲准清單,當成 router 的 `allow` 傳進去:

```python
from jevpolicy import guarded_call
from jevagentrouter import select_model

sel = guarded_call(select_model, data_class="internal",
                   service="jevai.org",     # 這個呼叫會把任務文字送過去
                   task="reconcile an inventory sync bug", stakes="medium")
```

出廠時**所有對象都只核准 `public`**,`approved_by` 為空。往上放行是法務與資安的
決定,這個 repo 不可能知道你跟供應商簽了什麼。詳見
[jev-data-policy-module/README.md](jev-data-policy-module/README.md)。

### 3. jev-workflow-module — 決定請求該進哪個工作流

一個請求進來，決定它該交給哪個工作流 — 從 `/brainstorm`、`/game-design`、
`/coding-game` 到 `/asset-audit-*`、`/*-generate`、`/playtest`、`/build-release`。
回傳的是**目標的接口**（指令、entry point、system prompt 路徑、需要的輸入、
下一步是誰），讓主程式收到後自己執行 — 這個模組只決策，不執行。

一個工作流是一個資料夾，被發現而不是被註冊：`wfrouter/workflows/<id>/module.json`
宣告接口，之後的 system prompt、skill、tool 都住在同一格路徑裡，可以單獨維護。
目前 33 個工作流分布在 10 個階段，全部只有接口，`implemented: false` 會一路顯示出來。

```bash
cd jev-workflow-module
python3 -m wfrouter "角色的待機動作還沒做完"
python3 -m wfrouter --list
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
from wfrouter import select_workflow

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
- **模組不互相 import**:需要組合時用依賴注入(把對方的函式當參數傳),
  讓每個模組都能單獨複製、單獨測試。

## 開發

```bash
cd <module>
python3 -m jevagentrouter.verify           # 離線,47 項,不打 API
python3 -m jevagentrouter.verify --all     # 含實際 API 與 CLI 呼叫
python3 -m wfrouter.verify                 # 離線,35 項
python3 -m wfrouter.verify --live          # 再加真實路由(對限流敏感)
python3 -m jevpolicy.verify                # 離線,22 項,沒有 live 層
```

每個模組都把自己的驗證依據寫在 `result.md` 裡 —— 實際輸入、實際輸出、
以及**沒有**驗過的部分:
[model router](jev-model-router-module/result.md)、
[data policy](jev-data-policy-module/result.md)、
[workflow](jev-workflow-module/result.md)。

每個模組的測試分成三層:離線(純邏輯)、per-model(對每個模型發真實呼叫)、
live(打 Jev API 驗證決策行為)。離線那層必須永遠是綠的;
後兩層會受外部服務的限流與可用性影響。
