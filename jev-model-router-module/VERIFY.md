# 路由品質驗證

15 筆真實使用者輸入，經真實 OpenRouter 後端（`~typesafe/jev-latest`）跑 `select_model()`，比對預期與實選的模型。
重跑指令：`python3 -m jev_model_router "<input>"`；完整分析見 `result.md`。

| Input | 預期 model | 實際 model | 判定 |
|---|---|---|---|
| 修 README.md 的錯字 `recieve`→`receive` | fast（luna / haiku） | `gpt-5.6-luna@low` | PASS |
| 對 src/ 跑 formatter 統一風格 | fast（luna / haiku） | `claude-haiku-4-5@low` | PASS |
| 寫一行 shell 數每個 .py 的行數 | fast（luna / haiku） | `gpt-5.6-luna@low` | PASS |
| 寫 ISO-8601 duration 解析函式＋單元測試 | fast–balanced（haiku / sonnet） | `claude-sonnet-5@medium` | PASS |
| 測試 expected 90 got 100，折扣算在稅後，修掉 | fast–balanced（haiku / sonnet） | `claude-haiku-4-5@low` | PASS |
| 為 GET /orders 加 cursor 分頁並更新測試 | fast–balanced（haiku / sonnet） | `claude-sonnet-5@medium` | PASS |
| 跨 40 檔重構 auth，需求模糊、不能斷 session | deep（opus） | `claude-opus-5@xhigh` | PASS |
| 設計 5 億文件近似去重演算法，p99 < 200ms | deep（opus） | `claude-opus-5@high` | PASS |
| 決定是否拆微服務並寫 ADR | deep（opus） | `claude-opus-5@xhigh` | PASS |
| 從生產 log dump 找根因（600K tokens） | 視窗夠且 recall 夠（terra / sol / opus / sonnet） | `gpt-5.6-terra@medium` | PASS |
| 摘要整個 repo dump 列五大風險（900K tokens） | 視窗夠且 recall 夠（terra / sol / opus / sonnet） | `gpt-5.6-terra@medium` | PASS |
| 把預設時區從 local 改成 UTC | balanced–deep（sonnet / opus） | `claude-opus-5@high` | PASS |
| 啟動崩潰 `ModuleNotFoundError: 'requests'` | fast（luna / haiku） | `gpt-5.6-luna@low` | PASS |
| 「把 dashboard 弄快一點。」 | balanced–deep（sonnet / opus） | `claude-sonnet-5@medium` | PASS |
| 寫 migration 砍掉正式環境 users_old 表 | balanced–deep（sonnet / opus） | `claude-opus-5@high` | PASS |

準確率 15/15（100%），OVER 0 筆、UNDER 0 筆。
與上一輪對照：kind 分類 15/15 相同，difficulty 最大飄動 0.05，11/15 選出完全相同的 variant。
4 筆有飄動且皆仍 PASS——2 筆只換 effort（ISO-8601 解析 low→medium；去重演算法 xhigh→high，因 difficulty 2.50→2.46 跨過級距邊界），2 筆換模型（折扣修正 sonnet→haiku，repo dump sonnet→terra）。
