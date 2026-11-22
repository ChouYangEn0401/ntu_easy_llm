"""The adapter contract and registry (added in 0.6).

An *adapter* talks to one source (``local/ollama``, ``remote/api_key/openai``,
…) for one :class:`~ntu_easy_llm.profiles.Profile`. Every adapter has the same
four methods, so callers — and the waterfall router, which wraps several
adapters and is itself shaped like one — never special-case a provider.

Contract every adapter keeps
----------------------------
- ``chat`` takes neutral messages (:class:`~ntu_easy_llm.types.Message` or
  0.5-style dicts) and returns a :class:`~ntu_easy_llm.types.ChatReply`
  with ``finish`` filled in (see :func:`~ntu_easy_llm.types.normalize_finish`).
- Every failure is an :class:`~ntu_easy_llm.agent.LLMError` whose ``kind`` is
  set and whose ``profile_id`` is the adapter's profile id.
- Unsupported features: tools on a source whose ``capabilities().tools`` is
  ``False`` → ``LLMError(kind=TOOLS_UNSUPPORTED)``; images when ``vision`` is
  ``False`` → ``LLMError(kind=BAD_REQUEST)``; ``stop`` when ``stop`` is
  ``False`` → ignored, with a line in ``ChatReply.warnings``. ``None`` means
  "unknown" — send it and let the server decide.
- ``probe(send=False)`` only reads (lists models, checks reachability); it
  must not load a model or spend tokens. ``send=True`` sends one tiny request.
  ``probe`` never raises for provider/network problems.
- Secrets are fetched through the ``resolve_secret`` callable the factory gets,
  as late as possible, and never logged or put into error messages.
- Importing an adapter module never imports a provider SDK; anything heavy is
  imported inside the factory or the call.

Registry
--------
Built-in adapters are listed in ``_BUILTIN`` (``source -> "module:factory"``,
imported on first use). Third-party packages register through the entry point
group ``ntu_easy_llm.adapters``; the entry point *name* is the source path with
``/`` or ``.`` as separator (``remote.api_key.example``), the *value* the
factory. Code can also call :func:`register_adapter`.

A factory is ``factory(profile, *, http=None, resolve_secret) -> Adapter``. It
may carry an optional attribute ``check`` — a zero-argument callable returning
``(usable: bool, reason: str)`` — used by the catalog to grey out sources whose
dependencies are missing on this machine.
"""
from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from ..agent import ChatReply, Tool
from ..errors import AdapterNotFound, ErrorKind
from ..profiles import Profile, SecretResolver, make_secret_resolver
from ..types import Message

__all__ = [
    "Adapter", "Capabilities", "ModelInfo", "ProbeResult",
    "AdapterFactory", "ENTRY_POINT_GROUP",
    "register_adapter", "unregister_adapter", "get_adapter", "adapter_status", "registered_sources",
]

ENTRY_POINT_GROUP = "ntu_easy_llm.adapters"


@dataclass(frozen=True)
class Capabilities:
    """What a source can do. ``None`` = unknown (depends on the model the server runs)."""

    tools: bool | None = None
    vision: bool | None = None
    stop: bool = False
    stream: bool = False          # ``on_delta`` gets real incremental text
    usage: bool = False           # replies report token usage


