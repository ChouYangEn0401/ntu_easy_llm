from src.ntu_easy_llm.core.cryptions import KeyMaterial, EnvKeyProvider
from src.ntu_easy_llm.core.cryptions import PlainTextStrategy, AESDecryptStrategy, RSADecryptStrategy
from src.ntu_easy_llm.core.utils import GeminiAdapter, ChatGPTAdapter, AnthropicAdapter

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

