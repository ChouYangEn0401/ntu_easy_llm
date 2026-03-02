# 使用說明書教學

## 版本限定
```commandline
python 3.11 以上
```

## 安裝指令
### 一、我只是一般API套件使用者，單純想要功能可以跑
```commandline
pip install ntu_easy_llm-0.1.0-py3-none-any.whl
```
使用時 import 不用打 src.
### 二、我是該套件的合作開發人員，我是團隊一員或者fork某部分並想加入自己的更動
```commandline
pip install -e ../ntu-easy-llm/.
```
使用時，請依照資料夾路徑正確 import
開發結束後，請到乾淨的資料夾做測試再打包
```bash
python -c "from ntu_easy_llm import ask_chatgpt; print(ask_chatgpt('test'))"
```

## 環境預備
請在專案 root folder 下，建立 `.env` 文件，並確保自己的 api-key 有加入
該文件請 .gitignore 起來
```text
gemini=*** gemini api key ***
chatgpt=*** chatgpt api key ***
anthropic=*** anthropic api key ***
```

## 代碼範例
### 所有可以用的 API
僅需 _**import ntu_easy_llm**_ 就可以直接用的

| 功能分類               | 函數 / 類別                 | 說明                               |
| ------------------ | ----------------------- |----------------------------------|
| **載入環境 / API key** | `load_api_key`          | 載入環境變數 env 裡面的 (加密)API key  |
| **列出可用模型**         | `list_chatgpt_models`   | 顯示可用 ChatGPT 模型列表                |
|                    | `list_gemini_models`    | 顯示可用 Gemini 模型列表                 |
|                    | `list_anthropic_models` | 顯示可用 Claude / Anthropic 模型列表     |
| **主要問答 API**       | `ask_chatgpt`           | 封裝 ChatGPT 問答呼叫                  |
|                    | `ask_gemini`            | 封裝 Gemini 問答呼叫                   |
|                    | `ask_anthropic`         | 封裝 Claude / Anthropic 問答呼叫       |
| **問答 Adapter 模式**  | `ChatGPTAdapter`        | Adapter 封裝 ChatGPT 問答            |
|                    | `GeminiAdapter`         | Adapter 封裝 Gemini 問答             |
|                    | `AnthropicAdapter`      | Adapter 封裝 Claude / Anthropic 問答 |


### 範例零: 查詢自己的API所有可用模型清單
```python
from ntu_easy_llm import load_api_key
from ntu_easy_llm import list_chatgpt_models, list_gemini_models, list_anthropic_models

if __name__ == "__main__":
    print(list_chatgpt_models(load_api_key("chatgpt")))
    print(list_gemini_models(load_api_key("gemini")))
    print(list_anthropic_models(load_api_key("anthropic")))
```
    
### 範例一: 簡單訪問服務
```python
from ntu_easy_llm import ask_chatgpt, ask_gemini, ask_anthropic

if __name__ == "__main__":
    print(ask_chatgpt("How Are You !!?"))
    print(ask_gemini("How Are You !!?"))
    print(ask_anthropic("How Are You !!?"))
```

### 範例二: 挑選模型
```python
from ntu_easy_llm import ask_chatgpt, ask_gemini, ask_anthropic

if __name__ == "__main__":
    print(ask_chatgpt(
        "This is the AI era. How can people use these tools to enrich their lives?",
        model_name="gpt-5.2-pro"
    ))

    print(ask_gemini(
        "What are the best recent movies in Taiwan?",
        model_name="gemini-2.5-flash-lite"
    ))

    print(ask_anthropic(
        "Provide a C++ template for a Player Manager system in Unreal Engine.",
        model_name="claude-sonnet-4-5-20250929"
    ))
```

