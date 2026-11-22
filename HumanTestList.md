# HumanTestList

要人親手測的項目（真的服務、真的金鑰、別台電腦）。電腦自己能跑的檢查在 `VerifyList.json`。

## 待測

HTTP adapter（2026-11-23 加入）只用假的伺服器測過。下面每一項先寫一份設定檔（金鑰用 `api_key_env` 指到環境變數），再跑命令列。
看到的錯誤字串跟架構文件第 5.4 節標【推測】的不一樣時，把實際的內文記下來（遮掉金鑰）交回來改規則。

- [ ] 2026-11-23 · LM Studio · 有模型的電腦開 LM Studio 伺服器 → `ntu_easy_llm models --config X --profile <lmstudio>` → `probe` → `probe --send` → `chat --stream "你好"` · 列表有 `[loaded]`／`[not loaded]`；`probe` 不會讓 LM Studio 載入模型；`chat` 有回覆、邊收邊印
- [ ] 2026-11-23 · LM Studio 超過 context · 用很小的 context 載入模型，送一段很長的文字 · 錯誤是 `[context_too_long]`
- [ ] 2026-11-23 · Ollama · `models`（已載入的標 `[loaded]`）→ `chat`（設 `options.context_size`）→ 帶工具的模型與不支援工具的模型各試一次（用 Python 呼叫 `chat(..., tools=[...])`）· 不支援工具的回 `tools_unsupported`；沒拉過的模型回 `model_not_found`
- [ ] 2026-11-23 · vLLM／llama.cpp（有的話）· `probe --send`、`chat --stream`；沒開工具旗標時帶工具呼叫 · 回 `tools_unsupported`
- [ ] 2026-11-23 · OpenAI／Anthropic／Gemini 真金鑰 · 各跑 `probe`、`probe --send`、`chat --stream` · 都通；Gemini 的 `models` 列出的 id 沒有 `models/` 前綴
- [ ] 2026-11-23 · 錯誤分類 · 故意給錯的金鑰（三家各一次）；有機會遇到額度用完或 429 時記下內文 · 錯金鑰是 `auth_invalid`；額度用完是 `quota_exhausted`、一般 429 是 `rate_limited`
- [ ] 2026-11-23 · proxy · 在開著系統 proxy 或 VPN 軟體的電腦上，對同一台或區網的 LM Studio／Ollama 跑 `probe` · 照樣連得到（本機與區網不走 proxy）

## 測過了
