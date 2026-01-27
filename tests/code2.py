from src.ntu_easy_llm.core.utils import ask_chatgpt, ask_gemini, ask_anthropic

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
