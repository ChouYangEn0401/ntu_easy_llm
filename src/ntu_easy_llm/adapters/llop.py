"""LLOP adapter (``remote/api_key/llop``): a model shared through an LLOP server, via the LLOP SDK.

Needs the optional extra ``ntu_easy_llm[llop]`` (the ``llmop.sdk`` package).
Importing this module does **not** import ``llmop``; the SDK is loaded the
first time an adapter is built, so a machine without the extra is unaffected.

Profile::

    {"source": "remote/api_key/llop", "llop_profile": "default", "model": "any",
     "options": {"transport": "auto", "max_tokens": 1024, "timeout": 600}}

- ``llop_profile`` is the name of a profile enrolled with the LLOP SDK on this
  machine (``llmop client enroll …`` / ``add-plain``). The API key, signing key
  and pinned server keys all live in the SDK's own profile files — never in
  the ntu_easy_llm config.
- ``model``: ``""`` / ``"any"`` lets the server pick (LLOP serves one model per
  worker).
- ``transport``: ``auto`` (the SDK profile decides), ``e2e`` or ``plain``.
- ``timeout`` is passed as the SDK's overall ``deadline``.

Errors: ``SdkError.kind_hint`` → :class:`~ntu_easy_llm.errors.ErrorKind`.
``link_changed`` is raised as ``LINK_CHANGED`` straight away (no retry, no
link discovery): the user gets a new link from the provider and runs
``llmop client set-link``. ``trust`` stays ``TRUST`` — never a fallback trigger.
"""
from __future__ import annotations

import importlib
import importlib.util
import threading
import time
from typing import Any, Callable

from ..agent import (ChatReply, LLMConnectionError, LLMError, LLMHTTPError, LLMResponseError, ToolCall, Usage,
                     _parse_args, _tool, to_openai_messages)
from ..errors import ConfigError, ErrorKind, classify
from ..profiles import Profile
from ..types import as_messages, normalize_finish
from .base import Capabilities, ModelInfo, ProbeResult

__all__ = ["LlopAdapter", "from_profile", "kind_from_hint", "INSTALL_HINT"]

INSTALL_HINT = "要先安裝 ntu_easy_llm[llop] / install ntu_easy_llm[llop] first"

#: SdkError.kind_hint → ErrorKind
_HINT_KIND: dict[str, ErrorKind] = {
    "quota": ErrorKind.QUOTA_EXHAUSTED,
    "auth": ErrorKind.AUTH_INVALID,
    "rate": ErrorKind.RATE_LIMITED,
    "link_changed": ErrorKind.LINK_CHANGED,
    "offline": ErrorKind.CONNECTION,
    "timeout": ErrorKind.TIMEOUT,
    "trust": ErrorKind.TRUST,
    "bad_request": ErrorKind.BAD_REQUEST,
    "server": ErrorKind.SERVER_ERROR,
    "unknown": ErrorKind.UNKNOWN,
}
_OVERLOADED_CODES = frozenset({"queue_full", "gateway_busy"})


def kind_from_hint(hint: str, *, code: str = "", status: int | None = None, message: str = "",
                   tools_sent: bool = False) -> ErrorKind:
    """Map an ``SdkError`` to an :class:`ErrorKind`.

    The coarse hints (``bad_request`` / ``server`` / ``unknown``) are refined
    with the generic HTTP rules (context too long, model not found, tools
    rejected, overloaded…); the specific ones are taken as they are.
    """
    kind = _HINT_KIND.get(str(hint or "unknown"), ErrorKind.UNKNOWN)
    if str(code) in _OVERLOADED_CODES:
        return ErrorKind.OVERLOADED
    if kind in (ErrorKind.BAD_REQUEST, ErrorKind.SERVER_ERROR, ErrorKind.UNKNOWN) and status:
        refined, _ = classify("", status=status, body=f"{code} {message}", tools_sent=tools_sent)
        if refined not in (ErrorKind.UNKNOWN, ErrorKind.BAD_RESPONSE, ErrorKind.QUOTA_EXHAUSTED,
                           ErrorKind.AUTH_INVALID, ErrorKind.RATE_LIMITED):
            return refined
    return kind


def _load_sdk() -> Any:
    if importlib.util.find_spec("llmop") is None:
        raise ConfigError(INSTALL_HINT)
    try:
        return importlib.import_module("llmop.sdk")
    except ImportError as e:
        raise ConfigError(f"{INSTALL_HINT} ({type(e).__name__}: {e})") from None


def _check() -> tuple[bool, str]:
    if importlib.util.find_spec("llmop") is None:       # top-level find_spec does not import llmop
        return False, INSTALL_HINT
    return True, ""


def _openai_messages(msgs) -> list[dict]:
    """Chat Completions messages; user images become ``image_url`` parts (data URLs)."""
    out = to_openai_messages(msgs)
    for wire, m in zip(out, msgs):
        if m.images:
            parts: list[dict] = [{"type": "text", "text": m.content}] if m.content else []
            parts += [{"type": "image_url", "image_url": {"url": im.to_data_url()}} for im in m.images]
            wire["content"] = parts
    return out


def _reply(data: Any) -> ChatReply:
    try:
        choice = data["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMResponseError("llop reply is not in Chat Completions format") from e
    calls = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function") or {}
        calls.append(ToolCall(id=c.get("id") or f"call_{i}", name=fn.get("name", ""),
                              arguments=_parse_args(fn.get("arguments"))))
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    usage = data.get("usage") or {}
    raw_finish = str(choice.get("finish_reason") or "")
    return ChatReply(text=content or "", tool_calls=calls,
                     usage=Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)),
                     raw=data, stop_reason=raw_finish,
                     finish=normalize_finish(raw_finish, text=content or "", has_tool_calls=bool(calls)))


