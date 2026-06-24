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

Lazy loading
------------
Importing ``ntu_easy_llm`` is cheap: it pulls in *nothing* heavy. Each public
name is resolved on first access (PEP 562 ``__getattr__``) and then cached, so
the underlying SDK for a platform is only imported when you actually touch it —
using just ``KeyMaterial`` never loads ``google-genai`` / ``openai`` / etc.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from ._version import __version__

# --- public name -> defining submodule (resolved lazily on first access) ---
_LAZY_SUBMODULES: dict[str, tuple[str, ...]] = {
    ".core.config_loader": ("load_api_key",),
    ".core.models": ("AnyModel", "AnthropicModel", "ChatGPTModel", "GeminiModel"),
    ".core.response_utils": (
        "parse_anthropic_response",
        "parse_gemini_response",
        "parse_openai_completion",
        "parse_openai_response",
    ),
    ".core.utils": (
        "ask_chatgpt", "ask_gemini", "ask_anthropic",
        "ask_chatgpt_async", "ask_gemini_async", "ask_anthropic_async",
        "ask", "ask_many",
        "list_chatgpt_models", "list_gemini_models", "list_anthropic_models",
        "LLMAdapter", "ChatGPTAdapter", "GeminiAdapter", "AnthropicAdapter",
    ),
    ".core.cache": ("ResponseCache",),
    ".core.session": ("LLMSession",),
    ".core.cryptions": (
        "KeyMaterial", "EnvKeyProvider",
        "PlainTextStrategy", "AESDecryptStrategy", "RSADecryptStrategy",
        "aes_encrypt", "rsa_encrypt",
    ),
}

_NAME_TO_MODULE: dict[str, str] = {
    name: module
    for module, names in _LAZY_SUBMODULES.items()
    for name in names
}


def __getattr__(name: str):
    """Resolve a public name to its submodule on first access, then cache it."""
    module_path = _NAME_TO_MODULE.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = importlib.import_module(module_path, __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


# --- static analysis only: gives IDEs / mypy the real symbols (never runs) ---
if TYPE_CHECKING:
    from .core.config_loader import load_api_key
    from .core.models import AnyModel, AnthropicModel, ChatGPTModel, GeminiModel
    from .core.response_utils import (
        parse_anthropic_response,
        parse_gemini_response,
        parse_openai_completion,
        parse_openai_response,
    )
    from .core.utils import (
        ask_anthropic, ask_chatgpt, ask_gemini,
        ask_anthropic_async, ask_chatgpt_async, ask_gemini_async,
        ask, ask_many,
        list_anthropic_models, list_chatgpt_models, list_gemini_models,
        AnthropicAdapter, ChatGPTAdapter, GeminiAdapter, LLMAdapter,
    )
    from .core.cache import ResponseCache
    from .core.session import LLMSession
    from .core.cryptions import (
        KeyMaterial, EnvKeyProvider,
        PlainTextStrategy, AESDecryptStrategy, RSADecryptStrategy,
        aes_encrypt, rsa_encrypt,
    )


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

    # batch (bounded concurrency + retry + optional cache)
    "ask_many",

    # response cache
    "ResponseCache",

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

    # secure key management
    "KeyMaterial",
    "EnvKeyProvider",
    "PlainTextStrategy",
    "AESDecryptStrategy",
    "RSADecryptStrategy",
    "aes_encrypt",
    "rsa_encrypt",

    # meta
    "__version__",
]
