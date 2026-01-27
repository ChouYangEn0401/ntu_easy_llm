from src.ntu_easy_llm.core.utils import ask_chatgpt, ask_gemini, ask_anthropic

if __name__ == "__main__":
    print(ask_chatgpt("How Are You !!?"))
    print(ask_gemini("How Are You !!?"))
    print(ask_anthropic("How Are You !!?"))
