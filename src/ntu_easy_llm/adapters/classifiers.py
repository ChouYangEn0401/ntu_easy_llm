"""Provider-specific error rules for :func:`ntu_easy_llm.errors.classify` (added in 0.6).

Each function here is registered with :func:`~ntu_easy_llm.errors.register_classifier`
under the provider name the HTTP adapters (and the 0.5 ``agent.chat``) pass to
``classify``. They run before the generic rules and return ``None`` to fall
through to them, so they only *refine* the generic table.

The built-in rules are loaded by :mod:`ntu_easy_llm.errors` the first time
``classify`` or ``register_classifier`` runs, so a caller's own
``register_classifier("openai", ...)`` always wins over the built-in one.

Wording marked 【推測】 (guessed) below comes from public docs and issue
reports, not from a test against the live service; adjust when a real reply
disagrees. Only the standard library is used.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

from ..errors import ErrorKind

__all__ = ["BUILTIN_CLASSIFIERS", "error_fields"]

Result = "tuple[ErrorKind, float | None] | None"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def error_fields(body: str) -> dict[str, str]:
    """Pull ``code`` / ``type`` / ``status`` / ``message`` / ``reason`` out of an error body (lower-cased).

    Understands the OpenAI shape ``{"error": {"code", "type", "message"}}``,
    Anthropic ``{"type": "error", "error": {"type", "message"}}``, Google
    ``{"error": {"code": 400, "status", "message", "details": [{"reason"}]}}``
    (also wrapped in a one-element list), and plain ``{"error": "text"}``.
    Anything unparsable gives ``{}``.
    """
    try:
        data: Any = json.loads(body) if body and body.lstrip()[:1] in "{[" else None
    except ValueError:
        return {}
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, Mapping):
        return {}
    err = data.get("error", data)
    if isinstance(err, str):
        return {"message": err.lower()}
    if not isinstance(err, Mapping):
        return {}
    out = {k: str(err.get(k) or "").lower() for k in ("code", "type", "status", "message", "param")}
    reasons = [str(d.get("reason") or "") for d in (err.get("details") or []) if isinstance(d, Mapping)]
    out["reason"] = " ".join(reasons).lower()
    return out


def _has(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


_DURATION = re.compile(r'"retrydelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def _google_retry_delay(low_body: str) -> float | None:
    """Gemini puts the wait in ``details[].retryDelay`` (``"37s"``) 【推測：欄位名照公開文件】."""
    m = _DURATION.search(low_body)
    return float(m.group(1)) if m else None


# Local servers (LM Studio, Ollama, vLLM, llama.cpp) and generic OpenAI-compatible gateways.
_LOCAL_CONTEXT = (
    "context size has been exceeded",                 # LM Studio
    "context length",                                 # LM Studio / Ollama variants 【推測】 (checked with "exceed")
    "exceeds the available context size",             # llama.cpp server 【推測】
    "exceed_context_size_error",                      # llama.cpp server error type 【推測】
    "maximum context length",                         # vLLM / OpenAI style
    "input length exceeds",                           # Ollama 【推測】
    "greater than the context length",                # LM Studio "tokens to keep ... greater than the context length" 【推測】
)
_LOCAL_MODEL_MISSING = (
    "try pulling it first",                           # Ollama: model "x" not found, try pulling it first
    "no models loaded",                               # LM Studio with JIT loading off 【推測】
    "model_not_found", "model not found",
    "does not exist",                                 # vLLM: The model `x` does not exist. (with "model")
    "invalid model identifier",                       # LM Studio 【推測】
)


def _local(*, status: int | None, body: str, headers: Mapping[str, str], exc: BaseException | None,
            tools_sent: bool) -> "tuple[ErrorKind, float | None] | None":
    if not status or status < 400:
        return None
    low = body.lower()
    if status < 500 or "context" in low:
        if _has(low, _LOCAL_CONTEXT) and ("exceed" in low or "greater than" in low or "maximum" in low):
            return ErrorKind.CONTEXT_TOO_LONG, None
    if status < 500 and "model" in low and _has(low, _LOCAL_MODEL_MISSING):
        return ErrorKind.MODEL_NOT_FOUND, None
    return None


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

def _openai(*, status: int | None, body: str, headers: Mapping[str, str], exc: BaseException | None,
            tools_sent: bool) -> "tuple[ErrorKind, float | None] | None":
    """OpenAI's documented error codes (``error.code`` / ``error.type``)."""
    if not status or status < 400:
        return None
    f = error_fields(body)
    code, etype, msg = f.get("code", ""), f.get("type", ""), f.get("message", "")
    if code == "insufficient_quota" or etype == "insufficient_quota":
        return ErrorKind.QUOTA_EXHAUSTED, None
    if status == 429:
        return ErrorKind.RATE_LIMITED, None           # rate_limit_exceeded and any other 429
    if status == 401 or code == "invalid_api_key":
        return ErrorKind.AUTH_INVALID, None
    if status == 403:
        return ErrorKind.PERMISSION, None             # e.g. unsupported_country_region_territory
    if code == "model_not_found":
        return ErrorKind.MODEL_NOT_FOUND, None
    if code == "context_length_exceeded":
        return ErrorKind.CONTEXT_TOO_LONG, None
    # a broken tool *schema* is the caller's bug, not "tools unsupported" 【推測：code 名照公開回報】
    if code == "invalid_function_parameters" or msg.startswith("invalid schema for function"):
        return ErrorKind.BAD_REQUEST, None
    return None


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #

