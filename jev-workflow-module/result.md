# jev-workflow-module Review 結果

兩輪 review。第一輪是離線驗證（契約、載入器、邊界值），第二輪是**真實 OpenRouter
呼叫**的判準評測（給一段真實使用者輸入，它選出來的流程正不正確）。
本文所有段落都標明是「離線」還是「live 實測」。

- 離線層：`python3 -m jev_workflow.verify` — **49 passed, 0 failed**（不打網路）
- live 層：backend = `openrouter`（`~typesafe/jev-latest`），共約 420 次真實決策呼叫
- 最終判準成績：**一段式 60/60（100%）、兩段式 58/60（97%）**

---

## 一、分析依據

### 離線（讀碼與資料）

- 讀完 `README.md` 全文，逐條抽出它對外宣告的契約：33 個工作流／10 個階段的對照表、
  `module.json` 的欄位規則、「載入時會驗證：id 與資料夾不符、command 與 id 不符、
  未知階段、指向不存在或更後面的 `depends_on`、壞掉的 JSON，全部拒絕載入」、
  兩段式門檻、`blocked_by` 的「沒提到的就是不知道」、2D 專案刪四個資料夾、
  以及「離線檢查 / 21 條真實路由」。
- 讀完 `jev_workflow/` 下全部 .py，確認角色分工：`registry` 負責「發現＋驗證＋算出
  interface」、`router` 負責「問 Jev 一次或兩次、把答案框在候選名單內」、`tools`
  是 function-calling 的薄殼、`client` 是 native／openrouter 兩個 backend 的傳輸層。
- 讀 `workflows/stages.json`（version 2，10 階段與 order）與 33 個 `module.json`
  （全量載入後列印 stage／depends_on 圖譜比對）。
- 以 `copytree` 複製 workflows 到暫存目錄後刻意破壞（id、command、stage、缺欄位、
  壞 JSON、entry.kind、dangling／倒流 `depends_on`、只剩一個工作流、刪四個 3D 資料夾），
  再 `R.load()` 看是否照契約拒絕。
- 資料完整性專項：33 個資料夾全部都有 `module.json`；`entry.system_prompt` 宣告的
  `system_prompt.md` 全部存在且非空（內含 TODO，所以 `implemented` 一律 false，
  與 README 的「33 個都只有接口」一致）；沒有孤兒資料夾、沒有多餘檔案；
  10 個階段全部至少有一個工作流；audit／generate 對 7 種資產完全對稱。

### live（真實 OpenRouter 呼叫）

- 設定來自 repo 根目錄 `jev.json`（`modules.jev_workflow.backend = openrouter`）與
  `.env` 的 `OPENROUTER_API_KEY`；另外用 `JEV_BACKEND=native` 打了一次 www.jevai.org
  做交叉比對。
- 自訂評測集 63 筆（見第三節），33 個工作流每個至少一筆，中英文各半，
  另含 10 筆難分辨對照組與 3 筆明顯不屬於任何 workflow 的輸入。
- 每筆都用真實呼叫跑過一段式與兩段式，記錄實得 command、stage、confidence、
  是否走兩段式、`blocked_by` 與耗時。
- 針對「階段判斷」單獨做 A/B：改動前後各跑 3 回合 × 60 筆，確認差異不是雜訊。

---

## 二、離線 Input -> Output（第一輪，不打網路）

### 1. 離線驗證套件
- Input：`python3 -m jev_workflow.verify`
- 預期：全綠，且檢查數與 README 宣稱一致。
- 實際：修正前 `48 passed, 0 failed`（README 寫 35）；補上新檢查後 `49 passed, 0 failed`。
- 判定：功能 PASS，README 數字 FAIL（已修）。

### 2. registry 全量載入
- Input：`R.load()`
- 預期：33 個工作流、10 個階段，stage 分布與 README 表格相同。
- 實際：33／10，逐階段比對與 README 表格完全相同。
- 判定：PASS

