# ntu_easy_llm：現況盤點與 0.5.0 加了什麼（2026-11-10）

> 為了讓 SYNOX（可散布的 Telegram agent host）用 ntu_easy_llm 當 LLM 那一層而寫。
> 只在本機 commit；**沒有 push、沒有 GitHub release**——發佈由擁有者決定。

## 1. 0.4.3 有什麼（clone 下來讀程式＋跑測試確認）

| 項目 | 現況 |
|---|---|
| provider | ChatGPT（OpenAI **Responses API**，`responses.create`）、Gemini（`google-genai` SDK）、Anthropic（`anthropic` SDK） |
| 呼叫方式 | `ask_chatgpt/ask_gemini/ask_anthropic(prompt, model_name, …)`、`ask(service_provider, api_key, prompt, model_name, web_search)`、`ask_many`、`LLMSession`、`ChatGPTAdapter` 等 |
| 回傳 | 一律純 `str` |
| 多輪 | 內部 `_call_*` 吃 message list，但只有 `LLMSession` 用到；公開的 `ask` 只收一個 prompt |
| 串流（streaming） | 沒有 |
| JSON mode | 沒有 |
| **工具呼叫（tool calling）** | **沒有**（只有 OpenAI 的 `web_search` 內建工具） |
| 用量（usage）／花費 | 沒有回傳 |
| 自訂 `base_url`（公司閘道、Ollama） | 沒有（`OpenAI(api_key=…)` 寫死官方端點） |
| 金鑰 | `.env` 往上找＋AES／RSA（`KeyMaterial`）；`ask` 可以直接給 key |
| 載入 | PEP 562 lazy；但 `core/utils.py` 一 import 就載入三家 SDK |
| 測試 | `tests/test_public_api.py`（23 支，不連網）；`smoke_test.py --live` 才打真的 API |
| 發佈方式 | 本機 `builder__whl.ps1`／`.bat`（`python -m build`），把 `dist/*.whl` 上傳成 GitHub **pre-release**，tag 叫 `ntu_easy_llm-<版本>`（0.1.0、0.3.0、0.4.3） |

ClawBot 怎麼用（`src/clawbot/core/llm_provider.py`，只讀）：只呼叫 `ask(service_provider=…, api_key=…, prompt=…, model_name=…)`，
多輪是自己把訊息攤平成一段 prompt。`requirements.txt` 用 release 的 wheel 網址裝。

## 2. SYNOX 需要什麼

SYNOX 的 agent 迴圈要：多輪訊息（system／user／assistant／tool）、工具定義（JSON Schema）、AI 回的工具呼叫（名字＋參數 dict＋id）、
token 用量（記到 `/ai_usage`）、四種來源（OpenAI、Anthropic API、Gemini、**相容端點**：公司閘道／Ollama，要能給 `base_url`、金鑰可空）、
一個「測一下」（`probe`），以及**看得懂的錯誤**（401／404／429／連不到）讓它翻成中文。測試不能連網，所以要能把 HTTP 換成假的。

0.4.3 缺：工具呼叫、用量、`base_url`、型別化的錯誤、可注入的 HTTP。

## 3. 0.5.0 加了什麼（舊 API 一個字都沒改）

新模組 `ntu_easy_llm/agent.py`（`ntu_easy_llm.agent`，lazy，不 import 任何 SDK）：

```python
chat(provider, model, messages, tools=None, *, api_key="", base_url=None, max_tokens=1024,
     temperature=None, timeout=90.0, session=None, extra_headers=None, extra_body=None) -> ChatReply
probe(provider, model, *, api_key="", base_url=None, timeout=30.0, session=None, max_tokens=256) -> str | None

ChatReply(text, tool_calls: list[ToolCall], usage: Usage, raw, stop_reason)
ToolCall(id, name, arguments: dict)      Tool(name, description, parameters)      Usage(input_tokens, output_tokens)
LLMError ← LLMHTTPError(.status, .body, .provider) / LLMConnectionError / LLMResponseError
normalize_provider()、to_openai_messages()、to_anthropic_messages()、PROVIDERS、DEFAULT_BASE_URLS
```

| provider | 怎麼打 |
|---|---|
| `openai` | `https://api.openai.com/v1/chat/completions`；長度上限送 `max_completion_tokens`（新模型不收 `max_tokens`） |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`（同一把 Gemini key） |
| `openai_compatible` | `<base_url>/chat/completions`，**base_url 必填**；送 `max_tokens`；金鑰空就不送 Authorization |
| `anthropic` | `https://api.anthropic.com/v1/messages`，原生 tool use；system 拆出來、連續工具結果併成一則 user |

別名：`chatgpt`→openai、`claude`→anthropic、`ollama`／`openai_compat`→openai_compatible（不分大小寫）。

設計決定：

- **直接打 HTTP（stdlib urllib），不經三家 SDK。** 理由：相容端點（公司閘道、Ollama）只保證 `/chat/completions` 的 JSON 形狀，
  一條程式路徑通吃；`import ntu_easy_llm.agent` 不會載入 openai／anthropic／google-genai（有測試鎖住）；測試用 `session=` 注入假的。
  舊的 `ask*` 仍然走 SDK，沒動。
- 用 Chat Completions 而不是 OpenAI Responses API：工具呼叫的格式是業界共通的那一份，Gemini、Ollama、閘道都吃。
- 不讀 `.env`：agent host 自己管金鑰（SYNOX 放保管箱）。
- `probe` 的 `max_tokens` 預設 256：推理模型會先花 token 想，上限太小會回空字串、被誤判成「接不通」。

沒做（之後要再加）：串流、JSON mode／structured output、圖片輸入、重試與退避（交給呼叫端）、花費換算（只回 token 數）、
Gemini 原生 function calling（走相容端點就夠）。

## 4. 版本、tag、wheel

- 版本 `0.4.3` → **`0.5.0`**（semver minor：只加東西）。CHANGELOG 補了原本漏掉的 `[0.4.3]` 標題。
- 本機 tag：任務要求的 `v0.5.0`，另外照這個 repo 既有慣例也打 `ntu_easy_llm-0.5.0`（release 網址用的是這個名字）。
- wheel：`dist\ntu_easy_llm-0.5.0-py3-none-any.whl`（`.venv` 裡 `python -m build`；`dist/` 在 `.gitignore`）。
- 發佈時（擁有者做）：`git push origin master --tags`，再
  `gh release create ntu_easy_llm-0.5.0 dist\ntu_easy_llm-0.5.0-py3-none-any.whl dist\ntu_easy_llm-0.5.0.tar.gz --prerelease -R ChouYangEn0401/ntu_easy_llm`。
  之後的下載網址：
  `https://github.com/ChouYangEn0401/ntu_easy_llm/releases/download/ntu_easy_llm-0.5.0/ntu_easy_llm-0.5.0-py3-none-any.whl`
