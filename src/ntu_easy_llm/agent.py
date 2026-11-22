"""Provider-neutral chat with tool (function) calling — the "agent API" (added in 0.5.0).

The older ``ask*`` functions return a plain ``str`` and know nothing about tools.
Agent hosts need more: a multi-turn message list, tool definitions, the tool
calls the model wants to make, and token usage.  This module gives one call
shape for every provider::

    from ntu_easy_llm.agent import chat, Tool

    reply = chat("openai", "gpt-4.1-mini",
                 [{"role": "user", "content": "Add 80 for coffee"}],
                 tools=[Tool("money_add", "Record an expense",
                             {"type": "object",
                              "properties": {"amount": {"type": "number"}},
                              "required": ["amount"]})],
                 api_key="sk-...")
    reply.text          # "" when the model only called tools
    reply.tool_calls    # [ToolCall(id="call_1", name="money_add", arguments={"amount": 80})]
    reply.usage         # Usage(input_tokens=..., output_tokens=...)

Providers
---------
``openai``              https://api.openai.com/v1           (Chat Completions)
``gemini``              Gemini's OpenAI-compatible endpoint  (Chat Completions)
``anthropic``           https://api.anthropic.com           (Messages API, native tool use)
``openai_compatible``   any ``base_url`` that speaks /chat/completions: company
                        gateways, Ollama (``http://localhost:11434/v1``), LM Studio, vLLM…

Messages are plain dicts (provider-neutral)::

    {"role": "system" | "user" | "assistant" | "tool", "content": "..."}
    {"role": "assistant", "content": "", "tool_calls": [ToolCall(...) or {"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": "call_1", "name": "money_add", "content": "ok"}

Design notes
------------
- Talks HTTP directly (stdlib ``urllib``), not through the provider SDKs, so the
  same code path works for any OpenAI-compatible gateway and importing this
  module never loads ``openai`` / ``anthropic`` / ``google-genai``.
- ``session=`` accepts any object with a requests-style
  ``post(url, json=, headers=, timeout=)`` returning ``.status_code``,
  ``.text`` and ``.json()`` — pass ``requests.Session()`` or a fake in tests.
- Errors are typed (:class:`LLMHTTPError` carries ``status`` and ``body``) so a
  caller can turn them into its own user-facing language.
- Nothing here touches ``.env``: the caller passes ``api_key`` explicitly
  (use :class:`~ntu_easy_llm.KeyMaterial` or :func:`~ntu_easy_llm.load_api_key`
  to obtain one).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .errors import ErrorKind
from .errors import classify as _classify

__all__ = [
    "Tool", "ToolCall", "Usage", "ChatReply",
    "LLMError", "LLMHTTPError", "LLMConnectionError", "LLMResponseError",
    "PROVIDERS", "DEFAULT_BASE_URLS", "normalize_provider",
    "chat", "probe",
    "to_openai_messages", "to_anthropic_messages",
]

PROVIDERS = ("openai", "anthropic", "gemini", "openai_compatible")

DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "anthropic": "https://api.anthropic.com",
}

ANTHROPIC_VERSION = "2023-06-01"

_ALIASES = {
    "chatgpt": "openai",
    "claude": "anthropic",
    "google": "gemini",
    "openai_compat": "openai_compatible",
    "compatible": "openai_compatible",
    "ollama": "openai_compatible",
}


# =============================================================================
# Data types
# =============================================================================

@dataclass
class Tool:
    """A tool the model may call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    """One call the model asked for. ``arguments`` is already a dict."""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class ChatReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: Any = None                 # the decoded JSON body, for anything not normalised here
    stop_reason: str = ""           # finish_reason (OpenAI-style) / stop_reason (Anthropic)
    # 0.6: normalised finish ("" | "stop" | "length" | "thinking" | "tool_calls", see
    # ntu_easy_llm.types.normalize_finish) and non-fatal notes from the adapter.
    finish: str = ""
    warnings: list[str] = field(default_factory=list)


class LLMError(Exception):
    """Base class for every error raised by :func:`chat`.

    0.6 adds three attributes (class defaults, so old code that raises these
    errors keeps working): ``kind`` (an :class:`~ntu_easy_llm.errors.ErrorKind`),
    ``retry_after`` (seconds or ``None``) and ``profile_id`` (which configured
    profile failed; ``""`` when not known).
    """

    kind: ErrorKind = ErrorKind.UNKNOWN
    retry_after: float | None = None
    profile_id: str = ""

    def __init__(self, *args: Any, kind: ErrorKind | str | None = None,
                 retry_after: float | None = None, profile_id: str = ""):
        super().__init__(*args)
        if kind is not None:
            self.kind = ErrorKind(kind)
        if retry_after is not None:
            self.retry_after = float(retry_after)
        if profile_id:
            self.profile_id = str(profile_id)


