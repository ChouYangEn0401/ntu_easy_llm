## 套件對外的 API
"""
ntu_easy_llm public API
"""
from .core.utils import ask_chatgpt, ask_gemini
from ._version import __version__

__all__ = [
    "ask_chatgpt",
    "ask_gemini",
]
