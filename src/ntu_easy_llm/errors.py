"""Error kinds shared by every adapter (added in 0.6).

Every error raised by :mod:`ntu_easy_llm.agent` and by the 0.6 adapters is an
:class:`~ntu_easy_llm.agent.LLMError` that carries:

- ``kind``         one :class:`ErrorKind` — *what went wrong* in provider-neutral terms
- ``retry_after``  seconds the server asked us to wait (``Retry-After``), or ``None``
- ``profile_id``   which configured profile failed (``""`` when not known)

The kinds are what a caller (and the waterfall) decides on: "the quota is used
up" and "slow down" are both HTTP 429, but only the first one means "try
another source".

:func:`classify` is the generic, provider-neutral rule set. Adapters for one
provider can add their own rules with :func:`register_classifier`; those run
first and fall back to the generic rules when they return ``None``.

This module must stay import-light: :mod:`ntu_easy_llm.agent` imports it, so it
never imports ``agent`` at module level (the ``LLMError`` family is re-exported
lazily, see ``__getattr__`` at the bottom).
"""
from __future__ import annotations

import email.utils
import threading
import time
import urllib.error
from enum import StrEnum
from typing import Any, Callable, Mapping

__all__ = [
    "ErrorKind", "DEFAULT_TRIGGERS", "UNTRIGGERABLE",
    "ConfigError", "AdapterNotFound",
    "classify", "kind_of", "parse_retry_after", "register_classifier", "describe_kind",
    # re-exported lazily from ntu_easy_llm.agent / ntu_easy_llm.waterfall
    "LLMError", "LLMHTTPError", "LLMConnectionError", "LLMResponseError",
    "FallbackRequired", "WaterfallExhausted",
]


class ErrorKind(StrEnum):
    """Provider-neutral reason an LLM call failed. ``str(kind)`` is the value."""

    QUOTA_EXHAUSTED = "quota_exhausted"      # quota / credit / key budget used up
    AUTH_INVALID = "auth_invalid"            # key wrong, expired or revoked
    RATE_LIMITED = "rate_limited"            # too many requests right now (429 that is not quota)
    PERMISSION = "permission"                # 403: the key may not do this
    OVERLOADED = "overloaded"                # 503 / 529 / queue full
    CONTEXT_TOO_LONG = "context_too_long"    # prompt does not fit the model's context
    MODEL_NOT_FOUND = "model_not_found"
    TOOLS_UNSUPPORTED = "tools_unsupported"  # the server/model rejected the tools parameter
    BAD_REQUEST = "bad_request"              # other 4xx
    TIMEOUT = "timeout"                      # client or gateway timeout (incl. 504 / 524)
    CONNECTION = "connection"                # cannot reach the endpoint (DNS, refused, 530…)
    LINK_CHANGED = "link_changed"            # a relay's published link moved (set by that adapter)
    TRUST = "trust"                          # encryption / key-pinning check failed — never auto-switch
    SERVER_ERROR = "server_error"            # other 5xx
    BAD_RESPONSE = "bad_response"            # 200 but not the expected shape
    UNKNOWN = "unknown"


#: Kinds that, by default, make a waterfall move on to the next source.
#: RATE_LIMITED only counts after the 429 retries are used up (the router does that).
DEFAULT_TRIGGERS: frozenset[ErrorKind] = frozenset(
    {ErrorKind.QUOTA_EXHAUSTED, ErrorKind.AUTH_INVALID, ErrorKind.RATE_LIMITED})

#: Kinds that may never be configured as a waterfall trigger (security problems
#: must surface, not be silently routed around).
UNTRIGGERABLE: frozenset[ErrorKind] = frozenset({ErrorKind.TRUST})


class ConfigError(ValueError):
    """A configuration problem (bad profile file, missing environment variable, …).

    Messages never contain secret values — only field paths and variable names.
    """


class AdapterNotFound(ConfigError):
    """No adapter is registered (or usable on this machine) for a profile's source."""


# --------------------------------------------------------------------------- #
# Retry-After
# --------------------------------------------------------------------------- #

def _header(headers: Any, name: str) -> str | None:
    if not headers:
        return None
    getter = getattr(headers, "items", None)
    if getter is None:
        return None
    name = name.lower()
    for k, v in getter():
        if str(k).lower() == name:
            return str(v)
    return None


