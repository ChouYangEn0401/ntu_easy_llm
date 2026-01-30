## 套件對外的 API
"""
ntu_easy_llm public API
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
