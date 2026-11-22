"""0.6 adapters: one class per source family, all shaped like :class:`~ntu_easy_llm.adapters.base.Adapter`.

Not to be confused with the 0.4 ``ntu_easy_llm.LLMAdapter`` / ``ChatGPTAdapter``
family (SDK-based ``ask`` helpers), which is unchanged.
"""
from __future__ import annotations

from .base import (ENTRY_POINT_GROUP, Adapter, AdapterFactory, Capabilities, ModelInfo, ProbeResult,
                   adapter_status, get_adapter, register_adapter, registered_sources, unregister_adapter)

__all__ = [
    "Adapter", "AdapterFactory", "Capabilities", "ModelInfo", "ProbeResult", "ENTRY_POINT_GROUP",
    "register_adapter", "unregister_adapter", "get_adapter", "adapter_status", "registered_sources",
    "FakeAdapter",
]


def __getattr__(name: str):
    if name == "FakeAdapter":
        from .fake import FakeAdapter
        return FakeAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