_ANTHROPIC_TYPES: dict[str, ErrorKind] = {
    "authentication_error": ErrorKind.AUTH_INVALID,
    "permission_error": ErrorKind.PERMISSION,
    "rate_limit_error": ErrorKind.RATE_LIMITED,
    "overloaded_error": ErrorKind.OVERLOADED,
    "request_too_large": ErrorKind.CONTEXT_TOO_LONG,
    "billing_error": ErrorKind.QUOTA_EXHAUSTED,      # 【推測：型別名照公開文件，未實測】
    "api_error": ErrorKind.SERVER_ERROR,
    "timeout_error": ErrorKind.TIMEOUT,              # 【推測】
}


def _anthropic(*, status: int | None, body: str, headers: Mapping[str, str], exc: BaseException | None,
               tools_sent: bool) -> "tuple[ErrorKind, float | None] | None":
    """Anthropic Messages API: ``{"type": "error", "error": {"type": "...", "message": "..."}}``."""
    if not status or status < 400:
        return None
    if status == 529:
        return ErrorKind.OVERLOADED, None
    f = error_fields(body)
    etype, msg = f.get("type", ""), f.get("message", "")
    if "credit balance" in msg:                      # 400 "Your credit balance is too low ..." 【推測：確切字串待實測】
        return ErrorKind.QUOTA_EXHAUSTED, None
    if etype == "not_found_error":
        return (ErrorKind.MODEL_NOT_FOUND if "model" in msg else ErrorKind.BAD_REQUEST), None
    if etype in _ANTHROPIC_TYPES:
        return _ANTHROPIC_TYPES[etype], None
    if "prompt is too long" in msg:
        return ErrorKind.CONTEXT_TOO_LONG, None
    return None


# --------------------------------------------------------------------------- #
# Gemini (OpenAI-compatible endpoint; errors keep Google's shape)
# --------------------------------------------------------------------------- #

def _gemini(*, status: int | None, body: str, headers: Mapping[str, str], exc: BaseException | None,
            tools_sent: bool) -> "tuple[ErrorKind, float | None] | None":
    if not status or status < 400:
        return None
    low = body.lower()
    f = error_fields(body)
    gstatus, reason, msg = f.get("status", ""), f.get("reason", ""), f.get("message", "")
    if "api_key_invalid" in reason or "api_key_invalid" in low or "api key not valid" in low \
            or "api key expired" in low:
        return ErrorKind.AUTH_INVALID, None
    if status == 401:
        return ErrorKind.AUTH_INVALID, None
    if status == 429 or gstatus == "resource_exhausted":
        # Every Gemini 429 says "You exceeded your current quota"; only a *per-day* quota
        # (or billing) is really "used up" — per-minute limits pass. 【推測：quotaId 的寫法照公開回報】
        if "perday" in low or "per day" in low or "billing" in low:
            return ErrorKind.QUOTA_EXHAUSTED, _google_retry_delay(low)
        return ErrorKind.RATE_LIMITED, _google_retry_delay(low)
    if status == 403 or gstatus == "permission_denied":
        return ErrorKind.PERMISSION, None
    if "location is not supported" in msg:          # FAILED_PRECONDITION 【推測】
        return ErrorKind.PERMISSION, None
    if status == 404 or gstatus == "not_found":
        return (ErrorKind.MODEL_NOT_FOUND if "model" in low else ErrorKind.BAD_REQUEST), None
    if "input token count" in msg and "exceeds" in msg:   # 【推測】
        return ErrorKind.CONTEXT_TOO_LONG, None
    return None


#: provider name → rule function (registered lazily by ``ntu_easy_llm.errors``).
BUILTIN_CLASSIFIERS: dict[str, Callable[..., Any]] = {
    "openai": _openai,
    "anthropic": _anthropic,
    "gemini": _gemini,
    "openai_compatible": _local,
    "lmstudio": _local,
    "ollama": _local,
    "vllm": _local,
    "llamacpp": _local,
}