### 3. system_prompt 與資料夾完整性
- Input：對每個 manifest 跑 `R.system_prompt_path()`、列出資料夾內容。
- 實際：33/33 都找得到且非空；沒有「有資料夾沒 module.json」；沒有多餘檔案。
- 判定：PASS

### 4. `--show` 正常路徑與未知名稱
- Input：`--show coding-game` / `--show no-such-workflow`
- 實際：前者印出 interface JSON（絕對路徑的 `system_prompt_path`、
  `next_steps` = perf-profile／playtest／qa-test、`implemented: false`）；
  後者印錯誤並 exit=1。
- 判定：PASS

### 5. `--list` 與階段過濾
- Input：`--list`、`--list --stage audit`、`--list --stage nosuch`
- 實際：`33 workflows in 10 stages, 0 implemented`／`7 workflows in 1 stages`／
  未知階段印 `0 workflows in 0 stages` 且 exit=0（安靜的空結果；`--list` 只是展示，
  不視為缺陷，但有記下來）。
- 判定：PASS

### 6. 壞掉的 module.json（逐項）
- Input：id≠資料夾名、command≠`/id`、未知 stage、缺 `description`／`display_name`、
  壞 JSON、未知 `entry.kind`、dangling `depends_on`、刪掉 `stages.json`、只剩一個工作流。
- 實際：九項全部 RegistryError，訊息都指名是哪個工作流、哪個欄位。
- 判定：PASS

### 7. `depends_on` 指向更後面的階段
- Input：把 brainstorm（discuss, order 1）的 `depends_on` 改成 `['qa-test']`（verify, order 9）。
- 預期：README 與 `stages.json` 的 `_note` 都說這種 manifest 要被拒絕載入。
- 實際（修正前）：**載入成功**，完全沒有檢查 —— 與契約不符。
- 實際（修正後）：`brainstorm: depends_on ['qa-test'] in a later stage; a prerequisite
  may never come after the workflow that needs it`。
- 判定：修正前 FAIL，修正後 PASS

### 8. README 的「2D 專案刪四個資料夾」
- Input：刪掉 `model-generate`、`anim-generate`、`asset-audit-model`、`asset-audit-anim` 再載入。
- 實際（修正前）：`RegistryError: apply-assets: depends_on unknown workflow
  ['model-generate','anim-generate']` —— 與 README 自己「dangling depends_on 會被拒絕」那條矛盾。
- 補充實驗：把那兩個 id 從 `/apply-assets` 的 `depends_on` 拿掉後，29 個工作流載入成功、
  10 階段都不空、audit/generate 對 5 種資產仍然對稱。
- 判定：修正前 FAIL（README 說明不完整），修正後 PASS

### 9. function-calling 介面
- Input：`call('list_workflows', {'stage':'ship'})`、未知工具名、亂丟參數、over-narrow 的 select。
- 實際：`['build-release','localize']`；`unknown tool 'nope'` 附 available；
  `TypeError: ...unexpected keyword argument 'bogus'`；RegistryError 被包成 `{"error": ...}`。
- 判定：PASS

### 10. 參數與收窄的錯誤路徑（不打網路就擋下）
- Input：`stakes='wild'`、`allow=['playtest']`、`allow=[]`、`stage='nosuch'`、CLI 空請求。
- 實際：`stakes must be one of ...`；`only /playtest matches, so call it directly`；
  `nothing matches`（兩者）；CLI 印 usage 並 exit=1。
- 判定：PASS

---

## 三、live 判準評測：評測集與逐筆結果

### 評測集設計（63 筆，含 ground truth）

