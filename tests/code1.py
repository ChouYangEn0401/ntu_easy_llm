from src.ntu_easy_llm.core.config_loader import load_api_key
from src.ntu_easy_llm.core.utils import _ask_chatgpt, _ask_gemini

if __name__ == "__main__":
    chatgpt_api = load_api_key(tag="chatgpt")
    gemini_api = load_api_key(tag="gemini")

    print(_ask_chatgpt(chatgpt_api, "How Are You !!?", "gpt-4.1"))
    print(_ask_gemini(gemini_api, "How Are You !!?", "gemini-2.5-flash-lite"))

