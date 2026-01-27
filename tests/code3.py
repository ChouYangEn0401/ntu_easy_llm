from src.ntu_easy_llm.core.config_loader import load_api_key
from src.ntu_easy_llm.core.utils import GeminiAdapter, ChatGPTAdapter, AnthropicAdapter

if __name__ == "__main__":
    prompt = "How are you?"

    chatgpt = ChatGPTAdapter(
        api_key=load_api_key(tag="chatgpt"),
        model_name="gpt-4.1"
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