- **正常路徑 50 筆**：33 個工作流每個至少一筆，其中 17 個各給中英文兩種口吻。
- **難分辨對照組 10 筆**（ground truth 是「合理候選集合」）：
  audit vs generate（`角色的待機動作還沒做完`、`美術聖經已經寫好了，可以開始畫圖了`）、
  define vs audit（`音效要重做，整體質感不對`）、
  UI 三兄弟（`UI 很亂，每個畫面都不一樣`：define-ui / ux-design / asset-audit-ui）、
  coding-game vs coding-tools（`關卡編輯器的預覽視窗會閃爍`）、
  art vs ui generate（`把主選單的背景圖畫出來`）、
  narrative vs localize（`把現有的英文台詞翻成繁體中文`）、
  data-design vs coding-game（`傷害公式已經定案了，把它接進程式裡`）、
  playtest vs perf-profile（`the game feels slow`）、
  music 三段（`戰鬥音樂聽起來很單薄，想換個方向`）。
- **明顯不屬於任何 workflow 3 筆**：訂會議室、查天氣、玩家退費客訴
  （README 明講營運面刻意不收）。這三筆不計入準確率，只觀察它會不會硬選一個。

判定規則：實得 workflow 落在 ground truth 集合內算 PASS。下表的「一段式」是預設形狀
（payload 17.4 KB < 20 KiB 門檻），「兩段式」是 `two_step=True` 強制。

### 逐筆 Input -> Output（全部為真實 OpenRouter 呼叫，最終版程式碼）