class LlopAdapter:
    """Adapter for one LLOP SDK profile. ``client`` (an ``llmop.sdk.Client``) can be injected for tests."""

    def __init__(self, profile: Profile, *, client: Any = None, sdk: Any = None, http: Any = None):
        self.profile = profile
        self._client = client
        self._sdk = sdk
        self._http = http
        self._lock = threading.Lock()

    # -- helpers ---------------------------------------------------------------
    @property
    def llop_profile(self) -> str:
        return str(self.profile.option("llop_profile") or "default")

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                sdk = self._sdk or _load_sdk()
                self._sdk = sdk
                kw = {"http": self._http} if self._http is not None else {}
                try:
                    self._client = sdk.Client.from_profile(self.llop_profile, **kw)
                except Exception as e:  # noqa: BLE001 — SdkError or a bad profile file
                    raise self._error(e) from None
            return self._client

    def _error(self, e: BaseException, *, tools_sent: bool = False) -> LLMError:
        hint = getattr(e, "kind_hint", None)
        if hint is None:                                   # not an SdkError: network / programming error
            kind, _ = classify("llop", exc=e)
            return LLMConnectionError(f"llop: {type(e).__name__}: {e}", kind=kind, profile_id=self.profile.id)
        code = str(getattr(e, "code", "") or "")
        status = getattr(e, "status", None)
        message = str(getattr(e, "message", "") or "")
        kind = kind_from_hint(hint, code=code, status=status, message=message, tools_sent=tools_sent)
        retry_after = getattr(e, "retry_after", None)
        text = f"{code}: {message}" if message else code
        if kind is ErrorKind.LINK_CHANGED:
            text = f"{text} — llmop client set-link --profile {self.llop_profile} <new link>"
        if kind in (ErrorKind.CONNECTION, ErrorKind.TIMEOUT):
            return LLMConnectionError(f"llop: {text}", kind=kind, retry_after=retry_after,
                                      profile_id=self.profile.id)
        if status:
            return LLMHTTPError(int(status), text, "llop", kind=kind, retry_after=retry_after,
                                profile_id=self.profile.id)
        return LLMError(f"llop: {text}", kind=kind, retry_after=retry_after, profile_id=self.profile.id)

    def _model(self) -> str | None:
        m = (self.profile.model or "").strip()
        return None if m in ("", "any") else m

    # -- Adapter ---------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        # tools / vision depend on the model the server runs; LLOP has no streaming
        return Capabilities(tools=None, vision=None, stop=True, stream=False, usage=True)

    def chat(self, messages, tools=None, *, max_tokens=None, temperature=None, stop=(), timeout=None,
             on_delta: Callable[[str], None] | None = None) -> ChatReply:
        msgs = as_messages(messages)
        tool_list = [_tool(t) for t in (tools or [])]
        body: dict[str, Any] = {"messages": _openai_messages(msgs)}
        temp = temperature if temperature is not None else self.profile.option("temperature")
        if temp is not None:
            body["temperature"] = temp
        if stop:
            body["stop"] = list(stop)
        if tool_list:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.parameters}} for t in tool_list]
        kw: dict[str, Any] = {"model": self._model()}
        mt = max_tokens or self.profile.option("max_tokens")
        if mt:
            kw["max_tokens"] = int(mt)
        transport = str(self.profile.option("transport") or "auto")
        if transport != "auto":
            kw["transport"] = transport
        deadline = timeout or self.profile.option("timeout")
        if deadline:
            kw["deadline"] = float(deadline)
        client = self._get_client()
        try:
            data = client.chat(body, **kw)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 — SdkError and anything the SDK lets through
            raise self._error(e, tools_sent=bool(tool_list)) from None
        reply = _reply(data)
        if on_delta and reply.text:
            on_delta(reply.text)
        return reply

    def list_models(self, *, timeout: float = 15.0) -> list[ModelInfo]:
        client = self._get_client()
        try:
            entries = client.models()
        except Exception as e:  # noqa: BLE001
            raise self._error(e) from None
        return [ModelInfo(id=str(m.id), label=str(getattr(m, "owned_by", "") or ""),
                          context=getattr(m, "context_length", None)) for m in entries]

    def probe(self, *, send: bool = False, timeout: float = 30.0) -> ProbeResult:
        start = time.monotonic()
        try:
            models = tuple(self.list_models(timeout=timeout))
            if send:
                reply = self.chat([{"role": "user", "content": "Reply with one word: ok"}], max_tokens=16,
                                  timeout=timeout)
                if not (reply.text or reply.tool_calls):
                    return ProbeResult(False, "connected, but the model returned an empty reply",
                                       ErrorKind.BAD_RESPONSE, models, time.monotonic() - start)
        except LLMError as e:
            return ProbeResult(False, str(e), e.kind, (), time.monotonic() - start)
        except ConfigError as e:
            return ProbeResult(False, str(e), None, (), time.monotonic() - start)
        return ProbeResult(True, "", None, models, time.monotonic() - start)

    def close(self) -> None:
        with self._lock:
            if self._client is not None and hasattr(self._client, "close"):
                self._client.close()
            self._client = None


def from_profile(profile: Profile, *, http: Any = None, resolve_secret: Any = None) -> LlopAdapter:
    """Factory for ``remote/api_key/llop``. ``resolve_secret`` is unused: the key lives in the SDK profile."""
    return LlopAdapter(profile, http=http)


from_profile.check = _check  # type: ignore[attr-defined]
