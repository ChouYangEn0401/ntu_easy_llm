# 使用說明書教學

## 版本限定
```commandline
python 3.11 以上
```

## 安裝指令
### 一、我只是使用者
```commandline
pip install ntu_easy_llm-0.1.0-py3-none-any.whl
```
### 二、我在一起開發
```commandline
pip install -e ../ntu-easy-llm/.
```

## 代碼範例
### 範例一: 簡單訪問服務
```python
from ntu_easy_llm import ask_chatgpt, ask_gemini

if __name__ == "__main__":
    print(ask_chatgpt("How Are You !!?"))
    print(ask_gemini("How Are You !!?"))
```

### 範例二: 挑選模型
![chatgpt_models.png](docs/chatgpt_models.png)
![gemini_models.png](docs/gemini_models.png)

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