| # | 使用者輸入 | ground truth | 一段式實得 | conf | 秒 | 兩段式實得 (stage) | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | 我在想主角到底要不要有二段跳，先跟你聊聊看方向 | brainstorm | /brainstorm | 1 | 0.55 | /brainstorm (discuss) | PASS / PASS |
| 2 | not sure if we should go roguelike or metroidvania -- want to kick it around? | brainstorm | /brainstorm | 1 | 0.55 | /brainstorm (discuss) | PASS / PASS |
| 3 | 大家對格擋窗口的理解都不一樣，把這個機制正式寫成規格 | game-design | /game-design | 0.97 | 0.45 | /game-design (design) | PASS / PASS |
| 4 | write down exactly how the parry system works before anyone codes it | game-design | /game-design | 0.92 | 0.47 | /tech-design (architect) | PASS / FAIL |
| 5 | 第三關沒有地方教玩家用鉤爪，重新排一下關卡的動線跟敵人配置 | level-design | /level-design | 1 | 0.56 | /level-design (design) | PASS / PASS |
| 6 | the mine level sags in the middle, lay out the encounters again | level-design | /level-design | 1 | 0.52 | /level-design (design) | PASS / PASS |
| 7 | 店主需要二十句左右的待機台詞，語氣要毒舌一點 | narrative-design | /narrative-design | 1 | 0.52 | /narrative-design (design) | PASS / PASS |
| 8 | write the lore for the sunken city and the boss's dialogue lines | narrative-design | /narrative-design | 1 | 0.59 | /narrative-design (design) | PASS / PASS |
| 9 | 玩家從暫停畫面找不到背包，整個選單流程要重想 | ux-design | /ux-design | 1 | 0.54 | /ux-design (design) | PASS / PASS |
| 10 | decide what the HUD shows during combat and how the pause menu is structured | ux-design | /ux-design | 1 | 0.55 | /ux-design (design) | PASS / PASS |
| 11 | 第 12 關的王太痛了，重新調一下傷害公式跟掉寶率 | data-design | /data-design | 0.98 | 0.5 | /data-design (design) | PASS / PASS |
| 12 | rebalance the shop prices and the xp curve, I want the tuning table | data-design | /data-design | 1 | 0.55 | /data-design (design) | PASS / PASS |
| 13 | 美術風格一直很亂，先把配色跟造型規則寫成美術聖經 | define-art | /define-art | 1 | 0.66 | /define-art (define) | PASS / PASS |
| 14 | nobody agrees what the game should look like; write it down once and for all | define-art | /define-art | 0.99 | 0.57 | /define-art (define) | PASS / PASS |
| 15 | 每個畫面的按鈕都長不一樣，需要一套字級、間距跟元件狀態的規範 | define-ui | /define-ui | 1 | 0.53 | /define-ui (define) | PASS / PASS |
| 16 | 音效的質感還沒定調，先訂出材質、力度跟頻率預算的準則 | define-sound | /define-sound | 1 | 0.53 | /define-sound (define) | PASS / PASS |
| 17 | decide the character of our sound effects before anyone makes one | define-sound | /define-sound | 1 | 0.59 | /define-sound (define) | PASS / PASS |
| 18 | 配樂方向還沒決定，先定樂器編制、調式跟速度的基調 | define-music | /define-music | 1 | 0.56 | /define-music (define) | PASS / PASS |
| 19 | 存檔格式要先決定，順便把模組邊界想清楚再開始寫 | tech-design | /tech-design | 1 | 0.45 | /tech-design (architect) | PASS / PASS |
| 20 | we need to pick a netcode model for 4-player co-op; what are the trade-offs | tech-design | /tech-design | 1 | 0.5 | /tech-design (architect) | PASS / PASS |
| 21 | 按著跳躍鍵的時候二段跳不會觸發，修一下 | coding-game | /coding-game | 1 | 0.59 | /coding-game (build) | PASS / PASS |
| 22 | implement dash-cancel on attacks in the player controller | coding-game | /coding-game | 1 | 0.59 | /coding-game (build) | PASS / PASS |
| 23 | 設計師每次都要手動改貼圖的匯入設定，幫他們做一個匯入工具 | coding-tools | /coding-tools | 1 | 0.56 | /coding-tools (build) | PASS / PASS |
| 24 | write an asset validator that runs in the editor before commit | coding-tools | /coding-tools | 1 | 0.62 | /coding-tools (build) | PASS / PASS |
| 25 | 盤點一下還缺哪些 2D 美術，列成清單 | asset-audit-art | /asset-audit-art | 1 | 0.56 | /asset-audit-art (audit) | PASS / PASS |
| 26 | 各個畫面的 icon 跟九宮格還缺哪些，列出來 | asset-audit-ui | /asset-audit-ui | 1 | 0.53 | /asset-audit-ui (audit) | PASS / PASS |
| 27 | 哪些技能還沒有特效，幫我列一份清單 | asset-audit-vfx | /asset-audit-vfx | 1 | 0.59 | /asset-audit-vfx (audit) | PASS / PASS |
| 28 | 場景還有多少地方站在白模上？列出還需要的 3D 模型 | asset-audit-model | /asset-audit-model | 1 | 0.56 | /asset-audit-model (audit) | PASS / PASS |
| 29 | which character animations are still missing | asset-audit-anim | /asset-audit-anim | 1 | 0.49 | /asset-audit-anim (audit) | PASS / PASS |
| 30 | list every sound effect this build still needs | asset-audit-sfx | /asset-audit-sfx | 1 | 0.54 | /asset-audit-sfx (audit) | PASS / PASS |
| 31 | what music cues do we still need? make me a checklist | asset-audit-music | /asset-audit-music | 1 | 0.57 | /asset-audit-music (audit) | PASS / PASS |
| 32 | 清單上的角色立繪都畫出來 | art-generate | /art-generate | 1 | 0.5 | /art-generate (generate) | PASS / PASS |
| 33 | 照清單把 icon 跟按鈕各狀態的圖都產出來 | ui-generate | /ui-generate | 1 | 0.49 | /ui-generate (generate) | PASS / PASS |
| 34 | 把清單上的打擊特效跟拖尾做出來 | vfx-generate | /vfx-generate | 1 | 0.61 | /vfx-generate (generate) | PASS / PASS |
| 35 | model the props on the checklist, stay inside the poly budget | model-generate | /model-generate | 0.94 | 0.48 | /model-generate (generate) | PASS / PASS |
| 36 | make the idle and run clips for the goblin from the checklist | anim-generate | /anim-generate | 1 | 0.62 | /anim-generate (generate) | PASS / PASS |
| 37 | 照音效清單把腳步聲跟揮劍聲做出來 | sfx-generate | /sfx-generate | 1 | 0.75 | /sfx-generate (generate) | PASS / PASS |
| 38 | compose the three town themes on the music checklist | music-generate | /music-generate | 0.89 | 0.56 | /music-generate (generate) | PASS / PASS |
| 39 | 新的圖都做好了放在 staging，幫我匯進專案把佔位圖換掉 | apply-assets | /apply-assets | 1 | 0.66 | /apply-assets (integrate) | PASS / PASS |
| 40 | wire the finished icons into the project and fix their import settings | apply-assets | /apply-assets | 1 | 0.51 | /apply-assets (integrate) | PASS / PASS |
| 41 | 這版打起來爽不爽？先玩一輪跟我講感覺 | playtest | /playtest | 1 | 0.63 | /playtest (verify) | PASS / PASS |
| 42 | play the new build and tell me where it drags | playtest | /playtest | 0.92 | 0.53 | /playtest (verify) | PASS / PASS |
| 43 | 這個 bug 要能穩定重現，順便補一個回歸測試 | qa-test | /qa-test | 0.98 | 0.51 | /qa-test (verify) | PASS / PASS |
| 44 | write automated tests for the save/load path before we release | qa-test | /qa-test | 0.99 | 0.55 | /qa-test (verify) | PASS / PASS |
| 45 | 進戰鬥的時候會頓一下，量一下是什麼吃掉 frame time | perf-profile | /perf-profile | 1 | 0.53 | /perf-profile (verify) | PASS / PASS |
| 46 | load times are twelve seconds; measure where it all goes | perf-profile | /perf-profile | 1 | 0.57 | /perf-profile (verify) | PASS / PASS |
| 47 | 要出日文版，字串抽一抽送翻譯，記得看排版會不會爆 | localize | /localize | 1 | 0.51 | /localize (ship) | PASS / PASS |
| 48 | extract the hardcoded strings and check the font covers Japanese | localize | /localize | 1 | 0.54 | /localize (ship) | PASS / PASS |
| 49 | 要出一版給測試者，打包、簽章、版號都弄好 | build-release | /build-release | 1 | 0.67 | /build-release (ship) | PASS / PASS |
| 50 | cut a release candidate for Steam | build-release | /build-release | 1 | 0.59 | /build-release (ship) | PASS / PASS |
| 51 | 角色的待機動作還沒做完 | asset-audit-anim/anim-generate | /anim-generate | 0.65 | 0.56 | /anim-generate (generate) | PASS / PASS |
| 52 | 美術聖經已經寫好了，可以開始畫圖了 | asset-audit-art/art-generate | /art-generate | 0.91 | 0.66 | /art-generate (generate) | PASS / PASS |
| 53 | 音效要重做，整體質感不對 | define-sound/asset-audit-sfx | /define-sound | 0.59 | 0.59 | /sfx-generate (generate) | PASS / FAIL |
| 54 | UI 很亂，每個畫面都不一樣 | define-ui/ux-design/asset-audit-ui | /define-ui | 0.99 | 0.8 | /define-ui (define) | PASS / PASS |
| 55 | 關卡編輯器的預覽視窗會閃爍，修一下 | coding-tools | /coding-tools | 0.83 | 0.66 | /coding-tools (build) | PASS / PASS |
| 56 | 把主選單的背景圖畫出來 | art-generate/ui-generate | /ui-generate | 0.67 | 0.55 | /ui-generate (generate) | PASS / PASS |
| 57 | 把現有的英文台詞翻成繁體中文 | localize | /localize | 0.98 | 0.5 | /localize (ship) | PASS / PASS |
| 58 | 傷害公式已經定案了，把它接進程式裡 | coding-game | /coding-game | 1 | 0.7 | /coding-game (build) | PASS / PASS |
| 59 | the game feels slow | playtest/perf-profile | /playtest | 0.7 | 0.5 | /playtest (verify) | PASS / PASS |
| 60 | 戰鬥音樂聽起來很單薄，想換個方向 | define-music/asset-audit-music/music-generate | /define-music | 0.84 | 0.6 | /define-music (define) | PASS / PASS |
| 61 | 幫我訂明天下午三點的會議室 | （不屬於任何 workflow） | /brainstorm | 0.57 | 0.6 | /ux-design (design) | — / — |
| 62 | what's the weather in Taipei tomorrow | （不屬於任何 workflow） | /brainstorm | 0.73 | 0.49 | /brainstorm (discuss) | — / — |
| 63 | 玩家客訴要退費，這種案子要怎麼處理？ | （不屬於任何 workflow） | /brainstorm | 0.89 | 0.6 | /brainstorm (discuss) | — / — |
**成績：一段式 60/60 = 100%，兩段式 58/60 = 97%。**
平均延遲：一段式 0.56s（最慢 0.80s），兩段式 1.00s（最慢 2.02s）。
三筆 out-of-scope 一段式全部落在 `/brainstorm`（confidence 0.59 / 0.74 / 0.89）。

