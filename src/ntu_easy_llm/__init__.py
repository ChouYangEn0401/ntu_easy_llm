"""
ntu_easy_llm: Unified LLM API wrapper for ChatGPT, Gemini, and Anthropic.

A lightweight, production-ready package for building LLM applications with
automatic API key management, model caching, and encryption support.

Example usage:
    >>> from ntu_easy_llm import ask_chatgpt, load_api_key
    >>> response = ask_chatgpt("What is Python?")
    >>> print(response)
    
    >>> from ntu_easy_llm import ChatGPTAdapter
    >>> adapter = ChatGPTAdapter(api_key=load_api_key("chatgpt"))
    >>> response = adapter.ask("Explain decorators")
"""
from .core.config_loader import load_api_key
from .core.utils import ask_chatgpt, ask_gemini, ask_anthropic
from .core.utils import list_chatgpt_models, list_gemini_models, list_anthropic_models
from .core.utils import ChatGPTAdapter, GeminiAdapter, AnthropicAdapter
from ._version import __version__

__all__ = [
    ## load env
    "load_api_key",

    ## show all usable model list
    "list_chatgpt_models",
    "list_gemini_models",
    "list_anthropic_models",

    ## main core asking api
    "ask_chatgpt",
    "ask_gemini",
    "ask_anthropic",

    ## main core asking api in adapter mode
    "ChatGPTAdapter",
    "GeminiAdapter",
    "AnthropicAdapter",
]
