# ntu_easy_llm

> **v0.4.3** · Python 3.11+

針對 **ChatGPT**、**Gemini**、**Anthropic Claude** 三大 LLM 服務的輕量 Python 封裝套件。

- **統一介面** — 一個 import，三個平台，呼叫方式完全一致。
- **Session 對話管理** — 透過 session ID 跨次執行延續對話，支援存檔 / 讀取 / 壓縮歷史。
- **Web 搜尋模式** — 一個參數開啟 ChatGPT / Gemini 的網路搜尋能力。
- **非阻斷非同步** — 背景執行緒處理 API 請求，透過 `end_callback` 訂閱結果。
- **加密金鑰** — 支援 AES / RSA 加密的 API 金鑰存放於 `.env`。
- **Adapter 模式** — OOP 風格封裝，方便依賴注入與測試替換。
- **Lazy 載入** — `import ntu_easy_llm` 本身極輕量；各平台的 SDK 只在首次用到時才載入。

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