### 範例三: 透過 adapter 自行讀取特定 api-key
```python
from ntu_easy_llm import load_api_key
from ntu_easy_llm import GeminiAdapter, ChatGPTAdapter, AnthropicAdapter

if __name__ == "__main__":
    prompt = "How are you?"

    chatgpt = ChatGPTAdapter(
        api_key=load_api_key(tag="chatgpt"),
        model_name="gpt-4o-mini"
    )

    gemini = GeminiAdapter(
        api_key=load_api_key(tag="gemini"),
        model_name="gemini-2.5-flash-lite"
    )

    anthropic = AnthropicAdapter(
        api_key=load_api_key(tag="anthropic"),
        model_name="claude-sonnet-4-5-20250929"
    )

    print(chatgpt.ask(prompt))
    print(gemini.ask(prompt))
    print(anthropic.ask(prompt))
```
```text
CHATGPT
''''''
I'm doing well, thank you! How are you? How can I assist you today? 😊
''''''

GEMINI
''''''
I am doing well, thank you for asking! As a large language model, I don't experience feelings or emotions in the same way humans do. However, I am functioning optimally and ready to assist you.

How are **you** doing today? I hope you're having a good one!
''''''

ANTHROPIC
''''''
I'm doing well, thank you for asking! I'm here and ready to help with whatever questions or tasks you have in mind. How are you doing today?
''''''


Process finished with exit code 0
```

### 範例四: 範例三寫法 如何轉換為等價 key-material + adapter 寫法
```python
from ntu_easy_llm.core.cryptions import KeyMaterial, EnvKeyProvider
from ntu_easy_llm.core.cryptions import PlainTextStrategy, AESDecryptStrategy, RSADecryptStrategy
from ntu_easy_llm import GeminiAdapter, ChatGPTAdapter, AnthropicAdapter

if __name__ == "__main__":
    prompt = "How are you?"
    decode_strategy = PlainTextStrategy()

    # ---- Key Materials ----
    gemini_key = KeyMaterial(
        provider=EnvKeyProvider(env_password_tag="gemini"),
        decryptor=decode_strategy,  # Plain key
    ).resolve()

    chatgpt_key = KeyMaterial(
        provider=EnvKeyProvider(env_password_tag="chatgpt"),
        decryptor=decode_strategy,  # Plain key
    ).resolve()

    anthropic_key = KeyMaterial(
        provider=EnvKeyProvider(env_password_tag="anthropic"),
        decryptor=decode_strategy,  # Plain key
    ).resolve()

    # ---- Adapters ----
    gemini = GeminiAdapter(gemini_key, "gemini-2.5-flash-lite")
    chatgpt = ChatGPTAdapter(chatgpt_key, "gpt-4.1")
    anthropic = AnthropicAdapter(anthropic_key, "claude-sonnet-4-5-20250929")

    # ---- Ask ----
    print(gemini.ask(prompt))
    print(chatgpt.ask(prompt))
    print(anthropic.ask(prompt))
```

### \[開發中...] 範例五: 透過  key-material + adapter 寫法，自行讀取特定 api-key 並同時提供不同解密手法
```python
from ntu_easy_llm.core.cryptions import KeyMaterial, EnvKeyProvider
from ntu_easy_llm.core.cryptions import PlainTextStrategy, AESDecryptStrategy, RSADecryptStrategy
from ntu_easy_llm import GeminiAdapter, ChatGPTAdapter, AnthropicAdapter

if __name__ == "__main__":
    prompt = "How are you?"
    decode_strategy = PlainTextStrategy()

    # ---- Key Materials ----
    gemini_key = KeyMaterial(
        provider=EnvKeyProvider(env_password_tag="GEMINI_API_KEY"),
        decryptor=RSADecryptStrategy(env_password_tag="RSA_PRIVATE_KEY_PEM"),
    ).resolve()  # RSA encrypted key + password also from env

    gemini_key_free = KeyMaterial(
        provider=EnvKeyProvider(env_password_tag="GEMINI_API_KEY"),
        decryptor=decode_strategy,  # Plain key
    ).resolve()

    chatgpt_key = KeyMaterial(
        provider=EnvKeyProvider(env_password_tag="CHATGPT_API_KEY"),
        decryptor=decode_strategy,  # Plain key
    ).resolve()

    anthropic_key = KeyMaterial(
        provider=EnvKeyProvider(env_password_tag="ANTHROPIC_API_KEY"),
        decryptor=AESDecryptStrategy(env_password_tag="AES_PASSWORD"),  # AES 密碼從 env
    ).resolve()  # AES encrypted key + password also from env

    # ---- Adapters ----
    gemini = GeminiAdapter(gemini_key, "gemini-2.5-flash-lite")
    chatgpt_free = ChatGPTAdapter(gemini_key_free, "gpt-5.2-pro")
    chatgpt = ChatGPTAdapter(chatgpt_key, "gpt-4.1")
    anthropic = AnthropicAdapter(anthropic_key, "claude-sonnet-4-5-20250929")

    # ---- Ask ----
    print(gemini.ask(prompt))
    print(chatgpt_free.ask(prompt))
    print(chatgpt.ask(prompt))
    print(anthropic.ask(prompt))
```

