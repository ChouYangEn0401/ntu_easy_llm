"""Core utilities for unified LLM API access.

This module provides:
- Stateless single-shot ask functions  (ask_chatgpt / ask_gemini / ask_anthropic)
- Async variants with end_callback support (ask_chatgpt_async / …)
- Unified ask() dispatcher that accepts an explicit API key
- Adapter classes for dependency-injection style usage
- Model listing helpers
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Callable, Literal, Sequence

from anthropic import Anthropic
from google import genai
from openai import OpenAI

from .config_loader import load_api_key
from .cryptions import _decode_aes
from .models import AnyModel, AnthropicModel, ChatGPTModel, GeminiModel
from .response_utils import (
    parse_anthropic_response,
    parse_gemini_response,
    parse_openai_response,
)

if TYPE_CHECKING:
    from .cache import ResponseCache

# Module-level thread pool shared by all stateless async helpers.
# For session-level concurrency see LLMSession._executor.
_thread_pool = ThreadPoolExecutor(max_workers=8)

# Recommended default model per provider (lower-case provider keys).
_DEFAULT_MODELS: dict[str, str] = {
    "chatgpt": "gpt-4.1",
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
}


def _cached(
    cache: "ResponseCache | None",
    cache_key: str | None,
    provider: str,
    model: str,
    prompt: str,
    web_search: bool,
    compute: "Callable[[], str]",
) -> str:
    """Return a cached answer if present, else compute it and store it.

    The key is *cache_key* when given (a caller-chosen semantic key), otherwise
    a hash of ``(provider, model, prompt, web_search)``.
    """
    if cache is None:
        return compute()
    key = cache_key or cache.make_key(provider, model, prompt, web_search)
    hit = cache.get(key)
    if hit is not None:
        return hit
    result = compute()
    cache.set(key, result)
    return result


# =============================================================================
# API Key Resolution
# =============================================================================

def _resolve_api_key(
    provider: str,
    api_key: str | None,
    password: str | None,
) -> str:
    """Return a ready-to-use API key.

    Priority: explicit *api_key* argument > .env file lookup.
    If *password* is provided the resolved key is AES-decrypted first.
    """
    raw = api_key if api_key else load_api_key(tag=provider.lower())
    return _decode_aes(raw, password) if password else raw


# =============================================================================
# Internal Provider Calls  (accept a full message list for multi-turn support)
# =============================================================================

def _call_chatgpt(
    api_key: str,
    messages: list[dict],
    model_name: str,
    web_search: bool = False,
) -> str:
    """Call the OpenAI Responses API with optional web search.

    Passes the full *messages* list so session history is preserved across
    multi-turn conversations.  Uses ``responses.create`` (the current OpenAI
    API) rather than the legacy ``chat.completions.create``.
    """
    client = OpenAI(api_key=api_key)
    kwargs: dict = dict(model=model_name, input=messages)
    if web_search:
        kwargs["tools"] = [{"type": "web_search"}]
    resp = client.responses.create(**kwargs)
    return parse_openai_response(resp)


def _call_gemini(
    api_key: str,
    messages: list[dict],
    model_name: str,
    web_search: bool = False,
) -> str:
    """Call the Google Gemini generate_content API with optional web search."""
    client = genai.Client(api_key=api_key)

    # Gemini uses "user" / "model" roles; system goes into config
    system_instruction: str | None = None
    contents: list = []
    for m in messages:
        if m["role"] == "system":
            system_instruction = m["content"]
        else:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

    from google.genai import types as _gt  # lazy import keeps startup fast

    config_kwargs: dict = {}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if web_search:
        config_kwargs["tools"] = [_gt.Tool(google_search=_gt.GoogleSearch())]

    config = _gt.GenerateContentConfig(**config_kwargs) if config_kwargs else None
    resp = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=config,
    )
    return parse_gemini_response(resp)


def _call_anthropic(
    api_key: str,
    messages: list[dict],
    model_name: str,
) -> str:
    """Call the Anthropic Messages API.

    Anthropic requires the system prompt to be a top-level parameter rather
    than a message with role="system", so it is separated out automatically.
    """
    client = Anthropic(api_key=api_key)

    system_prompt: str | None = None
    chat_messages: list = []
    for m in messages:
        if m["role"] == "system":
            system_prompt = m["content"]
        else:
            chat_messages.append({"role": m["role"], "content": m["content"]})

    kwargs: dict = dict(model=model_name, max_tokens=4096, messages=chat_messages)
    if system_prompt:
        kwargs["system"] = system_prompt

    resp = client.messages.create(**kwargs)
    return parse_anthropic_response(resp)


# =============================================================================
# Stateless Public Ask APIs  (single-shot, blocking)
# =============================================================================

def ask_chatgpt(
    prompt: str,
    model_name: ChatGPTModel = "gpt-4.1",
    web_search: bool = False,
    password: str | None = None,
    cache: "ResponseCache | None" = None,
    cache_key: str | None = None,
) -> str:
    """Ask ChatGPT a single question and return the answer (blocking).

    Parameters
    ----------
    prompt:
        Your question or instruction.
    model_name:
        ChatGPT model to use.  Defaults to ``"gpt-4.1"``.
    web_search:
        When *True* the model is allowed to search the web before answering.
    password:
        AES password used to decrypt an encrypted API key stored in .env.
    cache:
        Optional :class:`~ntu_easy_llm.ResponseCache`. When supplied, a cached
        answer is returned instead of calling the API; new answers are stored.
    cache_key:
        Optional caller-chosen key for the cache entry. Defaults to a hash of
        ``(provider, model, prompt, web_search)``.
    """
    def _compute() -> str:
        key = _resolve_api_key("chatgpt", None, password)
        return _call_chatgpt(key, [{"role": "user", "content": prompt}], model_name, web_search)

    return _cached(cache, cache_key, "chatgpt", model_name, prompt, web_search, _compute)


def ask_chatgpt_async(
    prompt: str,
    end_callback: Callable[[str], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    model_name: ChatGPTModel = "gpt-4.1",
    web_search: bool = False,
    password: str | None = None,
) -> "Future[str]":
    """Ask ChatGPT a single question without blocking the calling thread.

    Returns a :class:`~concurrent.futures.Future`.  Use *end_callback* to handle
    the result or *on_error* to handle exceptions.

    Parameters
    ----------
    end_callback:
        ``end_callback(response: str)`` — called on the worker thread when done.
    on_error:
        ``on_error(exc: Exception)`` — called when the request raises.
    """
    future: Future[str] = _thread_pool.submit(
        ask_chatgpt, prompt,
        model_name=model_name,
        web_search=web_search,
        password=password,
    )
    _attach_callbacks(future, end_callback, on_error)
    return future


def ask_gemini(
    prompt: str,
    model_name: GeminiModel = "gemini-2.5-flash",
    web_search: bool = False,
    password: str | None = None,
    cache: "ResponseCache | None" = None,
    cache_key: str | None = None,
) -> str:
    """Ask Gemini a single question and return the answer (blocking).

    Parameters
    ----------
    web_search:
        When *True* activates Google Search grounding.
    cache:
        Optional :class:`~ntu_easy_llm.ResponseCache` (see :func:`ask_chatgpt`).
    cache_key:
        Optional caller-chosen cache key.
    """
    def _compute() -> str:
        key = _resolve_api_key("gemini", None, password)
        return _call_gemini(key, [{"role": "user", "content": prompt}], model_name, web_search)

    return _cached(cache, cache_key, "gemini", model_name, prompt, web_search, _compute)


def ask_gemini_async(
    prompt: str,
    end_callback: Callable[[str], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    model_name: GeminiModel = "gemini-2.5-flash",
    web_search: bool = False,
    password: str | None = None,
) -> "Future[str]":
    """Ask Gemini a single question without blocking the calling thread."""
    future: Future[str] = _thread_pool.submit(
        ask_gemini, prompt,
        model_name=model_name,
        web_search=web_search,
        password=password,
    )
    _attach_callbacks(future, end_callback, on_error)
    return future


def ask_anthropic(
    prompt: str,
    model_name: AnthropicModel = "claude-haiku-4-5-20251001",
    password: str | None = None,
    cache: "ResponseCache | None" = None,
    cache_key: str | None = None,
) -> str:
    """Ask Anthropic Claude a single question and return the answer (blocking).

    Parameters
    ----------
    cache:
        Optional :class:`~ntu_easy_llm.ResponseCache` (see :func:`ask_chatgpt`).
    cache_key:
        Optional caller-chosen cache key.
    """
    def _compute() -> str:
        key = _resolve_api_key("anthropic", None, password)
        return _call_anthropic(key, [{"role": "user", "content": prompt}], model_name)

    return _cached(cache, cache_key, "anthropic", model_name, prompt, False, _compute)


def ask_anthropic_async(
    prompt: str,
    end_callback: Callable[[str], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    model_name: AnthropicModel = "claude-haiku-4-5-20251001",
    password: str | None = None,
) -> "Future[str]":
    """Ask Anthropic Claude a single question without blocking the calling thread."""
    future: Future[str] = _thread_pool.submit(
        ask_anthropic, prompt,
        model_name=model_name,
        password=password,
    )
    _attach_callbacks(future, end_callback, on_error)
    return future


# =============================================================================
# Unified Ask  (explicit API key, legacy-compatible)
# =============================================================================

def ask(
    service_provider: Literal["CHATGPT", "GEMINI", "ANTHROPIC"],
    api_key: str,
    prompt: str,
    model_name: AnyModel,
    web_search: bool = False,
) -> str:
    """Dispatch a single question to any provider using an explicit API key.

    Useful when you manage keys yourself instead of relying on .env files.
    """
    messages = [{"role": "user", "content": prompt}]
    if service_provider == "CHATGPT":
        return _call_chatgpt(api_key, messages, model_name, web_search)  # type: ignore[arg-type]
    elif service_provider == "GEMINI":
        return _call_gemini(api_key, messages, model_name, web_search)   # type: ignore[arg-type]
    elif service_provider == "ANTHROPIC":
        return _call_anthropic(api_key, messages, model_name)            # type: ignore[arg-type]
    else:
        raise ValueError(
            f"Unknown provider: {service_provider!r}. "
            "Use 'CHATGPT', 'GEMINI', or 'ANTHROPIC'."
        )


# =============================================================================
# Batch  (bounded concurrency + retry + optional cache)
# =============================================================================

def ask_many(
    prompts: Sequence[str],
    provider: Literal["chatgpt", "gemini", "anthropic"] = "chatgpt",
    model: str | None = None,
    *,
    max_concurrent: int = 4,
    retries: int = 2,
    backoff: float = 1.0,
    wait_seconds: float = 0.0,
    web_search: bool = False,
    password: str | None = None,
    on_result: Callable[[int, str, str], None] | None = None,
    cache: "ResponseCache | None" = None,
    cache_keys: Sequence[str] | None = None,
) -> list[str]:
    """Ask many prompts concurrently and return the answers **in input order**.

    Concurrency is bounded by *max_concurrent* — the single most important knob
    for staying under provider rate limits, so it is yours to set. Each call is
    retried up to *retries* times with exponential backoff; supply a *cache* to
    skip prompts that were already answered on a previous run.

    Parameters
    ----------
    prompts:
        The prompts to send.
    provider:
        ``"chatgpt"``, ``"gemini"``, or ``"anthropic"`` (case-insensitive).
    model:
        Model to use. Defaults to the provider's recommended model.
    max_concurrent:
        Maximum number of requests in flight at once (rate-limit control).
    retries:
        Extra attempts per prompt on error (total tries = ``retries + 1``).
    backoff:
        Base seconds for exponential backoff between retries (``backoff * 2**n``).
    wait_seconds:
        Optional pause after each call to further throttle throughput.
    web_search:
        Enable web search (ChatGPT / Gemini only; ignored for Anthropic).
    password:
        AES password to decrypt an encrypted key in ``.env``.
    on_result:
        ``on_result(index, prompt, answer)`` — called on a worker thread as each
        result arrives (useful for progress bars / incremental writing).
    cache:
        Optional :class:`~ntu_easy_llm.ResponseCache`. Cached prompts are not
        re-sent; new answers are stored, and the cache is saved once at the end
        (so an interrupted run can resume).
    cache_keys:
        Optional per-prompt semantic keys (same length as *prompts*). Defaults
        to hashing each prompt.

    Returns
    -------
    list[str]
        Answers aligned with *prompts*.

    Notes
    -----
    If a prompt still fails after all retries the exception propagates. When a
    *cache* is supplied the answers gathered before the failure are persisted,
    so re-running resumes from where it stopped.
    """
    provider_l = provider.lower()
    if provider_l not in _DEFAULT_MODELS:
        raise ValueError(
            f"Unknown provider: {provider!r}. "
            "Use 'chatgpt', 'gemini', or 'anthropic'."
        )
    model = model or _DEFAULT_MODELS[provider_l]

    prompt_list = list(prompts)
    if cache_keys is not None:
        cache_keys = list(cache_keys)
        if len(cache_keys) != len(prompt_list):
            raise ValueError("cache_keys must be the same length as prompts.")

    api_key = _resolve_api_key(provider_l, None, password)
    _provider_call = {
        "chatgpt": lambda p: _call_chatgpt(api_key, [{"role": "user", "content": p}], model, web_search),
        "gemini": lambda p: _call_gemini(api_key, [{"role": "user", "content": p}], model, web_search),
        "anthropic": lambda p: _call_anthropic(api_key, [{"role": "user", "content": p}], model),
    }[provider_l]

    def _one(index: int, prompt: str) -> str:
        ck = cache_keys[index] if cache_keys is not None else None

        def _compute() -> str:
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    return _provider_call(prompt)
                except Exception as exc:  # noqa: BLE001 — retry then re-raise
                    last_exc = exc
                    if attempt < retries:
                        time.sleep(backoff * (2 ** attempt))
            assert last_exc is not None
            raise last_exc

        result = _cached(cache, ck, provider_l, model, prompt, web_search, _compute)
        if wait_seconds:
            time.sleep(wait_seconds)
        if on_result is not None:
            on_result(index, prompt, result)
        return result

    results: list[str] = [""] * len(prompt_list)
    try:
        with ThreadPoolExecutor(max_workers=max(1, max_concurrent)) as pool:
            futures = {
                pool.submit(_one, i, p): i
                for i, p in enumerate(prompt_list)
            }
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()
    finally:
        if cache is not None:
            cache.save()
    return results


# =============================================================================
# Internal helper
# =============================================================================

def _attach_callbacks(
    future: "Future",
    end_callback: "Callable[[str], None] | None",
    on_error: "Callable[[Exception], None] | None",
) -> None:
    if not (end_callback or on_error):
        return

    def _done(f: "Future") -> None:
        exc = f.exception()
        if exc:
            if on_error:
                on_error(exc)
        else:
            if end_callback:
                end_callback(f.result())

    future.add_done_callback(_done)


# =============================================================================
# Model Utilities
# =============================================================================

def resolve_claude_model(client: Anthropic, requested: str) -> str:
    """Return *requested* if available, otherwise fall back to haiku."""
    available = {m.id for m in client.models.list().data}
    if requested in available:
        return requested
    fallback = "claude-haiku-4-5-20251001"
    if fallback in available:
        return fallback
    raise RuntimeError(
        f"Model '{requested}' is not available for this API key.\n"
        f"Available: {sorted(available)}"
    )


def list_chatgpt_models(api_key: str) -> list[str]:
    """Return a list of OpenAI model IDs accessible with *api_key*."""
    return [m.id for m in OpenAI(api_key=api_key).models.list().data]


def list_gemini_models(api_key: str) -> list[str]:
    """Return a list of Gemini model names accessible with *api_key*."""
    return [m.name for m in genai.Client(api_key=api_key).models.list()]


def list_anthropic_models(api_key: str) -> list[str]:
    """Return a list of Anthropic model IDs accessible with *api_key*."""
    return [m.id for m in Anthropic(api_key=api_key).models.list().data]


# =============================================================================
# Adapter Classes  (dependency-injection / OOP style)
# =============================================================================

class LLMAdapter(ABC):
    """Abstract base for single-turn, stateless provider adapters."""

    def __init__(self, api_key: str, model_name: AnyModel):
        self.api_key = api_key
        self.model_name = model_name

    @abstractmethod
    def ask(self, prompt: str) -> str:
        """Send *prompt* and return the response string."""


class ChatGPTAdapter(LLMAdapter):
    """Single-turn ChatGPT adapter with optional web search."""

    def __init__(
        self,
        api_key: str,
        model_name: ChatGPTModel = "gpt-4.1",
        web_search: bool = False,
    ):
        super().__init__(api_key, model_name)
        self.web_search = web_search

    def ask(self, prompt: str) -> str:
        return _call_chatgpt(
            self.api_key,
            [{"role": "user", "content": prompt}],
            self.model_name,
            self.web_search,
        )


class GeminiAdapter(LLMAdapter):
    """Single-turn Gemini adapter with optional web search."""

    def __init__(
        self,
        api_key: str,
        model_name: GeminiModel = "gemini-2.5-flash",
        web_search: bool = False,
    ):
        super().__init__(api_key, model_name)
        self.web_search = web_search

    def ask(self, prompt: str) -> str:
        return _call_gemini(
            self.api_key,
            [{"role": "user", "content": prompt}],
            self.model_name,
            self.web_search,
        )


class AnthropicAdapter(LLMAdapter):
    """Single-turn Anthropic Claude adapter."""

    def __init__(
        self,
        api_key: str,
        model_name: AnthropicModel = "claude-haiku-4-5-20251001",
    ):
        super().__init__(api_key, model_name)

    def ask(self, prompt: str) -> str:
        return _call_anthropic(
            self.api_key,
            [{"role": "user", "content": prompt}],
            self.model_name,
        )

