

# 使用說明書教學

## 安裝
### 一、我只是使用者
```commandline
pip install ntu-easy-llm.whl
```
### 二、我在一起開發
```commandline
pip install -e ../ntu-easy-llm/.
```

## 代碼範例
### \[廢棄] 範例一: 簡單訪問服務
code1.py

```python
from ntu_easy_llm import load_api_key_from_env
from ntu_easy_llm import _ask_chatgpt, _ask_gemini

if __name__ == "__main__":
    chatgpt_api = load_api_key_from_env(TAG="chatgpt")
    gemini_api = load_api_key_from_env(TAG="gemini")

    print(_ask_chatgpt(chatgpt_api, "gpt-4.1", "How Are You !!?"))
    print(_ask_gemini(gemini_api, "gemini-2.5-flash-lite", "How Are You !!?"))
```

### 範例一: 簡單訪問服務
code2.py

```python
from ntu_easy_llm import ask_chatgpt, ask_gemini

if __name__ == "__main__":
    print(ask_chatgpt("How Are You !!?"))
    print(ask_gemini("How Are You !!?"))
```