同一組輸入在優化後又完整重跑一次（第三輪）：一段式 60/60、兩段式 58/60，
兩筆 FAIL 與上一輪**完全相同**（`write down exactly how the parry system works`
選到 architect、`音效要重做，整體質感不對` 選到 generate），沒有飄動。
逐筆對照表另見 [VERIFY.md](VERIFY.md)。

---

## 四、live 實測：真實回應與模組假設的落差

### 4.1 回傳格式（PASS，沒有落差）
OpenRouter 實際回傳：

```json
{"answers": {"workflow": {"type": "choice", "choice": "asset-audit-anim",
  "probabilities": {"...": 0, "asset-audit-anim": 1}, "confidence": 1}}}
```

- 值放在 `choice` 而不是 `decision`，與 README／`_answer()` 的假設一致。
- native backend（`JEV_BACKEND=native`，www.jevai.org）同一題回
  `/asset-audit-anim`、confidence 1、0.75s，`code/data` 外層也如預期被剝掉。
  兩個 backend 在這個模組上沒有任何形狀差異。
- **小落差**：真實回應**從來沒有** `guidance` 欄位（數百次呼叫都沒有），
  `Route.guidance` 恆為空字串。CLI 只在非空時才印，所以沒有壞掉，
  但 README 範例輸出與 `as_dict()` 裡的 `guidance` 實際上是個永遠空的欄位。
  沒有改動——空欄位是 API 的事實，不是模組的 bug。
