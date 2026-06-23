"""Model name literals for type-safe LLM API calls.

Import these in your own code for IDE auto-complete and type checking:

    from ntu_easy_llm import ChatGPTModel, GeminiModel, AnthropicModel
"""
from typing import Literal, Union

# ---------------------------------------------------------------------------
# OpenAI / ChatGPT
# ---------------------------------------------------------------------------

ChatGPTModel = Literal[
    "gpt-5.2",
    "gpt-5.2-pro",
    "gpt-5.1",
    "gpt-5",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4o-mini-search-preview",
    "gpt-4o-search-preview",
    "gpt-4-turbo",
]

# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

GeminiModel = Literal[
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# ---------------------------------------------------------------------------
# Anthropic Claude
# ---------------------------------------------------------------------------

AnthropicModel = Literal[
    "claude-opus-4-5-20251101",    # latest flagship
    "claude-sonnet-4-5-20250929",  # general purpose
    "claude-haiku-4-5-20251001",   # fast / lightweight
    "claude-opus-4-1-20250805",
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-3-haiku-20240307",
    "claude-3-5-haiku-20241022",
    "claude-3-7-sonnet-20250219",
]

# ---------------------------------------------------------------------------
# Union
# ---------------------------------------------------------------------------

AnyModel = Union[ChatGPTModel, GeminiModel, AnthropicModel]
