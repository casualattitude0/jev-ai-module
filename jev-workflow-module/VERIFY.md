# jev-workflow-module 路由判準驗證

60 筆真實使用者輸入（涵蓋全部 33 個工作流、中英文、語意相近的對照組），
以真實 Jev 決策呼叫（backend `openrouter`）逐筆路由，比對預期與實際選到的工作流。

## 一段式（預設形狀，單一 choice 問題涵蓋 33 個工作流）

| Input | 預期 workflow | 實際 workflow | 判定 |
|---|---|---|---|
| 我在想主角到底要不要有二段跳，先跟你聊聊看方向 | /brainstorm | /brainstorm | PASS |
| not sure if we should go roguelike or metroidvania -… | /brainstorm | /brainstorm | PASS |
| 大家對格擋窗口的理解都不一樣，把這個機制正式寫成規格 | /game-design | /game-design | PASS |
| write down exactly how the parry system works before… | /game-design | /game-design | PASS |
| 第三關沒有地方教玩家用鉤爪，重新排一下關卡的動線跟敵… | /level-design | /level-design | PASS |
| the mine level sags in the middle, lay out the encou… | /level-design | /level-design | PASS |
| 店主需要二十句左右的待機台詞，語氣要毒舌一點 | /narrative-design | /narrative-design | PASS |
| write the lore for the sunken city and the boss's di… | /narrative-design | /narrative-design | PASS |
| 玩家從暫停畫面找不到背包，整個選單流程要重想 | /ux-design | /ux-design | PASS |
| decide what the HUD shows during combat and how the… | /ux-design | /ux-design | PASS |
| 第 12 關的王太痛了，重新調一下傷害公式跟掉寶率 | /data-design | /data-design | PASS |
| rebalance the shop prices and the xp curve, I want t… | /data-design | /data-design | PASS |
| 美術風格一直很亂，先把配色跟造型規則寫成美術聖經 | /define-art | /define-art | PASS |
| nobody agrees what the game should look like; write… | /define-art | /define-art | PASS |
| 每個畫面的按鈕都長不一樣，需要一套字級、間距跟元件狀… | /define-ui | /define-ui | PASS |
| 音效的質感還沒定調，先訂出材質、力度跟頻率預算的準則 | /define-sound | /define-sound | PASS |
| decide the character of our sound effects before any… | /define-sound | /define-sound | PASS |
| 配樂方向還沒決定，先定樂器編制、調式跟速度的基調 | /define-music | /define-music | PASS |
| 存檔格式要先決定，順便把模組邊界想清楚再開始寫 | /tech-design | /tech-design | PASS |
| we need to pick a netcode model for 4-player co-op;… | /tech-design | /tech-design | PASS |
| 按著跳躍鍵的時候二段跳不會觸發，修一下 | /coding-game | /coding-game | PASS |
| implement dash-cancel on attacks in the player contr… | /coding-game | /coding-game | PASS |
| 設計師每次都要手動改貼圖的匯入設定，幫他們做一個匯入… | /coding-tools | /coding-tools | PASS |
| write an asset validator that runs in the editor bef… | /coding-tools | /coding-tools | PASS |
| 盤點一下還缺哪些 2D 美術，列成清單 | /asset-audit-art | /asset-audit-art | PASS |
| 各個畫面的 icon 跟九宮格還缺哪些，列出來 | /asset-audit-ui | /asset-audit-ui | PASS |
| 哪些技能還沒有特效，幫我列一份清單 | /asset-audit-vfx | /asset-audit-vfx | PASS |
| 場景還有多少地方站在白模上？列出還需要的 3D 模型 | /asset-audit-model | /asset-audit-model | PASS |
| which character animations are still missing | /asset-audit-anim | /asset-audit-anim | PASS |
| list every sound effect this build still needs | /asset-audit-sfx | /asset-audit-sfx | PASS |
| what music cues do we still need? make me a checklis… | /asset-audit-music | /asset-audit-music | PASS |
| 清單上的角色立繪都畫出來 | /art-generate | /art-generate | PASS |
| 照清單把 icon 跟按鈕各狀態的圖都產出來 | /ui-generate | /ui-generate | PASS |
| 把清單上的打擊特效跟拖尾做出來 | /vfx-generate | /vfx-generate | PASS |
| model the props on the checklist, stay inside the po… | /model-generate | /model-generate | PASS |
| make the idle and run clips for the goblin from the… | /anim-generate | /anim-generate | PASS |
| 照音效清單把腳步聲跟揮劍聲做出來 | /sfx-generate | /sfx-generate | PASS |
| compose the three town themes on the music checklist | /music-generate | /music-generate | PASS |
| 新的圖都做好了放在 staging，幫我匯進專案把佔位圖換掉 | /apply-assets | /apply-assets | PASS |
| wire the finished icons into the project and fix the… | /apply-assets | /apply-assets | PASS |
| 這版打起來爽不爽？先玩一輪跟我講感覺 | /playtest | /playtest | PASS |
| play the new build and tell me where it drags | /playtest | /playtest | PASS |
| 這個 bug 要能穩定重現，順便補一個回歸測試 | /qa-test | /qa-test | PASS |
| write automated tests for the save/load path before… | /qa-test | /qa-test | PASS |
| 進戰鬥的時候會頓一下，量一下是什麼吃掉 frame time | /perf-profile | /perf-profile | PASS |
| load times are twelve seconds; measure where it all… | /perf-profile | /perf-profile | PASS |
| 要出日文版，字串抽一抽送翻譯，記得看排版會不會爆 | /localize | /localize | PASS |
| extract the hardcoded strings and check the font cov… | /localize | /localize | PASS |
| 要出一版給測試者，打包、簽章、版號都弄好 | /build-release | /build-release | PASS |
| cut a release candidate for Steam | /build-release | /build-release | PASS |
| 角色的待機動作還沒做完 | /asset-audit-anim 或 /anim-generate | /anim-generate | PASS |
| 美術聖經已經寫好了，可以開始畫圖了 | /asset-audit-art 或 /art-generate | /art-generate | PASS |
| 音效要重做，整體質感不對 | /define-sound 或 /asset-audit-sfx | /define-sound | PASS |
| UI 很亂，每個畫面都不一樣 | /define-ui 或 /ux-design 或 /asset-audit-ui | /define-ui | PASS |
| 關卡編輯器的預覽視窗會閃爍，修一下 | /coding-tools | /coding-tools | PASS |
| 把主選單的背景圖畫出來 | /art-generate 或 /ui-generate | /ui-generate | PASS |
| 把現有的英文台詞翻成繁體中文 | /localize | /localize | PASS |
| 傷害公式已經定案了，把它接進程式裡 | /coding-game | /coding-game | PASS |
| the game feels slow | /playtest 或 /perf-profile | /playtest | PASS |
| 戰鬥音樂聽起來很單薄，想換個方向 | /define-music 或 /asset-audit-music 或 /music-generate | /define-music | PASS |

