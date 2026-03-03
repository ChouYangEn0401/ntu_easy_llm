from src.ntu_easy_llm.core.cryptions import KeyMaterial, EnvKeyProvider  ## not yet api release final check
from src.ntu_easy_llm.core.cryptions import PlainTextStrategy, AESDecryptStrategy, RSADecryptStrategy  ## not yet api release final check
from src.ntu_easy_llm import GeminiAdapter, ChatGPTAdapter, AnthropicAdapter

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

