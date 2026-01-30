from src.ntu_easy_llm import load_api_key
from src.ntu_easy_llm import list_chatgpt_models, list_gemini_models, list_anthropic_models

if __name__ == "__main__":
    print(list_chatgpt_models(load_api_key("chatgpt")))
    print(list_gemini_models(load_api_key("gemini")))
    print(list_anthropic_models(load_api_key("anthropic")))