class LLMHTTPError(LLMError):
    """The provider answered with a non-200 status."""

    def __init__(self, status: int, body: str = "", provider: str = "", *,
                 headers: Mapping[str, str] | None = None, kind: ErrorKind | str | None = None,
                 retry_after: float | None = None, profile_id: str = ""):
        self.status = int(status)
        self.body = body or ""
        self.provider = provider
        self.headers = {str(k): str(v) for k, v in (headers.items() if headers else ())}
        guessed, guessed_wait = _classify(provider, status=self.status, body=self.body, headers=self.headers)
        snippet = " ".join(self.body.split())[:200]
        super().__init__(f"{provider or 'LLM'} HTTP {self.status}" + (f": {snippet}" if snippet else ""),
                         kind=kind if kind is not None else guessed,
                         retry_after=retry_after if retry_after is not None else guessed_wait,
                         profile_id=profile_id)


class LLMConnectionError(LLMError):
    """Could not reach the endpoint (DNS, refused, timeout, TLS…)."""

    kind = ErrorKind.CONNECTION


class LLMResponseError(LLMError):
    """The endpoint answered 200 but the body is not the expected shape."""

    kind = ErrorKind.BAD_RESPONSE


# =============================================================================
# Normalisation helpers
# =============================================================================

def normalize_provider(provider: str) -> str:
    """'CHATGPT' → 'openai', 'ollama' → 'openai_compatible', …; ValueError if unknown."""
    p = str(provider or "").strip().lower()
    p = _ALIASES.get(p, p)
    if p not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Use one of {', '.join(PROVIDERS)}.")
    return p


def _tool(t: Tool | Mapping) -> Tool:
    if isinstance(t, Tool):
        return t
    params = t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}}
    return Tool(name=str(t["name"]), description=str(t.get("description") or ""), parameters=dict(params))


def _call(c: ToolCall | Mapping) -> ToolCall:
    if isinstance(c, ToolCall):
        return c
    args = c.get("arguments", c.get("args", {}))
    if isinstance(args, str):
        args = _parse_args(args)
    return ToolCall(id=str(c.get("id") or ""), name=str(c.get("name") or ""), arguments=dict(args or {}))


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": raw}


def _get(m: Any, key: str, default: Any = None) -> Any:
    """Read a message field from a dict or from an object with attributes."""
    if isinstance(m, Mapping):
        return m.get(key, default)
    return getattr(m, key, default)


def _images(m: Any) -> list:
    """``images`` of a message (0.6 :class:`~ntu_easy_llm.types.ImagePart` objects); ``[]`` when none."""
    return list(_get(m, "images") or [])


def to_openai_messages(messages: Sequence[Any]) -> list[dict]:
    """Neutral messages → Chat Completions ``messages`` (one output per input message).

    A user message with ``images`` (0.6 ``ImagePart``) becomes a list of
    ``text`` + ``image_url`` (data URL) parts; messages without images keep the
    plain-string ``content`` they always had.
    """
    out: list[dict] = []
    for m in messages:
        role = _get(m, "role", "user")
        content = _get(m, "content", "") or ""
        calls = [_call(c) for c in (_get(m, "tool_calls") or [])]
        images = _images(m)
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": _get(m, "tool_call_id", "") or "", "content": content})
        elif role == "assistant" and calls:
            out.append({"role": "assistant", "content": content or None, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in calls]})
        elif images:
            parts: list[dict] = [{"type": "text", "text": content}] if content else []
            parts += [{"type": "image_url", "image_url": {"url": im.to_data_url()}} for im in images]
            out.append({"role": role, "content": parts})
        else:
            out.append({"role": role, "content": content})
    return out


def to_anthropic_messages(messages: Sequence[Any]) -> tuple[str, list[dict]]:
    """Neutral messages → (system, Messages-API ``messages``).

    System messages are joined into the top-level ``system``; consecutive tool
    results are merged into one ``user`` message, as the API requires.
    """
    system = "\n\n".join((_get(m, "content", "") or "") for m in messages if _get(m, "role") == "system")
    out: list[dict] = []
    for m in messages:
        role = _get(m, "role", "user")
        content = _get(m, "content", "") or ""
        calls = [_call(c) for c in (_get(m, "tool_calls") or [])]
        if role == "system":
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": _get(m, "tool_call_id", "") or "", "content": content}
            last = out[-1] if out else None
            if last and last["role"] == "user" and isinstance(last["content"], list) \
                    and all(b.get("type") == "tool_result" for b in last["content"]):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant" and calls:
            blocks: list[dict] = [{"type": "text", "text": content}] if content else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in calls]
            out.append({"role": "assistant", "content": blocks})
        elif role == "user" and _images(m):
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": im.mime,
                                                   "data": im.to_base64()}} for im in _images(m)]
            if content:
                blocks.append({"type": "text", "text": content})
            out.append({"role": "user", "content": blocks})
        else:
            out.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    return system, out