## 模型選擇

### OpenAI 模型
| 模型 ID                 | 備註                                                         |
| --------------------- | ---------------------------------------------------------- |
| **gpt-5.2**           | 最新主推、高階通用大模型。 ([OpenAI 平台][1])                             |
| **gpt-5.2-pro**       | 智能更強、適合更複雜推理/agent workflow。 ([OpenAI 平台][1])              |
| **gpt-5.1**           | 5 系列成熟版本、coding / agent 任務優。 ([OpenAI 平台][1])              |
| **gpt-5**             | 仍是主力一代。 ([OpenAI 平台][1])                                   |
| **gpt-4.1**           | GPT-4 系列新版旗艦（官方直接寫了model name，不需要 alias）。 ([OpenAI 平台][1]) |
| **gpt-4.1-mini**      | 較小成本版與快速款，可直接用。 ([OpenAI 平台][1])                           |
| **gpt-4o**            | 官方推的 GPT-4o 旗艦版本。 ([OpenAI 平台][1])                         |
| **gpt-4o-mini**       | 經濟版輕量模型。 ([OpenAI 平台][1])                                  |
| **gpt-4-turbo**       | 仍可用的更快版本。 ([OpenAI 平台][1])                                 |
| **（可選）gpt-3.5-turbo** | 傳統經濟模型（不推薦如果不需要 backwards support）。 ([OpenAI 平台][1])       |

[1]: https://platform.openai.com/docs/models?utm_source=chatgpt.com "Models | OpenAI API"
🔗 [OpenAI 官方模型文件](https://platform.openai.com/docs/models)

---

### Google 模型
| 模型 ID                     | 備註                                          |
| ------------------------- |---------------------------------------------|
| **gemini-2.5-pro**        | 高階推理、多功能主推。 ([Google AI for Developers][2]) |
| **gemini-2.5-flash**      | 能力強＋速度高。 ([Google AI for Developers][2])    |
| **gemini-2.5-flash-lite** | 經濟快速版本。 ([Google AI for Developers][2])     |

[2]: https://ai.google.dev/gemini-api/docs/models?utm_source=chatgpt.com "Gemini models | Gemini API - Google AI for Developers"
🔗 [Google Gemini 官方文件](https://ai.google.dev/gemini-api/docs/models/gemini)

---

### Anthropic 模型
| 模型 ID                          | 世代  | 備註 / 適用情境                                  |
| ------------------------------ | --- |--------------------------------------------|
| **claude-opus-4-5-20251101**   | 4.5 | 最新 flagship 版本，高複雜推理/代理任務 ([Docs][3])      |
| **claude-sonnet-4-5-20250929** | 4.5 | 通用智能 & 效能平衡，主推版本 ([Docs][4])               |
| **claude-haiku-4-5-20251001**  | 4.5 | 快速且便宜，適合簡單聊天或大量請求 ([Docs][4])              |
| **claude-opus-4-1-20250805**   | 4.1 | 舊 4.x 版本 flagship，兼容長文本 ([Docs][3])        |
| **claude-opus-4-20250514**     | 4   | 舊 4.x 版本，推理能力較好但 context 較短 ([Docs][3])    |
| **claude-sonnet-4-20250514**   | 4   | 舊 4.x 版本通用智能 ([Docs][3])                   |
| **claude-3-haiku-20240307**    | 3   | 舊 3.x 輕量版本，快速且便宜 ([Docs][3])               |
| **claude-3-5-haiku-20241022**  | 3.5 | 3.5 family 輕量版本，部分帳號可用 ([Docs][3])         |
| **claude-3-7-sonnet-20250219** | 3.7 | 3.7 family Sonnet，200k context ([Docs][3]) |

[3]: https://platform.claude.com/docs/en/about-claude/models/overview?utm_source=chatgpt.com "Models overview - Anthropic"
[4]: https://platform.claude.com/docs/zh-TW/about-claude/models/overview?utm_source=chatgpt.com "模型概覽 - Claude API Docs"
🔗 [Anthropic 官方模型文件](https://docs.anthropic.com/claude/docs/models-overview)

