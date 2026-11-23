# ntu_easy_llm

第一次用？看 [docs/TUTORIAL.md](docs/TUTORIAL.md)

> **v0.5.0** · Python 3.11+

針對 **ChatGPT**、**Gemini**、**Anthropic Claude** 三大 LLM 服務的輕量 Python 封裝套件。

- **統一介面** — 一個 import，三個平台，呼叫方式完全一致。
- **Session 對話管理** — 透過 session ID 跨次執行延續對話，支援存檔 / 讀取 / 壓縮歷史。
- **Web 搜尋模式** — 一個參數開啟 ChatGPT / Gemini 的網路搜尋能力。
- **非阻斷非同步** — 背景執行緒處理 API 請求，透過 `end_callback` 訂閱結果。
- **加密金鑰** — 支援 AES / RSA 加密的 API 金鑰存放於 `.env`。
- **Adapter 模式** — OOP 風格封裝，方便依賴注入與測試替換。
- **Lazy 載入** — `import ntu_easy_llm` 本身極輕量；各平台的 SDK 只在首次用到時才載入。
- **Agent API（0.5.0）** — `ntu_easy_llm.agent.chat`：多輪訊息＋工具呼叫（tool calling）＋用量，OpenAI／Anthropic／Gemini／任何 OpenAI 相容端點（公司閘道、Ollama）同一個呼叫方式。見 [Agent API](#agent-api工具呼叫)。

---

## Agent API（工具呼叫）

> 0.5.0 新增。舊的 `ask*`、`LLMSession`、Adapter 完全不變；這是另外一組給「agent host」用的 API。

`ask*` 只回一段 `str`。要讓 AI 替你「按按鈕」（呼叫工具）就要：多輪訊息、工具定義、AI 想呼叫哪些工具、用了多少 token。
`ntu_easy_llm.agent` 給四家同一個呼叫方式：

```python
from ntu_easy_llm.agent import chat, probe, Tool

tools = [Tool("money_add", "記一筆支出",
              {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]})]

reply = chat("openai", "gpt-4.1-mini",
             [{"role": "system", "content": "你是記帳助理"},
              {"role": "user", "content": "幫我記 80 元咖啡"}],
             tools=tools, api_key="sk-...")

reply.text         # AI 的文字（只呼叫工具時是 ""）
reply.tool_calls   # [ToolCall(id="call_1", name="money_add", arguments={"amount": 80})]
reply.usage        # Usage(input_tokens=..., output_tokens=...)

# 把工具結果接回去，再問一次
history = [..., {"role": "assistant", "content": "", "tool_calls": reply.tool_calls},
           {"role": "tool", "tool_call_id": "call_1", "name": "money_add", "content": "記好了"}]
```

| provider | 端點 | 備註 |
|---|---|---|
| `openai` | `https://api.openai.com/v1` | Chat Completions；長度上限送 `max_completion_tokens` |
| `anthropic` | `https://api.anthropic.com` | Messages API 原生 tool use；`system` 自動拆出來、連續的工具結果自動併成一則 |
| `gemini` | Gemini 的 OpenAI 相容端點 | 同一把 Gemini API key |
| `openai_compatible` | **一定要給 `base_url`** | 公司閘道、Ollama（`http://localhost:11434/v1`，`api_key` 可空）、LM Studio、vLLM |

- `probe(provider, model, api_key=..., base_url=...)`：打一句測試，`None`＝通了，否則回一句原因（不丟例外）。適合安裝精靈的「測一下」。
- 錯誤有型別：`LLMHTTPError`（`.status`、`.body`）、`LLMConnectionError`、`LLMResponseError`，呼叫端可以自己翻成人話。
- 直接打 HTTP（標準函式庫 urllib），不經各家 SDK：`import ntu_easy_llm.agent` 不會載入 openai／anthropic／google-genai。
  測試時傳 `session=`（任何有 requests 風格 `post()` 的物件，例如 `requests.Session()` 或假的）。
- 不讀 `.env`：`api_key` 由呼叫端給（可以用 `KeyMaterial`／`load_api_key` 拿）。

---

## 0.6 預發布：設定檔＋各家 adapter＋命令列

> 0.6.0 開發中（介面見 `docs/design/20261123-v06-architecture.md`）。上面的 `agent.chat` 與舊 API 都照舊。

一份設定檔列出「怎麼連到哪個模型」（profile），金鑰**只寫在哪裡**（環境變數名稱或 `secret: "<scheme>:<名稱>"`），不寫金鑰本身：

```json
{
  "version": 1,
  "default": "home",
  "profiles": {
    "home":    {"source": "local/lmstudio", "model": "example-model", "options": {"tools": true}},
    "ollama":  {"source": "local/ollama", "model": "example-model", "options": {"context_size": 8192}},
    "api":     {"source": "remote/api_key/openai", "api_key_env": "EXAMPLE_OPENAI_KEY", "model": "example-model"},
    "gateway": {"source": "remote/api_key/openai_compatible", "base_url": "https://gateway.example.com/v1",
                "api_key_env": "EXAMPLE_GATEWAY_KEY", "model": "example-model"}
  }
}
```

```python
from ntu_easy_llm import load_profiles

ps = load_profiles("profiles.json")          # .yaml 要裝 ntu_easy_llm[yaml]
llm = ps.adapter("home")                     # 每個來源都有同樣的四個方法

llm.probe()                                  # 只讀：列模型、確認連得到，不載入模型、不花 token
llm.probe(send=True)                         # 真的送一句
llm.list_models()                            # [ModelInfo(id=..., loaded=True, context=32768, ...)]
reply = llm.chat([{"role": "user", "content": "你好"}], max_tokens=200, stop=["\n\n"],
                 on_delta=lambda piece: print(piece, end=""))     # 給 on_delta 就邊收邊印
reply.text, reply.finish, reply.usage        # finish："stop"｜"length"｜"thinking"｜"tool_calls"
```

- 來源總表（`ntu_easy_llm catalog` 列出全部、這台能不能用、能不能用工具）：

  | 來源 | 路徑（`source`） | 另外要裝 | 工具（MCP） |
  |---|---|---|---|
  | 本地伺服器 | `local/{lmstudio, ollama, vllm, llamacpp, openai_compatible}` | — | 看伺服器與模型（`options.tools` 可指定） |
  | API 金鑰 | `remote/api_key/{openai, anthropic, gemini}` | — | ✓ |
  | API 金鑰（相容服務） | `remote/api_key/openai_compatible` | — | 看服務與模型 |
  | LLOP | `remote/api_key/llop` | `ntu_easy_llm[llop]` | 看服務端的模型 |
  | 訂閱登入 | `remote/subscription/{claude, github_copilot, chatgpt}` | `ntu_easy_llm[claude-login]`／`[copilot]`／`[chatgpt]`（三個一起：`[subscriptions]`） | **✗ 只能聊天** |

  訂閱登入來源只有文字進出：帶 `tools` 呼叫會丟 `tools_unsupported`（「這個來源只能聊天，不支援工具（MCP）」），不會默默忽略；要用工具請改用 API 金鑰或本地來源。
- 失敗一律是 `LLMError`，帶 `kind`（`quota_exhausted`、`auth_invalid`、`rate_limited`、`context_too_long`、`model_not_found`、`tools_unsupported`…）、
  `retry_after`（伺服器要你等幾秒）、`profile_id`。各家的差異與限制見架構文件第 5 節。
- 本機與區網的網址不走系統 proxy（VPN／proxy 軟體不會把它吃掉）；遠端照系統設定。`options.proxy` 可以改成 `none`／`system`。
- 只用標準函式庫打 HTTP，不載入任何供應商 SDK。

命令列：

```
ntu_easy_llm catalog [--locale en] [--json]
ntu_easy_llm models --config profiles.json --profile home
ntu_easy_llm probe  --config profiles.json --profile api [--send]
ntu_easy_llm chat   --config profiles.json --profile home "用一句話介紹你自己" [--stream]
```

結束碼：0＝成功、1＝供應商或網路失敗（或 probe 不通）、2＝設定有問題。`chat` 目前只跑單一 profile。

---

## 版本需求

```
Python 3.11+
```

---

## 安裝

### 方式一 — 安裝 whl（一般使用者）

```bash
pip install ntu_easy_llm-<version>-py3-none-any.whl
```

安裝後 import 不需要加 `src.` 前綴：`from ntu_easy_llm import ask_chatgpt`。

選裝（extras，在檔名或套件名後面加 `[...]`）：`yaml`（YAML 設定檔）、`llop`（LLOP 來源）、
`claude-login`／`copilot`／`chatgpt`／`subscriptions`（訂閱登入來源，只能聊天、沒有工具；見「備援路由、LLOP 與訂閱來源」）。

### 方式二 — 可編輯安裝（開發貢獻者）

```bash
git clone <repo-url>
cd ntu_easy_llm
pip install -e .
```

開發結束後，請到**乾淨資料夾**做一次煙霧測試再打包：

```bash
python -c "from ntu_easy_llm import ask_chatgpt; print(ask_chatgpt('test'))"
```

---

## 環境設定

在**專案根目錄**建立 `.env` 檔，填入對應平台的 API 金鑰：

```env
chatgpt=sk-...
gemini=AIza...
anthropic=sk-ant-...
```

- 套件會從當前工作目錄**自動往上層資料夾搜尋**最近的 `.env`，不需額外設定路徑。
- 請務必將 `.env` 加入 `.gitignore`，切勿提交至版本控制。
- 不想存明文金鑰時，可改存 AES / RSA 加密過的金鑰，詳見 [範例 9](#範例-9--加密金鑰keymaterial)。

---

## 快速開始

```python
from ntu_easy_llm import ask_chatgpt, ask_gemini, ask_anthropic

print(ask_chatgpt("Python 是什麼？"))
print(ask_gemini("Python 是什麼？"))
print(ask_anthropic("Python 是什麼？"))
```

---

## 可用 API 總覽

| 功能分類                | 函數 / 類別               | 說明                                                      |
|------------------------|--------------------------|----------------------------------------------------------|
| **載入金鑰**            | `load_api_key`           | 從 `.env` 讀取 API key（支援加密）                         |
| **列出可用模型**        | `list_chatgpt_models` 等 | 回傳此 API key 可用的模型清單                              |
| **單次問答（阻斷）**    | `ask_chatgpt` 等         | 單次問答，等待回傳純 `str`                                 |
| **單次問答（非阻斷）**  | `ask_chatgpt_async` 等   | 背景執行緒，回傳 `Future`，支援 `end_callback` / `on_error`|
| **統一 Dispatcher**     | `ask`                    | 傳入明確 `api_key`，執行期動態選擇平台                     |
| **多輪對話**            | `LLMSession`             | 有記憶的對話 session，支援存檔 / 繼續 / 壓縮歷史           |
| **Adapter 模式**        | `ChatGPTAdapter` 等      | 單次問答 OOP 封裝，自帶 API key                            |
| **加密金鑰**            | `KeyMaterial`            | 組合「金鑰來源 + 解密策略」，`resolve()` 取得明文金鑰      |
| **型別別名**            | `ChatGPTModel` 等        | 各平台可用模型的 `Literal` 型別提示                        |

### 預設模型

| 平台        | 預設模型                        |
|------------|--------------------------------|
| ChatGPT    | `gpt-4.1`                      |
| Gemini     | `gemini-2.5-flash`             |
| Anthropic  | `claude-haiku-4-5-20251001`    |

---

## 代碼範例

### 範例 0 — 查詢自己的 API 可用模型清單

```python
from ntu_easy_llm import load_api_key
from ntu_easy_llm import list_chatgpt_models, list_gemini_models, list_anthropic_models

if __name__ == "__main__":
    print(list_chatgpt_models(load_api_key("chatgpt")))
    print(list_gemini_models(load_api_key("gemini")))
    print(list_anthropic_models(load_api_key("anthropic")))
```

---

### 範例 1 — 簡單問答（三個平台）

```python
from ntu_easy_llm import ask_chatgpt, ask_gemini, ask_anthropic

if __name__ == "__main__":
    print(ask_chatgpt("How are you?"))
    print(ask_gemini("How are you?"))
    print(ask_anthropic("How are you?"))
```

模擬輸出：

```text
CHATGPT
''''''
I'm doing well, thank you! How are you? How can I assist you today? 😊
''''''

GEMINI
''''''
I am doing well, thank you for asking! As a large language model, I don't
experience feelings or emotions in the same way humans do. However, I am
functioning optimally and ready to assist you.

How are **you** doing today? I hope you're having a good one!
''''''

ANTHROPIC
''''''
I'm doing well, thank you for asking! I'm here and ready to help with whatever
questions or tasks you have in mind. How are you doing today?
''''''

Process finished with exit code 0
```

`ask_chatgpt` / `ask_gemini` / `ask_anthropic` 三者參數完全相同，均回傳純 `str`：

| 參數         | 型別   | 預設值         | 說明                                     |
|-------------|--------|---------------|------------------------------------------|
| `prompt`    | `str`  | —             | 問題或指令                                |
| `model_name`| `str`  | 見上方預設模型 | 使用的模型（見 [可用模型列表](#可用模型列表)）|
| `web_search`| `bool` | `False`       | 開啟網路搜尋（僅 ChatGPT / Gemini 支援）  |
| `password`  | `str`  | `None`        | 解密 `.env` 中 AES 加密金鑰的密碼          |

---

### 範例 2 — 指定模型

```python
from ntu_easy_llm import ask_chatgpt, ask_gemini, ask_anthropic

if __name__ == "__main__":
    print(ask_chatgpt(
        "This is the AI era. How can people use these tools to enrich their lives?",
        model_name="gpt-5.2-pro",
    ))
    print(ask_gemini(
        "What are the best recent movies in Taiwan?",
        model_name="gemini-2.5-flash-lite",
    ))
    print(ask_anthropic(
        "Provide a C++ template for a Player Manager system in Unreal Engine.",
        model_name="claude-sonnet-4-5-20250929",
    ))
```

---

### 範例 3 — 開啟 Web 搜尋

傳入 `web_search=True` 即可，對單次問答與 `LLMSession` 都適用。

```python
from ntu_easy_llm import ask_chatgpt, ask_gemini

if __name__ == "__main__":
    print(ask_chatgpt("2025 年 NBA 總冠軍是誰？", web_search=True))
    print(ask_gemini("Python 最新版本有哪些新功能？", web_search=True))
```

| 平台        | 實作方式                                |
|-------------|----------------------------------------|
| ChatGPT     | OpenAI Responses API `web_search` tool |
| Gemini      | Google Search 搜尋基礎（grounding）    |
| Anthropic   | 不支援（傳入後忽略）                    |

---

### 範例 4 — 多輪對話（Session）

`LLMSession` 維護完整對話歷史，每次 `ask()` 都會把過往訊息一起送出，讓模型記得整段對話。

```python
from ntu_easy_llm import LLMSession

if __name__ == "__main__":
    session = LLMSession("chatgpt")  # 或 "gemini" / "anthropic"

    print(session.ask("我的名字是 Alice，我正在學 Python。"))
    print(session.ask("我的名字是什麼？"))            # 仍然記得「Alice」
    print(session.ask("根據我的背景，推薦我下一步學什麼？"))
```

建構子參數：

```python
session = LLMSession(
    provider="chatgpt",     # 必填，"chatgpt" | "gemini" | "anthropic"（大小寫不拘）
    model_name="gpt-4.1",   # 選填，預設使用各平台推薦模型
    api_key=None,           # 選填，None 則從 .env 讀取
    web_search=False,       # 開啟網路搜尋
    password=None,          # AES 金鑰解密密碼
    max_workers=4,          # ask_async 的執行緒池大小
)
print(session.session_id)   # UUID，儲存起來之後可以 resume
```

---

### 範例 5 — 儲存、繼續與壓縮 Session

```python
from ntu_easy_llm import LLMSession

# ── 第一次執行 ────────────────────────────────────────────────
if __name__ == "__main__":
    session = LLMSession("gemini")
    session.ask("我正在用 Python 開發一個食譜推薦 APP。")
    session.ask("我應該用哪個資料庫？")

    session.save()               # 儲存至 ~/.ntu_easy_llm/sessions/<id>.json
    print(f"已儲存 session：{session.session_id}")
```

```python
# ── 之後（或另一個 process）繼續 ─────────────────────────────
from ntu_easy_llm import LLMSession

if __name__ == "__main__":
    sid = "貼上上面印出來的 session_id"
    session = LLMSession.resume(sid)

    print(session.ask("幫我整理一下之前討論的重點。"))  # 仍然有完整記憶

    # 對話過長時壓縮 token 用量（keep_last=4 保留最近 4 個來回 = 8 則訊息原文）
    session.compress_history(keep_last=4)
    print(f"壓縮後剩 {session.message_count} 則訊息")
```

其他常用方法與屬性：

```python
session.save(sessions_dir="./my_sessions")          # 自訂儲存路徑
LLMSession.resume(sid, sessions_dir="./my_sessions")
session.clear_history()    # 清空歷史，但保留 session 物件繼續使用
session.message_count      # 目前歷史訊息總數
session.history            # 完整歷史 [{"role": ..., "content": ...}, ...]
```

---

### 範例 6 — 非同步呼叫

`ask_*_async` 在背景執行緒發送請求，主執行緒不阻塞，立即回傳
[`Future`](https://docs.python.org/3/library/concurrent.futures.html#future-objects)。
可用 `end_callback` 訂閱結果，或直接 `.result()` 等待（等同阻斷式呼叫）。

```python
from ntu_easy_llm import ask_chatgpt_async, ask_gemini_async, ask_anthropic_async
from concurrent.futures import wait

if __name__ == "__main__":
    # Fire-and-forget：結果透過 end_callback 接收
    ask_chatgpt_async(
        "用一句話解釋量子糾纏。",
        end_callback=lambda text: print("ChatGPT:", text),
        on_error=lambda exc: print("Error:", exc),
    )

    # 結果直接存檔
    ask_gemini_async(
        "幫我整理今天的 AI 新聞摘要。",
        web_search=True,
        end_callback=lambda text: open("news.txt", "w", encoding="utf-8").write(text),
    )

    # 三個平台同時並行查詢，等全部完成後列印
    futures = [
        ask_chatgpt_async("什麼是 Transformer 架構？"),
        ask_gemini_async("什麼是 Transformer 架構？"),
        ask_anthropic_async("什麼是 Transformer 架構？"),
    ]
    wait(futures)
    for f in futures:
        print(f.result())
```

在阻斷式參數之外，`async` 版本多了兩個 callback 參數：

| 參數           | 型別                           | 說明                       |
|---------------|--------------------------------|----------------------------|
| `end_callback`| `Callable[[str], None]`        | 完成後以回應字串呼叫         |
| `on_error`    | `Callable[[Exception], None]`  | 發生例外時呼叫               |

`LLMSession` 也有對應的 `ask_async()`，可串聯 pipeline（第一步完成後自動觸發第二步）：

```python
session = LLMSession("anthropic", model_name="claude-sonnet-4-5-20250929")

def write_tests(code: str):
    session.ask_async(
        f"幫以下程式碼補上單元測試：\n{code}",
        end_callback=lambda tests: open("test_stack.py", "w").write(tests),
    )

session.ask_async("寫一個 Python Stack class。", end_callback=write_tests)
```

> **執行緒** — 模組層級執行緒池預設 `max_workers=8`；`LLMSession` 上的多個 `ask_async`
> 會**依序排隊**（對話順序很重要）。需要真正並行請建立多個獨立的 `LLMSession`。

---

### 範例 7 — Adapter 模式（自帶 API key）

適合需要依賴注入、或在多處共用同一把 API key 的場景。
所有 Adapter 共用相同的 `ask(prompt: str) -> str` 介面。

```python
from ntu_easy_llm import load_api_key
from ntu_easy_llm import ChatGPTAdapter, GeminiAdapter, AnthropicAdapter

if __name__ == "__main__":
    chatgpt   = ChatGPTAdapter(load_api_key("chatgpt"),     "gpt-4.1")
    gemini    = GeminiAdapter(load_api_key("gemini"),       "gemini-2.5-flash-lite")
    anthropic = AnthropicAdapter(load_api_key("anthropic"), "claude-sonnet-4-5-20250929")

    for adapter in (chatgpt, gemini, anthropic):
        print(adapter.ask("用繁體中文解釋 transformer 架構。"))
```

---

### 範例 8 — 統一 Dispatcher（自帶 API key）

`ask()` 適合需要在執行期動態選擇平台、或不想依賴 `.env` 的場景。

```python
from ntu_easy_llm import ask

if __name__ == "__main__":
    result = ask(
        service_provider="GEMINI",   # "CHATGPT" | "GEMINI" | "ANTHROPIC"
        api_key="AIza...",
        prompt="什麼是 RAG？",
        model_name="gemini-2.5-flash",
        web_search=False,
    )
    print(result)
```

---

### 範例 9 — 加密金鑰（KeyMaterial）

不想在 `.env` 存放明文金鑰時，可將金鑰加密後存入，呼叫時動態解密（明文金鑰不會寫入磁碟）。

**最簡單的情境** — 直接用 `password` 解密 AES 金鑰：

```python
from ntu_easy_llm import ask_chatgpt

# .env 內 chatgpt=<AES 加密後的 base64>
print(ask_chatgpt("Hello!", password="your-aes-password"))
```

`LLMSession(password=...)` 與各 `ask_*_async(password=...)` 都支援相同參數。

**需要混用多種解密方式**（明文 / AES / RSA）時，用 `KeyMaterial` 的具名建構子，一行搞定：

```python
from ntu_easy_llm import KeyMaterial
from ntu_easy_llm import ChatGPTAdapter, GeminiAdapter, AnthropicAdapter

chatgpt_key   = KeyMaterial.plain("chatgpt").resolve()                  # 明文，不解密
gemini_key    = KeyMaterial.plain("gemini").resolve()
anthropic_key = KeyMaterial.aes("anthropic", "AES_PASSWORD").resolve()  # AES：密碼放在 .env 的 AES_PASSWORD
# gemini_key  = KeyMaterial.rsa("gemini", "RSA_PRIVATE_KEY_PEM").resolve()  # RSA：私鑰 PEM 放在 .env

chatgpt   = ChatGPTAdapter(chatgpt_key,     "gpt-4.1")
gemini    = GeminiAdapter(gemini_key,       "gemini-2.5-flash-lite")
anthropic = AnthropicAdapter(anthropic_key, "claude-sonnet-4-5-20250929")
print(chatgpt.ask("How are you?"))
```

> 建立 `KeyMaterial` 不會讀取 `.env`、也不會解密；要到 `.resolve()` 才真正取值（刻意的 lazy / first-touch 設計）。

需要自訂金鑰來源、或混搭非標準組合時，仍可用完整的 provider + strategy 寫法：

```python
from ntu_easy_llm import KeyMaterial, EnvKeyProvider, AESDecryptStrategy

anthropic_key = KeyMaterial(
    EnvKeyProvider("anthropic"),
    AESDecryptStrategy("AES_PASSWORD"),
).resolve()
```

要產生上面 AES / RSA 需要的加密金鑰，用內建工具函式跑一次、把輸出貼進 `.env`：

```python
from ntu_easy_llm import aes_encrypt, rsa_encrypt

# AES：把明文金鑰加密成 base64，貼進 .env 的 anthropic=...，密碼放 AES_PASSWORD=...
print(aes_encrypt("sk-ant-真正的金鑰", "my-aes-password"))

# RSA：用公鑰加密，私鑰 PEM 放進 .env 的 RSA_PRIVATE_KEY_PEM=...
print(rsa_encrypt("AIza-真正的金鑰", public_key_pem="-----BEGIN PUBLIC KEY-----\n..."))
```

---

### 範例 10 — 批次多工 + 結果快取（ask_many / ResponseCache）

需要一次問很多題時，用 `ask_many` 併發送出並**照輸入順序**收回結果。`max_concurrent`
是限流旋鈕（各家 API 有 rate limit，請自行斟酌）；每題失敗會自動 retry。

```python
from ntu_easy_llm import ask_many

prompts = [f"用一句話解釋：{t}" for t in ("RAG", "Transformer", "Diffusion")]
answers = ask_many(
    prompts,
    provider="chatgpt",
    max_concurrent=3,      # 同時最多 3 個請求（避免觸發 rate limit）
    retries=2,             # 每題最多重試 2 次
    on_result=lambda i, p, a: print(f"[{i}] done"),
)
for a in answers:
    print(a)
```

搭配 `ResponseCache`，**問過的就不再問**——把已問過的問題與答案變成一份持久化 mapping，
重跑時自動跳過已完成的項目（中途中斷也能續跑）。

```python
from ntu_easy_llm import ask_chatgpt, ask_many, ResponseCache

cache = ResponseCache("my_project")        # ~/.ntu_easy_llm/cache/my_project.json

# 單題：第二次相同呼叫直接讀快取，不打 API
ask_chatgpt("什麼是 RAG？", cache=cache)
ask_chatgpt("什麼是 RAG？", cache=cache)   # ← 命中快取

# 批次：只有沒問過的才會真的送出
ask_many(prompts, "chatgpt", cache=cache)
```

預設用 `(provider, model, prompt, web_search)` 雜湊當 key。若 prompt 會變動、但你問的
「對象」是穩定的（例如某個地址、某組 ID），可自帶**語意 key** 建立你自己的 mapping：

```python
# cache_key 由你決定（例：地址字串、"isi||name"、"|".join(sorted([a, b]))）
ask_chatgpt(build_prompt(addr), cache=cache, cache_key=addr)
answers = ask_many(prompts, "chatgpt", cache=cache, cache_keys=[addr1, addr2, ...])

# ResponseCache 也可當一般 mapping 直接用
key = cache.make_key("chatgpt", "gpt-4.1", "hi")
if key in cache:
    print(cache.get(key))
cache.set("my-key", "my-value")
cache.save()
```

| 參數（`ask_many`）| 說明                                              |
|------------------|---------------------------------------------------|
| `max_concurrent` | 同時在途的最大請求數（rate-limit 控制）             |
| `retries`        | 每題額外重試次數（總嘗試 = `retries + 1`）          |
| `backoff`        | 重試間的指數退避基數秒數（`backoff * 2**n`）        |
| `wait_seconds`   | 每次呼叫後額外暫停，進一步節流                      |
| `on_result`      | `on_result(index, prompt, answer)` 進度回呼        |
| `cache`          | 傳入 `ResponseCache`，命中則不重送、結束時自動存檔  |
| `cache_keys`     | 每題自訂語意 key（長度需與 prompts 相同）           |

---

## Response 解析工具

若你直接呼叫各平台的原始 SDK，可用這些函式統一萃取回應文字（自動處理 `None` 與空白）：

```python
from ntu_easy_llm import (
    parse_openai_response,     # client.responses.create()          → str
    parse_openai_completion,   # client.chat.completions.create()   → str
    parse_gemini_response,     # client.models.generate_content()   → str
    parse_anthropic_response,  # client.messages.create()           → str
)

from openai import OpenAI
client = OpenAI(api_key="sk-...")
resp = client.responses.create(model="gpt-4.1", input="你好！")
text = parse_openai_response(resp)
```

---

## 可用模型列表

未指定 `model_name` 時使用各平台**預設**（粗體）。完整清單以 `ChatGPTModel` / `GeminiModel` /
`AnthropicModel` 型別別名為準。

### ChatGPT / OpenAI

| 模型                          | 說明                      |
|-------------------------------|--------------------------|
| `gpt-5.2`                     | 最新旗艦                  |
| `gpt-5.2-pro`                 | 複雜推理 / agent workflow |
| `gpt-5.1` / `gpt-5`           | 5 系列                    |
| **`gpt-4.1`**                 | **預設** — 平衡效能       |
| `gpt-4.1-mini`                | 較快、成本較低            |
| `gpt-4o` / `gpt-4o-mini`      | GPT-4o 系列               |
| `gpt-4o-search-preview` 等    | Web 搜尋預覽版            |
| `gpt-4-turbo`                 | 更快版本                  |

🔗 [OpenAI 官方模型文件](https://platform.openai.com/docs/models)

### Gemini / Google

| 模型                    | 說明                |
|-------------------------|---------------------|
| `gemini-2.5-pro`        | 最高能力            |
| **`gemini-2.5-flash`**  | **預設** — 平衡效能 |
| `gemini-2.5-flash-lite` | 經濟快速版          |

🔗 [Google Gemini 官方模型文件](https://ai.google.dev/gemini-api/docs/models/gemini)

### Anthropic Claude

| 模型                         | 世代 | 說明                      |
|------------------------------|------|--------------------------|
| `claude-opus-4-5-20251101`   | 4.5  | 最新旗艦                  |
| `claude-sonnet-4-5-20250929` | 4.5  | 通用版                    |
| **`claude-haiku-4-5-20251001`** | 4.5 | **預設** — 快速輕量      |
| `claude-opus-4-1-20250805`   | 4.1  | 舊旗艦，相容長文本        |
| `claude-opus-4-20250514`     | 4    | 舊版，推理佳              |
| `claude-sonnet-4-20250514`   | 4    | 舊版通用                  |
| `claude-3-7-sonnet-20250219` | 3.7  | 200k context             |
| `claude-3-5-haiku-20241022`  | 3.5  | 輕量，部分帳號可用        |
| `claude-3-haiku-20240307`    | 3    | 舊版輕量                  |

🔗 [Anthropic 官方模型文件](https://docs.anthropic.com/claude/docs/models-overview)

---

## 完整 API 一覽

| 名稱                          | 類型        | 說明                                               |
|-------------------------------|-------------|----------------------------------------------------|
| `ask_chatgpt` / `_gemini` / `_anthropic`       | function    | 單次問答，阻斷式                  |
| `ask_chatgpt_async` / `_gemini_async` / `_anthropic_async` | function | 單次問答，非阻斷 + `end_callback` |
| `ask`                         | function    | 統一入口，需自帶 API 金鑰                           |
| `ask_many`                    | function    | 批次多工：有界併發 + 重試 + 可選快取，照順序回傳    |
| `ResponseCache`               | class       | 持久化「key→答案」快取，問過的不再問                |
| `LLMSession`                  | class       | 多輪對話，支援儲存 / 繼續 / 歷史壓縮                |
| `LLMSession.resume`           | classmethod | 依 session ID 從磁碟載入並繼續                      |
| `LLMAdapter`                  | class (ABC) | Adapter 抽象基底類別                                |
| `ChatGPTAdapter` / `GeminiAdapter` / `AnthropicAdapter` | class | 單次問答 OOP Adapter                  |
| `load_api_key`                | function    | 從最近的 `.env` 讀取指定 key                        |
| `list_chatgpt_models` / `_gemini_models` / `_anthropic_models` | function | 列出可用模型           |
| `parse_openai_response` / `_openai_completion` / `_gemini_response` / `_anthropic_response` | function | 解析原始 SDK 回傳值 |
| `KeyMaterial`                 | class       | 組合 provider + 解密策略，`resolve()` 取得明文金鑰  |
| `KeyMaterial.plain/aes/rsa`   | classmethod | 具名建構子，常見情境一行建立 `KeyMaterial`          |
| `EnvKeyProvider`              | class       | 從 `.env` 依 tag 提供（可能加密的）金鑰             |
| `PlainTextStrategy` / `AESDecryptStrategy` / `RSADecryptStrategy` | class | 解密策略                |
| `aes_encrypt` / `rsa_encrypt` | function    | 產生加密金鑰用的工具函式                            |
| `ChatGPTModel` / `GeminiModel` / `AnthropicModel` | `Literal` | 各平台模型名稱的型別提示             |
| `agent.chat` / `agent.probe`  | function    | 多輪＋工具呼叫＋用量，四種 provider 同一個呼叫方式（0.5.0） |
| `agent.Tool` / `ToolCall` / `ChatReply` / `Usage` | dataclass | Agent API 的資料型別 |

---

## 備援路由（waterfall）、LLOP 與訂閱來源（0.6 預發布）

> 介面與設定格式見 [docs/design/20261123-v06-architecture.md](docs/design/20261123-v06-architecture.md) 第 3.7、3.9 節。

**備援路由**：`ntu_easy_llm.router.Router` 把好幾個來源包成一個，用起來跟單一 adapter 一樣——主要的不能用（額度用完、金鑰失效、429 重試後仍失敗）才換下一個，一般錯誤照丟。順序與觸發條件全部寫在設定檔，函式庫不預設任何供應商的先後；沒設定 `waterfall`＝不啟動。

```python
from ntu_easy_llm import load_profiles
from ntu_easy_llm.router import FallbackRequired

ps = load_profiles({
    "default": "chat",
    "profiles": {
        "primary": {"source": "remote/api_key/openai_compatible", "base_url": "https://llm.example.test/v1",
                    "api_key_env": "EXAMPLE_PRIMARY_KEY", "model": "example-model"},
        "backup":  {"source": "local/ollama", "base_url": "http://127.0.0.1:11434", "model": "example-model"},
    },
    "chains": {"chat": {"members": ["primary", "backup"], "waterfall": {"mode": "auto"}}},
})
llm = ps.router("chat")                       # 也可以給單一 profile id（＝不備援）
r = llm.chat([{"role": "user", "content": "你好"}])
print(r.text, r.profile_id, r.notice)         # 換過來源時 notice＝「已改用 backup（原因：額度用完）」
llm.status()                                  # 每個成員的冷卻狀態（給 UI）

# mode="ask"：觸發時丟 FallbackRequired，由呼叫端決定
try:
    r = llm.chat(messages)
except FallbackRequired as e:
    r = llm.resume(e.pending, use=e.pending.candidates[0])   # use=None＝放棄、丟原本的錯
```

**LLOP**（`remote/api_key/llop`，本地模型發布服務）：要裝 `ntu_easy_llm[llop]`。LLOP 還沒上 PyPI，目前從 LLOP 的 GitHub release wheel 安裝（`pip install "<wheel 檔>[sdk]"`），或從原始碼 `pip install -e "<LLOP 的資料夾>[sdk]"`；沒裝這個 extra 的人完全不受影響（`import ntu_easy_llm` 不會載入 `llmop`）。
設定只寫 `"llop_profile": "<SDK 設定檔名>"`；API key、簽章私鑰、釘選都在 LLOP SDK 自己的設定檔裡（先在終端機跑 `llmop client enroll …`）。API 連結換了會丟 `kind == "link_changed"`，向服務提供者拿新連結後 `llmop client set-link`。

**訂閱來源**（`remote/subscription/claude`、`github_copilot`、`chatgpt`）：用這台電腦已登入的官方 CLI，另外安裝對應的登入套件。**只給本人使用**（用的是你個人的訂閱；見下方「訂閱登入來源的使用聲明」）。**只能聊天、沒有工具（MCP）**：帶 `tools` 呼叫會丟 `tools_unsupported`，不會默默忽略。

| 來源 | 登入套件 | 安裝 |
|---|---|---|
| `remote/subscription/claude` | ClaudeLogin（`claude-subscription`） | `pip install "ntu_easy_llm[claude-login]"` |
| `remote/subscription/github_copilot` | GithubCopilotLogin（`github_copilot_login`） | `pip install "ntu_easy_llm[copilot]"` |
| `remote/subscription/chatgpt` | ChatGPTLogin（`chatgpt_login`） | `pip install "ntu_easy_llm[chatgpt]"` |
| 三個都要 | | `pip install "ntu_easy_llm[subscriptions]"` |

從 wheel 檔安裝時寫成 `pip install "ntu_easy_llm-<version>-py3-none-any.whl[subscriptions]"`。這幾個 extra 跟 `[llop]` 一樣，用各套件 GitHub Release 的 wheel 網址釘住版本；它們不在 PyPI 上，**不要直接 `pip install` 套件名**（同名的 PyPI 套件可能是別人註冊的）。安裝之後還要在這台電腦裝好官方 CLI 並登入。

**這三個登入套件是獨立的專案**：各自包一個官方 CLI，也各自保有那個 CLI 本身的 MCP／agent 能力，照自己的節奏發展；ntu_easy_llm 不把它們的程式碼收進來，只呼叫它們的 `ask()`（文字進、文字出）。所以經 ntu_easy_llm 使用時沒有工具（MCP）；它們往後加的功能也不影響這邊。需要那些 CLI 的工具或 agent 能力，請直接用那幾個套件或官方 CLI。

### 訂閱登入來源的使用聲明

訂閱來源經 ClaudeLogin、GithubCopilotLogin、ChatGPTLogin 這三個套件，呼叫**你自己在這台電腦上已登入的官方 CLI**，用的是你個人的訂閱帳號。

1. **不推薦拿個人訂閱服務別人**：不建議把個人訂閱拿來服務其他人、轉售、共用或分享出去——例如經 LLOP 之類的通道提供給別人、放進給訪客使用的 AI 功能，或放進給別人用的備援鏈。目錄上這幾個來源標「只給本人使用」就是這個意思。
2. **條款由你自己確認**：每個訂閱服務（例如 Claude、GitHub Copilot、ChatGPT）都有自己的使用條款與政策。某種用法是否被允許，請你自己閱讀並理解該服務的條款，並自行負責。
3. **本專案只提供技術介面**：本專案及其作者、維護者不代表任何服務商，與你如何使用自己的訂閱無關，也不對違反服務條款所造成的後果（例如帳號被限制或停用）負責。函式庫技術上不阻擋任何用法，但「沒被擋」不代表服務商允許。
4. 這段是對 MIT 授權中「按現狀提供、不附任何擔保」的補充說明，不是額外的授權條件。

<details>
<summary>English version</summary>

**Usage notice for subscription sources.** The subscription sources call, through the ClaudeLogin, GithubCopilotLogin and ChatGPTLogin packages, the official CLI that **you** have signed in to on your own machine, using your personal subscription.

1. **Not recommended for serving others.** We do not recommend using a personal subscription to serve other people, or to resell, pool or share access — for example through a relay channel such as LLOP, an AI feature offered to guests, or a fallback chain used by other people. This is what the "for personal use only" label in the catalog means.
2. **You check the terms.** Each subscription service (for example Claude, GitHub Copilot, ChatGPT) has its own terms of use and policies. Whether a particular use is allowed is for you to read, understand and take responsibility for.
3. **Technical interface only.** This project and its authors and maintainers do not represent any service provider, are not involved in how you use your subscription, and are not responsible for the consequences of violating a provider's terms (such as account restrictions or suspension). The library does not technically block any use; not being blocked does not mean a use is permitted by the provider.
4. This notice supplements the "as is, without warranty" clause of the MIT License; it is not an additional license condition.

</details>

---

## 套件依賴

```
python        >= 3.11
openai        >= 2.0.0
google-genai  >= 1.0.0
anthropic     >= 0.25.0
cryptography  >= 42.0.0
python-dotenv >= 1.0.0
```

---

## 授權

本專案採 [MIT License](LICENSE) 授權。
