# HumanTestList

要人親手測的項目（真的服務、真的金鑰、別台電腦）。電腦自己能跑的檢查在 `VerifyList.json`。

## 待測

（0.6.0.dev0 只有介面與離線測試，沒有要人測的項目；真的 HTTP adapter 加進來後，在這裡補「用真的金鑰跑 probe／chat」的項目。）

- [ ] 2026-11-23 · LLOP 來源（`remote/api_key/llop`）接真的 LLOP 伺服器 · 在有 LLOP 帳號的電腦裝 `ntu_easy_llm[llop]`（LLOP SDK 從它的 release wheel 或原始碼裝）→ `llmop client enroll …` 建好設定檔 → 用 `{"source": "remote/api_key/llop", "llop_profile": "<設定檔名>", "model": "any"}` 跑 `list_models()`、`probe(send=True)`、`chat()` · 列得出模型、probe ok、chat 回得出字；把連結改成錯的時得到 `kind == link_changed`
- [ ] 2026-11-23 · 三個訂閱來源（claude／github_copilot／chatgpt）接這台已登入的官方 CLI · 裝對應的登入套件 → 各建一個 `remote/subscription/<名稱>` profile → `probe()`、`probe(send=True)`、`chat([{"role": "user", "content": "一句話介紹台北"}])` · 回得出字；登出後呼叫得到 `kind == auth_invalid`
- [ ] 2026-11-23 · 備援路由接真的來源 · 設一條 `mode: auto` 的 chain（第一個成員故意用失效的金鑰環境變數，第二個用能通的來源）→ `ps.router("<chain>").chat(...)` · 回覆來自第二個成員，`notice` 寫「已改用 …（原因：金鑰失效）」

## 測過了
