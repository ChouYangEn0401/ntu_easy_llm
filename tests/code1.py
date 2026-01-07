from src.ntu_easy_llm.core.helper import load_api_key_from_env
from src.ntu_easy_llm.core.utils import _ask_chatgpt, _ask_gemini

if __name__ == "__main__":
    chatgpt_api = load_api_key_from_env('../core/.env', TAG="chatgpt")
    gemini_api = load_api_key_from_env('../core/.env', TAG="gemini")

    print(_ask_chatgpt(chatgpt_api, "How Are You !!?", "gpt-4.1"))
    print(_ask_gemini(gemini_api, "How Are You !!?", "gemini-2.5-flash-lite"))