def parse_retry_after(headers: Any, *, now: float | None = None) -> float | None:
    """Seconds to wait from ``retry-after-ms`` / ``Retry-After`` (seconds or HTTP date).

    ``headers`` is any mapping (case-insensitive lookup). Returns ``None`` when
    absent or unparsable; never negative.
    """
    ms = _header(headers, "retry-after-ms")
    if ms is not None:
        try:
            return max(0.0, float(ms) / 1000.0)
        except ValueError:
            pass
    raw = _header(headers, "retry-after")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #

Classifier = Callable[..., "tuple[ErrorKind, float | None] | None"]
_CLASSIFIERS: dict[str, Classifier] = {}
_LOCK = threading.Lock()

_QUOTA_MARKERS = ("insufficient_quota", "quota_exceeded", "key_exhausted", "exceeded your current quota",
                  "credit balance", "billing_hard_limit", "out of credits")
_AUTH_MARKERS = ("invalid_api_key", "api_key_invalid", "key_expired", "invalid x-api-key",
                 "authentication_error", "incorrect api key")
_CONTEXT_MARKERS = ("context_length_exceeded", "maximum context length", "context size has been exceeded",
                    "prompt is too long", "context window")
_MODEL_MARKERS = ("model_not_found", "no such model", "model not found")
_TOOL_MARKERS = ("tool", "function")


def register_classifier(provider: str, fn: Classifier | None) -> None:
    """Add provider-specific rules (``fn=None`` removes them).

    ``fn(*, status, body, headers, exc, tools_sent)`` returns
    ``(ErrorKind, retry_after_or_None)`` or ``None`` to fall through to the
    generic rules. ``provider`` is matched case-insensitively against the
    ``provider`` argument of :func:`classify`.
    """
    key = str(provider or "").strip().lower()
    with _LOCK:
        if fn is None:
            _CLASSIFIERS.pop(key, None)
        else:
            _CLASSIFIERS[key] = fn


def _has(body: str, markers: tuple[str, ...]) -> bool:
    return any(m in body for m in markers)


def _classify_exception(exc: BaseException) -> ErrorKind:
    if isinstance(exc, TimeoutError):
        return ErrorKind.TIMEOUT
    if isinstance(exc, urllib.error.URLError) and isinstance(getattr(exc, "reason", None), TimeoutError):
        return ErrorKind.TIMEOUT
    if "timed out" in str(exc).lower():
        return ErrorKind.TIMEOUT
    if isinstance(exc, (ConnectionError, urllib.error.URLError, OSError)):
        return ErrorKind.CONNECTION
    if isinstance(exc, ValueError):          # includes json.JSONDecodeError
        return ErrorKind.BAD_RESPONSE
    return ErrorKind.UNKNOWN


def _classify_status(status: int, body: str, tools_sent: bool) -> ErrorKind:
    low = body.lower()
    if 200 <= status < 400:
        return ErrorKind.BAD_RESPONSE
    if 400 <= status < 500:
        if _has(low, _QUOTA_MARKERS) or status == 402:
            return ErrorKind.QUOTA_EXHAUSTED
        if status == 401 or _has(low, _AUTH_MARKERS):
            return ErrorKind.AUTH_INVALID
        if status == 429:
            return ErrorKind.RATE_LIMITED
        if status == 403:
            return ErrorKind.PERMISSION
        if status == 408:
            return ErrorKind.TIMEOUT
        if status == 413 or _has(low, _CONTEXT_MARKERS):
            return ErrorKind.CONTEXT_TOO_LONG
        if _has(low, _MODEL_MARKERS) or (status == 404 and "model" in low):
            return ErrorKind.MODEL_NOT_FOUND
        if tools_sent and status in (400, 422) and _has(low, _TOOL_MARKERS):
            return ErrorKind.TOOLS_UNSUPPORTED
        return ErrorKind.BAD_REQUEST
    if status in (503, 529):
        return ErrorKind.OVERLOADED
    if status in (504, 524):
        return ErrorKind.TIMEOUT
    if status in (520, 521, 522, 523, 530):
        return ErrorKind.CONNECTION
    if status >= 500:
        return ErrorKind.SERVER_ERROR
    return ErrorKind.UNKNOWN