## 兩段式（`--two-step`：先選階段，再選該階段裡的工作流）

| Input | 預期 workflow | 實際 workflow | 判定 |
|---|---|---|---|
| 我在想主角到底要不要有二段跳，先跟你聊聊看方向 | /brainstorm | /brainstorm | PASS |
| not sure if we should go roguelike or metroidvania -… | /brainstorm | /brainstorm | PASS |
| 大家對格擋窗口的理解都不一樣，把這個機制正式寫成規格 | /game-design | /game-design | PASS |
| write down exactly how the parry system works before… | /game-design | /tech-design | FAIL |
| 第三關沒有地方教玩家用鉤爪，重新排一下關卡的動線跟敵… | /level-design | /level-design | PASS |
| the mine level sags in the middle, lay out the encou… | /level-design | /level-design | PASS |
| 店主需要二十句左右的待機台詞，語氣要毒舌一點 | /narrative-design | /narrative-design | PASS |
| write the lore for the sunken city and the boss's di… | /narrative-design | /narrative-design | PASS |
| 玩家從暫停畫面找不到背包，整個選單流程要重想 | /ux-design | /ux-design | PASS |
| decide what the HUD shows during combat and how the… | /ux-design | /ux-design | PASS |
| 第 12 關的王太痛了，重新調一下傷害公式跟掉寶率 | /data-design | /data-design | PASS |
| rebalance the shop prices and the xp curve, I want t… | /data-design | /data-design | PASS |
| 美術風格一直很亂，先把配色跟造型規則寫成美術聖經 | /define-art | /define-art | PASS |
| nobody agrees what the game should look like; write… | /define-art | /define-art | PASS |
| 每個畫面的按鈕都長不一樣，需要一套字級、間距跟元件狀… | /define-ui | /define-ui | PASS |
| 音效的質感還沒定調，先訂出材質、力度跟頻率預算的準則 | /define-sound | /define-sound | PASS |
| decide the character of our sound effects before any… | /define-sound | /define-sound | PASS |
| 配樂方向還沒決定，先定樂器編制、調式跟速度的基調 | /define-music | /define-music | PASS |
| 存檔格式要先決定，順便把模組邊界想清楚再開始寫 | /tech-design | /tech-design | PASS |
| we need to pick a netcode model for 4-player co-op;… | /tech-design | /tech-design | PASS |
| 按著跳躍鍵的時候二段跳不會觸發，修一下 | /coding-game | /coding-game | PASS |
| implement dash-cancel on attacks in the player contr… | /coding-game | /coding-game | PASS |
| 設計師每次都要手動改貼圖的匯入設定，幫他們做一個匯入… | /coding-tools | /coding-tools | PASS |
| write an asset validator that runs in the editor bef… | /coding-tools | /coding-tools | PASS |
| 盤點一下還缺哪些 2D 美術，列成清單 | /asset-audit-art | /asset-audit-art | PASS |
| 各個畫面的 icon 跟九宮格還缺哪些，列出來 | /asset-audit-ui | /asset-audit-ui | PASS |
| 哪些技能還沒有特效，幫我列一份清單 | /asset-audit-vfx | /asset-audit-vfx | PASS |
| 場景還有多少地方站在白模上？列出還需要的 3D 模型 | /asset-audit-model | /asset-audit-model | PASS |
| which character animations are still missing | /asset-audit-anim | /asset-audit-anim | PASS |
| list every sound effect this build still needs | /asset-audit-sfx | /asset-audit-sfx | PASS |
| what music cues do we still need? make me a checklis… | /asset-audit-music | /asset-audit-music | PASS |
| 清單上的角色立繪都畫出來 | /art-generate | /art-generate | PASS |
| 照清單把 icon 跟按鈕各狀態的圖都產出來 | /ui-generate | /ui-generate | PASS |
| 把清單上的打擊特效跟拖尾做出來 | /vfx-generate | /vfx-generate | PASS |
| model the props on the checklist, stay inside the po… | /model-generate | /model-generate | PASS |
| make the idle and run clips for the goblin from the… | /anim-generate | /anim-generate | PASS |
| 照音效清單把腳步聲跟揮劍聲做出來 | /sfx-generate | /sfx-generate | PASS |
| compose the three town themes on the music checklist | /music-generate | /music-generate | PASS |
| 新的圖都做好了放在 staging，幫我匯進專案把佔位圖換掉 | /apply-assets | /apply-assets | PASS |
| wire the finished icons into the project and fix the… | /apply-assets | /apply-assets | PASS |
| 這版打起來爽不爽？先玩一輪跟我講感覺 | /playtest | /playtest | PASS |
| play the new build and tell me where it drags | /playtest | /playtest | PASS |
| 這個 bug 要能穩定重現，順便補一個回歸測試 | /qa-test | /qa-test | PASS |
| write automated tests for the save/load path before… | /qa-test | /qa-test | PASS |
| 進戰鬥的時候會頓一下，量一下是什麼吃掉 frame time | /perf-profile | /perf-profile | PASS |
| load times are twelve seconds; measure where it all… | /perf-profile | /perf-profile | PASS |
| 要出日文版，字串抽一抽送翻譯，記得看排版會不會爆 | /localize | /localize | PASS |
| extract the hardcoded strings and check the font cov… | /localize | /localize | PASS |
| 要出一版給測試者，打包、簽章、版號都弄好 | /build-release | /build-release | PASS |
| cut a release candidate for Steam | /build-release | /build-release | PASS |
| 角色的待機動作還沒做完 | /asset-audit-anim 或 /anim-generate | /anim-generate | PASS |
| 美術聖經已經寫好了，可以開始畫圖了 | /asset-audit-art 或 /art-generate | /art-generate | PASS |
| 音效要重做，整體質感不對 | /define-sound 或 /asset-audit-sfx | /sfx-generate | FAIL |
| UI 很亂，每個畫面都不一樣 | /define-ui 或 /ux-design 或 /asset-audit-ui | /define-ui | PASS |
| 關卡編輯器的預覽視窗會閃爍，修一下 | /coding-tools | /coding-tools | PASS |
| 把主選單的背景圖畫出來 | /art-generate 或 /ui-generate | /ui-generate | PASS |
| 把現有的英文台詞翻成繁體中文 | /localize | /localize | PASS |
| 傷害公式已經定案了，把它接進程式裡 | /coding-game | /coding-game | PASS |
| the game feels slow | /playtest 或 /perf-profile | /playtest | PASS |
| 戰鬥音樂聽起來很單薄，想換個方向 | /define-music 或 /asset-audit-music 或 /music-generate | /define-music | PASS |

---

一段式 60/60 = 100%，平均 0.57s／次。
兩段式 58/60 = 97%，平均 1.00s／次；兩筆 FAIL 都是第一題選錯階段。
另有 3 筆明顯不屬於任何工作流的輸入（訂會議室、查天氣、退費客訴）不計分，兩種形狀都落在 `/brainstorm`。