# =============================================================================
# HTTP
# =============================================================================

class _Response:
    def __init__(self, status: int, text: str, headers: Mapping[str, str] | None = None):
        self.status_code = status
        self.text = text
        self.headers = dict(headers or {})

    def json(self) -> Any:
        return json.loads(self.text)


class _UrllibSession:
    """Minimal requests-style session on top of urllib (no extra dependency)."""

    def post(self, url: str, json: Any = None, headers: Mapping[str, str] | None = None,
             timeout: float | None = None) -> _Response:
        import json as _json
        data = _json.dumps(json, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _Response(resp.status, resp.read().decode("utf-8", "replace"), _headers_of(resp))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if e.fp is not None else ""
            return _Response(e.code, body, _headers_of(e))


def _headers_of(obj: Any) -> dict[str, str] | None:
    """Response headers as a plain dict (``None`` when the object has none)."""
    h = getattr(obj, "headers", None)
    items = getattr(h, "items", None)
    return {str(k): str(v) for k, v in items()} if callable(items) else None


def _post(session: Any, url: str, body: dict, headers: dict, timeout: float, provider: str) -> Any:
    try:
        resp = session.post(url, json=body, headers=headers, timeout=timeout)
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001 — urllib / requests / socket errors all mean "unreachable"
        raise LLMConnectionError(f"cannot reach {url}: {type(e).__name__}: {e}",
                                 kind=_classify(provider, exc=e)[0]) from e
    status = int(getattr(resp, "status_code", 0) or 0)
    if status != 200:
        headers = getattr(resp, "headers", None)
        raise LLMHTTPError(status, getattr(resp, "text", "") or "", provider,
                           headers=headers if isinstance(headers, Mapping) else None)
    try:
        return resp.json()
    except ValueError as e:
        raise LLMResponseError(f"{provider} returned a non-JSON body") from e


# =============================================================================
# Provider calls
# =============================================================================

def _openai_tools(tools: Sequence[Tool]) -> list[dict]:
    """:class:`Tool` list → Chat Completions ``tools``."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}} for t in tools]


def _anthropic_tools(tools: Sequence[Tool]) -> list[dict]:
    """:class:`Tool` list → Messages API ``tools``."""
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


def _chat_openai_style(provider: str, model: str, messages, tools, *, api_key: str, base_url: str,
                       max_tokens: int | None, temperature: float | None, timeout: float, session,
                       extra_headers, extra_body) -> ChatReply:
    body: dict = {"model": model, "messages": to_openai_messages(messages)}
    if max_tokens:
        # OpenAI's newer models reject max_tokens; gateways / Ollama / Gemini still expect it.
        body["max_completion_tokens" if provider == "openai" else "max_tokens"] = int(max_tokens)
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = _openai_tools(tools)
    body.update(extra_body or {})
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(extra_headers or {})
    data = _post(session, f"{base_url}/chat/completions", body, headers, timeout, provider)
    return _openai_reply(provider, data)


def _openai_reply(provider: str, data: Any) -> ChatReply:
    """A decoded Chat Completions body → :class:`ChatReply` (shared with the 0.6 adapters)."""
    try:
        choice = data["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMResponseError(f"{provider} reply is not in Chat Completions format") from e
    calls = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function") or {}
        calls.append(ToolCall(id=c.get("id") or f"call_{i}", name=fn.get("name", ""),
                              arguments=_parse_args(fn.get("arguments"))))
    usage = data.get("usage") or {}
    content = msg.get("content")
    if isinstance(content, list):          # some gateways return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ChatReply(text=content or "", tool_calls=calls,
                     usage=Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)),
                     raw=data, stop_reason=str(choice.get("finish_reason") or ""))


def _chat_anthropic(model: str, messages, tools, *, api_key: str, base_url: str, max_tokens: int | None,
                    temperature: float | None, timeout: float, session, extra_headers, extra_body) -> ChatReply:
    system, wire = to_anthropic_messages(messages)
    body: dict = {"model": model, "max_tokens": int(max_tokens or 1024), "messages": wire}
    if system:
        body["system"] = system
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = _anthropic_tools(tools)
    body.update(extra_body or {})
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
    headers.update(extra_headers or {})
    data = _post(session, f"{base_url}/v1/messages", body, headers, timeout, "anthropic")
    return _anthropic_reply(data)


def _anthropic_reply(data: Any) -> ChatReply:
    """A decoded Messages API body → :class:`ChatReply` (shared with the 0.6 adapters)."""
    try:
        blocks = data["content"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [ToolCall(id=b.get("id", ""), name=b.get("name", ""), arguments=_parse_args(b.get("input")))
                 for b in blocks if b.get("type") == "tool_use"]
    except (KeyError, TypeError, AttributeError) as e:
        raise LLMResponseError("anthropic reply is not in Messages API format") from e
    usage = data.get("usage") or {}
    return ChatReply(text=text, tool_calls=calls,
                     usage=Usage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)),
                     raw=data, stop_reason=str(data.get("stop_reason") or ""))


# =============================================================================
# Public API
# =============================================================================

def chat(
    provider: str,
    model: str,
    messages: Sequence[Any],
    tools: Sequence[Tool | Mapping] | None = None,
    *,
    api_key: str = "",
    base_url: str | None = None,
    max_tokens: int | None = 1024,
    temperature: float | None = None,
    timeout: float = 90.0,
    session: Any = None,
    extra_headers: Mapping[str, str] | None = None,
    extra_body: Mapping[str, Any] | None = None,
) -> ChatReply:
    """Send a multi-turn conversation (optionally with tools) and return a :class:`ChatReply`.

    Parameters
    ----------
    provider:
        ``"openai"``, ``"anthropic"``, ``"gemini"`` or ``"openai_compatible"``
        (aliases: ``chatgpt``, ``claude``, ``ollama``, ``openai_compat``; case-insensitive).
    model:
        Model id as the provider spells it.
    messages:
        Neutral message dicts (see module docstring). Objects with the same
        attribute names are accepted too.
    tools:
        :class:`Tool` objects or ``{"name", "description", "parameters"}`` dicts.
    api_key:
        Sent as ``Authorization: Bearer`` (OpenAI-style) or ``x-api-key`` (Anthropic).
        May be empty for local endpoints such as Ollama.
    base_url:
        Required for ``openai_compatible``; optional override for the others.
    max_tokens:
        Cap on the reply length (``max_completion_tokens`` for ``openai``).
    session:
        requests-style object with ``post``; defaults to a stdlib urllib session.
    extra_headers / extra_body:
        Merged into the request as-is (gateway-specific fields).

    Raises
    ------
    ValueError        unknown provider, missing model, or ``openai_compatible`` without ``base_url``
    LLMHTTPError      non-200 answer (``.status``, ``.body``)
    LLMConnectionError endpoint unreachable
    LLMResponseError  200 but not the expected JSON shape
    """
    p = normalize_provider(provider)
    if not model:
        raise ValueError("model is required")
    url = (base_url or DEFAULT_BASE_URLS.get(p) or "").strip().rstrip("/")
    if not url:
        raise ValueError("openai_compatible needs base_url (e.g. http://localhost:11434/v1)")
    tool_list = [_tool(t) for t in (tools or [])]
    kw = dict(api_key=api_key or "", base_url=url, max_tokens=max_tokens, temperature=temperature,
              timeout=timeout, session=session or _UrllibSession(), extra_headers=extra_headers,
              extra_body=extra_body)
    if p == "anthropic":
        return _chat_anthropic(model, messages, tool_list, **kw)
    return _chat_openai_style(p, model, messages, tool_list, **kw)


def probe(
    provider: str,
    model: str,
    *,
    api_key: str = "",
    base_url: str | None = None,
    timeout: float = 30.0,
    session: Any = None,
    max_tokens: int = 256,
) -> str | None:
    """Send one tiny request to check the settings work.

    Returns ``None`` when the endpoint answered, otherwise a one-line reason
    (the exception's message). Never raises for provider/network problems.
    ``max_tokens`` is generous on purpose: reasoning models spend tokens before
    they write anything, and a tiny cap makes them answer with an empty string.
    """
    try:
        reply = chat(provider, model, [{"role": "user", "content": "Reply with one word: ok"}],
                     api_key=api_key, base_url=base_url, max_tokens=max_tokens, timeout=timeout,
                     session=session)
    except (LLMError, ValueError) as e:
        return str(e)
    if not (reply.text or reply.tool_calls):
        return "connected, but the model returned an empty reply"
    return None