- **小落差**：回應沒有 usage／cost 欄位，所以成本只能從呼叫次數與 payload 推估，
  模組本身無從記錄。

### 4.2 決策一定落在候選名單內（PASS）
約 540 次真實呼叫（含階段 A/B 與第三輪重跑）中，**沒有任何一次**回傳候選名單外的
id：`stage=generate` 收窄後不會吐 audit 的工作流，`allow=['playtest','/qa-test']`
收窄後只在這兩個之間選。`_ask()` 的 off-menu 防線在真實流量下沒有被觸發過，
但它擋的是 API 的行為改變，仍然該留著。

### 4.3 payload 與延遲（PASS）
- 一段式 payload 實測 17.4 KB（含請求文字），在 32 KiB 硬上限與 20 KiB 切換門檻之下，
  延遲 0.45–0.80s，沒有因為體積變慢的跡象。
- 兩段式是兩次呼叫，總延遲約 1.0s（單一工作流階段短路時只有一次，約 0.5s），
  也就是說目前規模下**一段式又準又快**，預設選一段式是對的。

### 4.4 blocked_by 的語意（PASS）
真實路由 `把清單上的待機動畫做出來` → `/anim-generate`：
- `context={'asset-audit-anim':'missing','model-generate':'done'}` → `blocked_by == ['asset-audit-anim']`
- `context={'model-generate':'done'}`（沒提 asset-audit-anim）→ `blocked_by == []`

