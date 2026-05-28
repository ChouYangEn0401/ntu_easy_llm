"""
ntu_easy_llm — Unified LLM API wrapper for ChatGPT, Gemini, and Anthropic.

Features
--------
- Single-shot ask functions  : ask_chatgpt / ask_gemini / ask_anthropic
- Non-blocking async variants: ask_chatgpt_async / … (with end_callback support)
- Multi-turn sessions        : LLMSession  (save / resume / compress history)
- Web search mode            : pass web_search=True to any ask or session
- Adapter pattern            : ChatGPTAdapter / GeminiAdapter / AnthropicAdapter
- Response normalisation     : parse_openai_response / parse_gemini_response / …
- Secure key management      : AES / RSA encrypted keys via .env

Quick start
-----------
    >>> from ntu_easy_llm import ask_chatgpt
    >>> print(ask_chatgpt("What is Python?"))

    >>> from ntu_easy_llm import LLMSession
    >>> session = LLMSession("gemini")
    >>> session.ask("My name is Alice.")
    >>> print(session.ask("What is my name?"))
"""

# --- environment / key helpers ---
from .core.config_loader import load_api_key

# --- model type literals ---
from .core.models import AnyModel, AnthropicModel, ChatGPTModel, GeminiModel

# --- response normalisation helpers ---
from .core.response_utils import (
    parse_anthropic_response,
    parse_gemini_response,
    parse_openai_completion,
    parse_openai_response,
)

# --- stateless single-shot ask (blocking) ---
from .core.utils import ask_anthropic, ask_chatgpt, ask_gemini

# --- stateless single-shot ask (non-blocking) ---
from .core.utils import ask_anthropic_async, ask_chatgpt_async, ask_gemini_async

# --- unified dispatcher (explicit API key) ---
from .core.utils import ask

# --- model listing ---
from .core.utils import list_anthropic_models, list_chatgpt_models, list_gemini_models

# --- adapter classes ---
from .core.utils import AnthropicAdapter, ChatGPTAdapter, GeminiAdapter, LLMAdapter

# --- multi-turn session ---
from .core.session import LLMSession

# --- version ---
from ._version import __version__

__all__ = [
    # key management
    "load_api_key",

    # model literals
    "ChatGPTModel",
    "GeminiModel",
    "AnthropicModel",
    "AnyModel",

    # response utils
    "parse_openai_response",
    "parse_openai_completion",
    "parse_gemini_response",
    "parse_anthropic_response",

    # single-shot blocking
    "ask_chatgpt",
    "ask_gemini",
    "ask_anthropic",

    # single-shot non-blocking
    "ask_chatgpt_async",
    "ask_gemini_async",
    "ask_anthropic_async",

    # unified dispatcher
    "ask",

    # model listing
    "list_chatgpt_models",
    "list_gemini_models",
    "list_anthropic_models",

    # adapters
    "LLMAdapter",
    "ChatGPTAdapter",
    "GeminiAdapter",
    "AnthropicAdapter",

    # session
    "LLMSession",

    # meta
    "__version__",
]