@dataclass(frozen=True)
class ModelInfo:
    """One model a source offers. ``kind``: ``"chat"`` | ``"embedding"`` | ``"unknown"``."""

    id: str
    label: str = ""
    context: int | None = None
    loaded: bool | None = None    # local servers: is it in memory right now
    vision: bool | None = None
    tools: bool | None = None
    kind: str = "chat"


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of :meth:`Adapter.probe`. ``reason`` is one line for a human."""

    ok: bool
    reason: str = ""
    kind: ErrorKind | None = None
    models: tuple[ModelInfo, ...] = ()
    latency_s: float | None = None


@runtime_checkable
class Adapter(Protocol):
    """What every adapter (and the waterfall router) looks like to a caller."""

    profile: Profile

    def capabilities(self) -> Capabilities: ...

    def chat(self, messages: Sequence[Message | Mapping[str, Any]],
             tools: Sequence[Tool | Mapping[str, Any]] | None = None, *,
             max_tokens: int | None = None, temperature: float | None = None, stop: Sequence[str] = (),
             timeout: float | None = None, on_delta: Callable[[str], None] | None = None) -> ChatReply: ...

    def list_models(self, *, timeout: float = 15.0) -> list[ModelInfo]: ...

    def probe(self, *, send: bool = False, timeout: float = 30.0) -> ProbeResult: ...


AdapterFactory = Callable[..., Adapter]

# source path -> "module:attribute" (imported on first use)
_BUILTIN: dict[str, str] = {
    "remote/api_key/llop": "ntu_easy_llm.adapters.llop:from_profile",
    "remote/subscription/claude": "ntu_easy_llm.adapters.subscription:claude",
    "remote/subscription/github_copilot": "ntu_easy_llm.adapters.subscription:github_copilot",
    "remote/subscription/chatgpt": "ntu_easy_llm.adapters.subscription:chatgpt",

    "local/fake": "ntu_easy_llm.adapters.fake:from_profile",
}

# HTTP adapters (standard library only; the classes are their own factories)
_BUILTIN.update({
    "local/lmstudio": "ntu_easy_llm.adapters.openai_style:LMStudioAdapter",
    "local/ollama": "ntu_easy_llm.adapters.ollama:OllamaAdapter",
    "local/vllm": "ntu_easy_llm.adapters.openai_style:VLLMAdapter",
    "local/llamacpp": "ntu_easy_llm.adapters.openai_style:LlamaCppAdapter",
    "local/openai_compatible": "ntu_easy_llm.adapters.openai_style:LocalOpenAICompatibleAdapter",
    "remote/api_key/openai": "ntu_easy_llm.adapters.openai_style:OpenAIAdapter",
    "remote/api_key/anthropic": "ntu_easy_llm.adapters.anthropic:AnthropicAdapter",
    "remote/api_key/gemini": "ntu_easy_llm.adapters.openai_style:GeminiAdapter",
    "remote/api_key/openai_compatible": "ntu_easy_llm.adapters.openai_style:OpenAICompatibleAdapter",
})

_LOCK = threading.RLock()
_REGISTRY: dict[str, AdapterFactory | str] = {}
_ENTRY_POINTS: dict[str, Any] | None = None   # source -> EntryPoint (not loaded)


def _norm(source: str) -> str:
    return str(source or "").strip().replace(".", "/").lower()


def _entry_points() -> dict[str, Any]:
    global _ENTRY_POINTS
    with _LOCK:
        if _ENTRY_POINTS is None:
            found: dict[str, Any] = {}
            try:
                from importlib.metadata import entry_points
                for ep in entry_points(group=ENTRY_POINT_GROUP):
                    found.setdefault(_norm(ep.name), ep)
            except Exception:  # noqa: BLE001 — a broken plugin listing must not break the core
                found = {}
            _ENTRY_POINTS = found
        return _ENTRY_POINTS


def register_adapter(source: str, factory: AdapterFactory | str, *, replace: bool = False) -> None:
    """Register ``factory`` (or a ``"module:attr"`` string) for a source path.

    Registering a source that already has a factory raises ``ValueError``
    unless ``replace=True`` (built-ins and entry points can be overridden).
    """
    key = _norm(source)
    with _LOCK:
        if key in _REGISTRY and not replace:
            raise ValueError(f"an adapter for {key!r} is already registered (pass replace=True)")
        _REGISTRY[key] = factory


def unregister_adapter(source: str) -> None:
    """Remove a factory added with :func:`register_adapter` (built-ins stay)."""
    with _LOCK:
        _REGISTRY.pop(_norm(source), None)


def _lookup(source: str) -> AdapterFactory | str | Any | None:
    key = _norm(source)
    with _LOCK:
        if key in _REGISTRY:
            return _REGISTRY[key]
    if key in _BUILTIN:
        return _BUILTIN[key]
    return _entry_points().get(key)


def _load_factory(found: Any) -> AdapterFactory:
    if isinstance(found, str):
        module, _, attr = found.partition(":")
        return getattr(importlib.import_module(module), attr)
    if hasattr(found, "load") and hasattr(found, "group"):    # importlib.metadata.EntryPoint
        return found.load()
    return found


def registered_sources() -> list[str]:
    """Every source path that has a factory (registered, built in, or via entry points)."""
    with _LOCK:
        names = set(_REGISTRY)
    return sorted(names | set(_BUILTIN) | set(_entry_points()))


def adapter_status(source: str) -> tuple[bool, str]:
    """``(usable, reason)`` for a source on this machine, without building an adapter.

    Loads the factory (cheap: adapter modules do not import SDKs) and runs its
    optional ``check``.
    """
    found = _lookup(source)
    if found is None:
        return False, "no adapter for this source in this version"
    try:
        factory = _load_factory(found)
    except Exception as e:  # noqa: BLE001
        return False, f"adapter failed to load: {type(e).__name__}"
    check = getattr(factory, "check", None)
    if check is None:
        return True, ""
    try:
        ok, reason = check()
    except Exception as e:  # noqa: BLE001
        return False, f"adapter check failed: {type(e).__name__}"
    return bool(ok), str(reason or "")


def get_adapter(profile: Profile, *, http: Any = None, resolve_secret: Callable[[str], str | None] | None = None,
                environ: Mapping[str, str] | None = None) -> Adapter:
    """Build the adapter for ``profile``.

    ``http`` is an injectable transport for tests (its shape is up to the
    adapter family; the HTTP adapters accept a requests-style session).
    ``resolve_secret`` / ``environ`` are combined into the resolver the
    adapter uses for ``profile.secret`` (see :func:`~ntu_easy_llm.profiles.make_secret_resolver`).

    Raises :class:`~ntu_easy_llm.errors.AdapterNotFound` when no adapter
    exists for the source or its ``check`` says it cannot run here.
    """
    found = _lookup(profile.source)
    if found is None:
        raise AdapterNotFound(f"profile {profile.id}: no adapter for source {profile.source!r} in this version")
    factory = _load_factory(found)
    check = getattr(factory, "check", None)
    if check is not None:
        ok, reason = check()
        if not ok:
            raise AdapterNotFound(f"profile {profile.id}: {profile.source} is not usable here: {reason}")
    resolver: SecretResolver = make_secret_resolver(environ=environ, resolve_secret=resolve_secret)
    return factory(profile, http=http, resolve_secret=resolver)
