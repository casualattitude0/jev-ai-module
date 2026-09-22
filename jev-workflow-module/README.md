# jev-workflow-module

決定一個請求該交給哪個工作流。判斷交給 [Jev decision API](https://www.jevai.org/docs)，
回傳的是**目標的接口**——指令、entry point、system prompt 路徑、需要的輸入、
下一步是誰——讓主程式收到後自己去執行。

這個模組只做決策，不執行任何工作流。怎麼叫起一個 skill／agent／tool 只有宿主
知道，硬塞進這裡只會讓兩邊都難改。

Stdlib only，零依賴。

## 產線：33 個工作流，10 個階段

| 階段 | 工作流 |
|---|---|
| `discuss` | `/brainstorm` |
| `design` | `/game-design` `/level-design` `/narrative-design` `/ux-design` `/data-design` |
| `define` | `/define-art` `/define-ui` `/define-sound` `/define-music` |
| `architect` | `/tech-design` |
| `build` | `/coding-game` `/coding-tools` |
| `audit` | `/asset-audit-art` `/asset-audit-ui` `/asset-audit-vfx` `/asset-audit-model` `/asset-audit-anim` `/asset-audit-sfx` `/asset-audit-music` |
| `generate` | `/art-generate` `/ui-generate` `/vfx-generate` `/model-generate` `/anim-generate` `/sfx-generate` `/music-generate` |
| `integrate` | `/apply-assets` |
| `verify` | `/playtest` `/qa-test` `/perf-profile` |
| `ship` | `/localize` `/build-release` |

階段只是順序與收窄用的分組；路由選的是工作流，不是階段。請求往回跳是正常的
（做到一半發現美術設定要重寫），唯一的硬規則是 `depends_on` 不能指向更後面的階段。

資產是一個對稱矩陣：**7 種資產（art / ui / vfx / model / anim / sfx / music）
各有一個 audit 和一個 generate**，有測試守著這件事。先有清單才有產出，這個
順序寫在每個工作流的 `avoid_when` 裡，不是靠開發者記得。

## 一個工作流 = 一個資料夾

```
jev_workflow/workflows/
  stages.json
  <id>/
    module.json          # 接口宣告
    system_prompt.md     # 內容（現在是空的）
```

資料夾是**被發現**的，不是被註冊的：丟一個新資料夾進去就多一個路由選項，
刪掉就少一個，沒有中央清單要同步維護。每個工作流之後要長出自己的 system
prompt、skill、tool，全部住在自己那一格路徑裡，互不干擾。

33 個資料夾目前都只有接口，`system_prompt.md` 是空殼。路由器已經能正確選到
它們，`implemented: false` 會一路顯示到 CLI 和回傳值上——沒做的事是**看得見**
的缺口，不是沉默的失敗。

## 用法

```bash
cp .env.example .env          # repo 根目錄那一份,填入 JEV_API_KEY
cd jev-workflow-module
python3 -m jev_workflow "角色的待機動作還沒做完"
```

`JEV_API_KEY` / `JEV_BASE_URL` 是所有模組共用的;若只要讓這個模組走別的
endpoint 或別的金鑰,在根目錄的 `.env` 用 `WORKFLOW_API_KEY`、
`WORKFLOW_BASE_URL`,有前綴的優先。

決策可以走兩個 backend:`native`(Jev API,預設)或 `openrouter`
(OpenRouter 上的 `~typesafe/jev-latest`,需要 `OPENROUTER_API_KEY`)。
常設的選擇寫在專案根目錄的 `jev.json`(`modules.jev_workflow.backend`),
臨時要換再用 `JEV_BACKEND`(全部)或 `WORKFLOW_BACKEND`(只有這個模組)覆蓋。
這個模組只問通用的 `{state, questions}` 決策,兩個 backend 都直接支援,
中間不做任何轉換。

```bash
JEV_BACKEND=openrouter python3 -m jev_workflow "角色的待機動作還沒做完"
```

```
route:      audit (1) -> asset-audit-anim (0.97)
workflow:   Audit animation needs  (asset-audit-anim)
command:    /asset-audit-anim
stage:      audit
entry:      skill (unimplemented)
dir:        .../jev_workflow/workflows/asset-audit-anim
inputs:     game design spec, the model checklist, the project tree
next:       /anim-generate
confidence: 0.97
note:       nothing is implemented behind /asset-audit-anim yet; write system_prompt.md in the directory above
```

當成函式庫，主程式拿 `interface` 去執行：

```python
from jev_workflow import select_workflow

route = select_workflow("粒子特效要做一輪",
                        context={"asset-audit-vfx": "missing"})

iface = route.interface
iface["command"]                      # '/vfx-generate'
iface["entry"]["system_prompt_path"]  # 絕對路徑，宿主自己讀
iface["inputs"]                       # 這個工作流需要什麼
iface["next_steps"]                   # ['apply-assets'] — 做完可以接誰
iface["implemented"]                  # False -> 還沒有東西可跑
route.blocked_by                      # ['asset-audit-vfx'] — 你說還沒做的前置
```

收窄選項：

```python
select_workflow(req, stage="generate")              # 只在某個階段裡選
select_workflow(req, allow=["playtest", "/qa-test"])
select_workflow(req, stakes="high")                 # 選錯的代價
select_workflow(req, two_step=True)                 # 強制先選階段
```

收窄到剩一個選項會直接拒絕（並告訴你該直接呼叫哪個指令），而不是假裝做了決策。

```bash
python3 -m jev_workflow --list              # 依階段列出全部
python3 -m jev_workflow --list --stage audit
python3 -m jev_workflow --show coding-game  # 單一工作流的接口
python3 -m jev_workflow --two-step "..."    # 或 --one-shot
```

## 兩段式路由

目錄長大之後，「一次把 33 個近鄰分清楚」本身就是個難題，而且 Jev 的 body
上限是 32 KiB，每個工作流的 description 約 530 bytes。所以路由有兩種形狀：

- **一段式**：一個 `choice` 問題涵蓋全部工作流。
- **兩段式**：先問這是產線的哪個階段（10 選 1），再問那個階段裡的哪個工作流
  （最多 7 選 1）。

`two_step=None`（預設）會自己選：payload 超過 `MAX_PAYLOAD_BYTES`（20 KiB）
就切成兩段。目前 33 個工作流是 17.5 KB，落在一段式。門檻是照著實測結果定的，
不是猜的——見 [result.md](result.md)。

兩段式選到只有一個工作流的階段（`discuss`／`architect`／`integrate`）時，
**不會**再問第二個問題：階段是 Jev 自己選的，那就是決策，不是呼叫端的過濾。
`route.steps` 會記錄整條決策路徑。

## module.json 的欄位

| 欄位 | 意思 |
|---|---|
| `id` | 必須等於資料夾名稱 |
| `command` | 必須是 `/<id>` |
| `stage` | 必須在 `stages.json` 裡 |
| `description` | **Jev 讀的就是這段**，決策品質全靠它 |
| `when_to_use` / `avoid_when` | 跟隔壁工作流的邊界 |
| `inputs` / `outputs` | 宿主要準備什麼、會拿到什麼 |
| `depends_on` | 誰的產出是它的輸入（不可以指向更後面的階段） |
| `entry` | `kind`、`ref`、`system_prompt`、`tools` — 宿主執行時要的東西 |

`description` 寫不好，路由就不會準。載入時會驗證：id 與資料夾不符、command
與 id 不符、未知階段、指向不存在或更後面的 `depends_on`、壞掉的 JSON，全部
拒絕載入，不會讓半個壞掉的工作流被端上去當選項。

指到別的工作流集合：`WORKFLOW_MODULES=/path/to/workflows`。

## 前置條件是報告，不是閘門

`depends_on` 會反過來生出 `next_steps`（誰接在你後面），也會生出 `blocked_by`：

```python
select_workflow(req, context={"define-art": "missing"}).blocked_by
# ['define-art']
```

規則只有一條：**沒提到的就是不知道，不是沒做**。你沒在 `context` 裡講的前置
不會被報成缺，因為路由器沒有讀你的專案，不該假裝知道。回報的缺也只是資訊——
路由照樣給出目標，要不要因此停下來是宿主的決定，不是藏在 filter 裡的行為。

## 幾個刻意的決定

**路由器不執行。** 回傳接口而不是結果，宿主才能決定同步或非同步、要不要先
跟使用者確認、要不要串成一條。決策與執行混在一起的路由器，換執行方式就得改
決策邏輯。

**決策一定落在候選名單內。** Jev 若回了名單外的 id（階段或工作流都一樣），
直接拋錯，不會把一個從未提供過的目標交給宿主去跑。

**問的是 `choice`，不是 `model-route`。** 用 Jev 的 native
`/api/v1/decisions`，因為候選是工作流不是模型。回傳的 answer 把值放在
`choice` 欄位（不是 `decision`），`_answer()` 會把兩種形狀都正規化掉。

## 沒有放進來的東西

刻意留在外面，需要的話各補一個資料夾就好：

- **營運面**：live-ops、活動排程、商城與變現、玩家客訴。這些是營運不是開發。
- **bug triage**：`/playtest` 產出的發現和 `/qa-test` 的失敗報告已經涵蓋，
  再切一個只會跟兩邊搶邊界。
- **`/asset-audit-text`**：字串抽取被放在 `/localize` 裡一起做，因為抽字串和
  翻譯回填在實務上是同一條管線。

**如果專案是 2D**：刪掉 `model-generate`、`anim-generate`、`asset-audit-model`、
`asset-audit-anim` 四個資料夾即可，測試會繼續是綠的（資產矩陣的對稱性檢查看的
是 audit 與 generate 有沒有配對，不是有幾種資產）。

## 驗證

```bash
python3 -m jev_workflow.verify          # 35 個離線檢查，不打網路
python3 -m jev_workflow.verify --live   # 再加 21 條真實路由
```

離線那層必須永遠是綠的。`--live` 會受 Jev 的可用性影響，也是 description
寫得夠不夠分得開的實測。