def classify(provider: str = "", *, status: int | None = None, body: str = "",
             headers: Mapping[str, str] | None = None, exc: BaseException | None = None,
             tools_sent: bool = False) -> tuple[ErrorKind, float | None]:
    """Map a failed call to ``(ErrorKind, retry_after_seconds_or_None)``.

    Give whatever is known: the HTTP ``status`` and ``body``, the response
    ``headers`` (for ``Retry-After``), or the exception ``exc``. An
    :class:`LLMError` that already has a specific ``kind`` keeps it.
    ``tools_sent=True`` lets a 400/422 that complains about tools become
    ``TOOLS_UNSUPPORTED``.

    This is the generic rule set (a skeleton every adapter shares); exact
    provider wording is added per provider with :func:`register_classifier`.
    """
    retry = parse_retry_after(headers)
    if exc is not None:
        known = getattr(exc, "kind", None)
        if isinstance(known, ErrorKind) and known is not ErrorKind.UNKNOWN:
            wait = getattr(exc, "retry_after", None)
            return known, wait if wait is not None else retry
        if status is None:
            status = getattr(exc, "status", None)
            body = body or str(getattr(exc, "body", "") or "")
            headers = headers or getattr(exc, "headers", None)
            retry = retry if retry is not None else parse_retry_after(headers)
    fn = _CLASSIFIERS.get(str(provider or "").strip().lower())
    if fn is not None:
        got = fn(status=status, body=body or "", headers=headers or {}, exc=exc, tools_sent=tools_sent)
        if got is not None:
            kind, wait = got
            return ErrorKind(kind), wait if wait is not None else retry
    if status is None or int(status) == 0:
        return (_classify_exception(exc) if exc is not None else ErrorKind.UNKNOWN), retry
    return _classify_status(int(status), body or "", tools_sent), retry


def kind_of(exc: BaseException) -> ErrorKind:
    """The :class:`ErrorKind` of any exception (``LLMError.kind`` or a classification)."""
    return classify(exc=exc)[0]


# --------------------------------------------------------------------------- #
# Human-readable labels (for notices and UIs)
# --------------------------------------------------------------------------- #

_LABELS: dict[str, dict[ErrorKind, str]] = {
    "zh-TW": {
        ErrorKind.QUOTA_EXHAUSTED: "額度用完",
        ErrorKind.AUTH_INVALID: "金鑰失效",
        ErrorKind.RATE_LIMITED: "請求太頻繁",
        ErrorKind.PERMISSION: "沒有權限",
        ErrorKind.OVERLOADED: "服務忙碌",
        ErrorKind.CONTEXT_TOO_LONG: "對話太長",
        ErrorKind.MODEL_NOT_FOUND: "找不到模型",
        ErrorKind.TOOLS_UNSUPPORTED: "不支援工具呼叫",
        ErrorKind.BAD_REQUEST: "請求格式不對",
        ErrorKind.TIMEOUT: "逾時",
        ErrorKind.CONNECTION: "連不上",
        ErrorKind.LINK_CHANGED: "連結已更換",
        ErrorKind.TRUST: "加密或金鑰核對失敗",
        ErrorKind.SERVER_ERROR: "伺服器錯誤",
        ErrorKind.BAD_RESPONSE: "回覆格式不對",
        ErrorKind.UNKNOWN: "未知錯誤",
    },
    "en": {
        ErrorKind.QUOTA_EXHAUSTED: "quota used up",
        ErrorKind.AUTH_INVALID: "key invalid",
        ErrorKind.RATE_LIMITED: "rate limited",
        ErrorKind.PERMISSION: "permission denied",
        ErrorKind.OVERLOADED: "service overloaded",
        ErrorKind.CONTEXT_TOO_LONG: "conversation too long",
        ErrorKind.MODEL_NOT_FOUND: "model not found",
        ErrorKind.TOOLS_UNSUPPORTED: "tool calling not supported",
        ErrorKind.BAD_REQUEST: "bad request",
        ErrorKind.TIMEOUT: "timed out",
        ErrorKind.CONNECTION: "cannot connect",
        ErrorKind.LINK_CHANGED: "link changed",
        ErrorKind.TRUST: "encryption / key check failed",
        ErrorKind.SERVER_ERROR: "server error",
        ErrorKind.BAD_RESPONSE: "unexpected reply",
        ErrorKind.UNKNOWN: "unknown error",
    },
}


def describe_kind(kind: ErrorKind | str, locale: str = "zh-TW") -> str:
    """Short human label for a kind (``zh-TW`` or ``en``; other locales get English)."""
    table = _LABELS.get(locale, _LABELS["en"])
    return table.get(ErrorKind(kind), str(kind))


# --------------------------------------------------------------------------- #
# Lazy re-exports (agent imports this module, so these cannot be top-level imports)
# --------------------------------------------------------------------------- #

_LAZY = {
    "LLMError": "agent", "LLMHTTPError": "agent", "LLMConnectionError": "agent", "LLMResponseError": "agent",
    "FallbackRequired": "waterfall", "WaterfallExhausted": "waterfall",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(f"ntu_easy_llm.{module}"), name)
    globals()[name] = value
    return value