「沒提到的就是不知道，不是沒做」在真實回應下成立，而且 blocked 不影響路由結果
（兩次都照樣給出 `/anim-generate`），與 README 的「前置條件是報告，不是閘門」一致。

### 4.5 單一工作流階段短路（PASS）
真實兩段式 `把 staging 的圖匯進專案` → 階段 `integrate`（confidence 1）後直接回
`/apply-assets`，`route.steps` 只有一筆 `stage`，沒有發第二次呼叫。
整份評測中 discuss／architect／integrate 共短路 10 次，全部只發一次呼叫。

### 4.6 模型對 33 個工作流的辨識度（**這是唯一有系統性問題的地方**）
- **一段式（預設形狀）**：60/60，7 組資產的 audit↔generate、define↔audit、
  coding-game↔coding-tools、narrative↔localize、playtest↔perf-profile
  全部分得開，連只有一句話的模糊輸入也落在合理候選內。
  這代表 33 個 `module.json` 的 `description`／`when_to_use`／`avoid_when`
  已經把邊界寫得夠清楚。
- **兩段式**：錯誤**全部發生在第一題（選階段）**，選錯階段之後第二題再準也救不回來。
  混淆配對（改動前，3 回合 × 60 筆）：

  | 混淆 | 次數/3 回合 | 代表輸入 |
  |---|---|---|
  | `design` -> `architect` | 6/6 | `把這個機制正式寫成規格`、`write down exactly how the parry system works before anyone codes it` |
  | `ship` -> `audit`／`build` | 3/3 | `extract the hardcoded strings and check the font covers Japanese` |
  | `define`/`audit` -> `generate` | 3/3 | `音效要重做，整體質感不對`（本來就模稜兩可） |

  最常被挑錯的 stage 是 `design`（被 `architect` 吃掉）與 `ship`（被 `audit`／`build` 吃掉）。
  原因在於第一題只看得到「階段描述 + 階段裡的工作流名字與指令」，
  `Game design spec (/game-design)` 這種標籤不足以對抗 "before anyone codes it"
  這種字面訊號。`registry.stage_candidates()` 的 docstring 自己就寫了：
  「A stage's description has to carry what is inside it, or the first step is
  choosing between labels rather than between kinds of work.」——實測證明它當時
  還沒做到這件事。

---

## 五、優化：做了什麼、以及被實測否決的嘗試

### 5.1 採用：階段選項帶上每個工作流的第一句描述
`registry.stage_candidates()` 原本只列 `顯示名稱 (/command)`，改成
`顯示名稱 (/command) -- 第一句描述`（新增 `registry.gist()`）。
階段題 payload 2.7 KB -> 6.7 KB，仍遠低於 32 KiB。

A/B（同一組 60 筆、各 3 回合、只量階段題）：

| 版本 | rep0 | rep1 | rep2 | 階段命中率 |
|---|---|---|---|---|
| 改動前 | 56/60 | 56/60 | 56/60 | **93%** |
| 加上第一句描述 | 58/60 | 58/60 | 58/60 | **97%** |

端到端兩段式：56/60（93%）-> 58/60（97%），第三輪重跑仍是 58/60 且 FAIL 的兩筆
一模一樣。一段式不受影響（階段文字不在它的 payload 裡），重跑仍是 60/60。
殘留的兩筆是 `write down exactly how the parry system works before anyone codes it`
（design/architect 的真實灰色地帶）與 `音效要重做，整體質感不對`（輸入本身就模稜兩可）。

同時把 `verify.py` 既有的「stage options carry their contents」檢查加嚴：
現在要求階段描述不只提到 `/command`，還要包含那個工作流的第一句話，
否則離線就 FAIL——這條規則以後不會靠人記得。

### 5.2 被實測否決（已還原，留下記錄）
1. **改寫 `stages.json` 四個階段描述**（design/architect/audit/ship 各補一句邊界）。
   A/B：改動後 54/56/55（90–93%），與改動前 56/56/56（93%）在雜訊內，
   而且新引入 `角色的待機動作還沒做完` -> `build` 的錯誤。**已 `git checkout` 還原。**
