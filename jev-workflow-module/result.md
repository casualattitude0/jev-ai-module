# Results

Request in : workflow out. Last updated 2026-09-22, 33 workflow modules in 10 stages.

## Routing

13 個工作流時的全綠結果（`--live`，一段式）：

| request | workflow | confidence |
|---|---|---|
| `I'm not sure the dash should cancel attacks -- what do you think?` | `brainstorm` | 0.98 |
| `the double jump doesn't trigger when you hold the button` | `coding-game` | 1.00 |
| `the boss at level 12 kills new players too fast, retune the damage` | `data-design` | 0.99 |
| `nobody agrees what the game should look like; write it down` | `define-art` | 1.00 |
| `list every sound effect this build still needs` | `asset-audit-sfx` | 1.00 |
| `draw all the portraits on the checklist` | `art-generate` | 1.00 |
| `the new icons are sitting in staging, get them into the project` | `apply-assets` | 1.00 |

擴到 33 個之後，節流重測（間隔 12 秒）：

| request | workflow | confidence |
|---|---|---|
| `the shopkeeper needs about twenty lines of idle dialogue` | `narrative-design` | 1.00 |
| `the game hitches every time we enter the market square` | `perf-profile` | 1.00 |
| `cut a release candidate for the Steam beta` | `build-release` | 1.00 |

中文請求同樣可路由：`美術風格現在很亂，先把設定寫下來` → `/define-art` 1.00。

## 一段式 vs 兩段式

23 題兩種形狀各跑一次。**這次實測被 API 的 rate limit 打爛**，兩邊各有一半的
呼叫收到 429／502，所以絕對數字不能當準確率看：

| 形狀 | 完成的呼叫 | 其中正確 | 真正選錯 | Jev 呼叫數 |
|---|---|---|---|---|
| 一段式 | 14 | **14** | 0 | 14 |
| 兩段式 | 17 | 14 | **3** | 32 |

樣本小，但方向是清楚的，而且錯的樣子有意義：兩段式那 3 次**全部錯在第一段的
階段題**，第二段只是忠實地在錯的階段裡挑。

```
'the shopkeeper needs about twenty lines of idle dialogue'
    stage=generate -> sfx-generate        （想要的是 narrative-design）
'write down exactly how the parry window works before anyone codes it'
    stage=architect -> tech-design        （想要的是 game-design）
```

階段選錯就救不回來 — 這是兩段式的固有代價，換來的是 payload 從 17.5 KB
降到 2.3 KB + ≤4 KB。所以**預設維持一段式，兩段式是目錄長大後的退路，不是升級**。
`MAX_PAYLOAD_BYTES = 20 KiB` 的門檻是照這個結論定的：能一次問完就一次問完。

修正動作：把階段描述的邊界寫死（`design` 明講「produces documents and text,
never binary asset files」，`generate` 明講「never written text, design documents
or code」）。台詞那題重測後回到 `narrative-design` 1.00。

`game-design` 與 `tech-design` 的邊界仍然是已知弱點 —「寫清楚 parry window
怎麼運作」會被「before anyone codes it」拉去 architect。等這兩個工作流真的寫
內容時，description 要再收緊。

## Shape

一次決策回傳給主程式的東西：

```json
{
  "workflow": {
    "id": "asset-audit-anim",
    "command": "/asset-audit-anim",
    "stage": "audit",
    "dir": ".../jev_workflow/workflows/asset-audit-anim",
    "entry": {"kind": "skill", "ref": null,
              "system_prompt_path": ".../asset-audit-anim/system_prompt.md",
              "tools": []},
    "inputs": ["game design spec", "the model checklist", "the project tree"],
    "depends_on": ["define-art", "game-design"],
    "next_steps": ["anim-generate"],
    "implemented": false
  },
  "confidence": 0.97,
  "blocked_by": ["define-art"],
  "steps": [{"question": "stage", "decision": "audit", "confidence": 1},
            {"question": "workflow", "decision": "asset-audit-anim", "confidence": 0.97}]
}
```

`implemented: false` 是現況：33 個工作流都只有接口。

## Payload

每個工作流的 description 約 530 bytes，Jev 的 body 上限是 32 KiB：

| 工作流數 | 一段式 payload | 預設形狀 |
|---|---|---|
| 33（現在） | 17.5 KB | 一段式 |
| ~40 | 22 KB | 兩段式 |
| ~60 | 32 KB | 超過硬上限，只能兩段式 |

兩段式：階段題 2.3 KB，最大的階段題（audit／generate 各 7 個）3.9 KB。

## 注意

`--live` 對 rate limit 很敏感。`LIVE_PAUSE = 10` 秒仍可能收到 429，
離線那 35 項才是必須永遠綠的那層。
