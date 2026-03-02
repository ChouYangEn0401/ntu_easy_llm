"""Test script for listing all available models from API providers."""
from ntu_easy_llm import load_api_key
from ntu_easy_llm import list_chatgpt_models, list_gemini_models, list_anthropic_models

if __name__ == "__main__":
    print("=== Available ChatGPT Models ===")
    print(list_chatgpt_models(load_api_key("chatgpt")))
    
    print("\n=== Available Gemini Models ===")
    print(list_gemini_models(load_api_key("gemini")))
    
    print("\n=== Available Anthropic Models ===")
    print(list_anthropic_models(load_api_key("anthropic")))