2. **在 `STAGE_INSTRUCTIONS` 加一句「寫下遊戲做什麼是決策階段」**。
   端到端兩段式 56/60 -> 55/60，而且把 `compose the three town themes`
   推去 `design` 這種原本沒有的錯。**已還原。**

沒有動任何 `module.json` 的描述文字：一段式 60/60 證明工作流層級的邊界已經夠清楚，
錯誤集中在階段層級，改工作流描述是改錯地方。

### 5.3 觀察到但刻意不改：out-of-scope 沒有棄權出口
三筆明顯不屬於任何 workflow 的輸入（訂會議室、查天氣、玩家退費客訴）
一段式全部被硬塞到 `/brainstorm`，confidence 0.59／0.74／0.89。
confidence 有一點訊號（是整份評測裡最低的一段），但**不可分**：
退費客訴拿到 0.89，比某些正確答案還高。

這不是 bug：README 明說「決策一定落在候選名單內」，也明說營運面刻意不收，
模組沒有承諾棄權。但宿主如果把這個路由器接在使用者輸入的最前面，
必須自己先做一層「這是不是遊戲開發請求」的守門，光看 confidence 擋不住。
加一個「以上皆非」候選會改變模組對外的決策契約，超出 review 範圍，因此只記錄不動手。

---

## 六、修正項目彙總

1. `jev_workflow/registry.py` — `load()`（第一輪，離線）
   - 問題：README 與 `stages.json` 的 `_note` 都宣告「`depends_on` 不可以指向更後面的
     階段，否則拒絕載入」，但 `load()` 只檢查 dangling，完全沒有檢查階段順序；
     `verify.py` 的 `deps_flow_forward` 只看現有資料、不看載入器，所以測試照樣全綠。
   - 修法：在既有的 `depends_on` 檢查迴圈裡用 `stages[...]["order"]` 建 id -> order 表，
     把倒流的依賴一併拒絕。同階段互相依賴（`anim-generate` -> `model-generate`）仍允許。

2. `jev_workflow/verify.py` — 拒絕清單（第一輪，離線）
   - 在既有的 `_rejects` 表格加一列 `("a depends_on pointing at a later stage", ...)`，
     守住上面那條規則。離線檢查 48 -> 49。

3. `jev_workflow/registry.py` — `stage_candidates()` 與新的 `gist()`（第二輪，live 驅動）
   - 問題：兩段式的第一題只給階段名與工作流標籤，`design` 被 `architect` 系統性吃掉。
   - 修法：階段描述帶上每個工作流的第一句話。階段命中率 93% -> 97%（3 回合穩定）。

4. `jev_workflow/verify.py` — 「stage options carry their contents」加嚴（第二輪）
   - 現在要求階段描述包含每個工作流的 `gist()`，不只是 `/command`。

5. `README.md`
   - 「2D 專案刪四個資料夾即可」與實際不符（`/apply-assets` 的 `depends_on` 仍指向
     被刪掉的工作流，載入會被拒絕）：補上「同時要把那兩個 id 從 `/apply-assets`
     的 `depends_on` 拿掉」與原因。
   - 「35 個離線檢查」：實際是 49。已更正。
   - 「目前 33 個工作流是 17.5 KB」：實測 17.1 KB。已更正。
   - CLI 範例輸出少了一定會印的 `backend:` 行，已補上。
   - 兩段式章節補上階段選項現在會帶工作流第一句描述（6.7 KB）與實測數字。

6. `VERIFY.md`（新檔，第三輪）
   - 一段式／兩段式各一張逐筆對照表（Input｜預期｜實際｜PASS/FAIL），給人快速掃過用；
     分析與修正記錄留在本檔。

沒有發現 crash、孤兒或缺漏的工作流、manifest 與程式不一致；
路由器的候選框限、兩段式短路、`blocked_by` 的保守語意，在離線與真實呼叫下都與 README 一致。
