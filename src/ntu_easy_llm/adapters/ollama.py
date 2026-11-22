"""HTTP adapter for ``local/ollama``: Ollama's native ``/api/chat``, or its ``/v1`` endpoint.

Native is the default because it takes the settings ``/v1`` cannot:
``options.context_size`` ??``num_ctx`` (Ollama otherwise uses its small
default context), ``max_tokens`` ??``num_predict``, ``stop``, ``temperature``,
and the optional ``options.think`` (``true`` / ``false``; not sent unless
set, because older Ollama versions and non-thinking models may reject it).

It talks ``/v1`` (OpenAI style, :mod:`.openai_style`) instead when
``base_url`` ends in ``/v1``, when ``options.api`` is ``"openai"``, or ??automatically, once ??when ``/api/chat`` answers 404 without mentioning a
model (a proxy in front of Ollama that only exposes ``/v1``).

Messages are converted with :func:`ntu_easy_llm.agent.to_openai_messages` and
then reshaped for the native API (images as base64 strings in ``images``,
tool-call arguments as objects, tool results carry ``tool_name``).
``list_models`` reads ``/api/tags`` and ``/api/ps`` (which models are loaded);
neither loads a model.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from ..agent import ChatReply, LLMError, LLMHTTPError, ToolCall, Usage, _openai_tools, _parse_args, \
    to_openai_messages
from ..catalog import _LOCAL_URLS
from ..errors import ConfigError
from ..types import Message
from ._http import _lines, model_kind
from .base import ModelInfo
from .openai_style import _LocalOpenAIStyle

__all__ = ["OllamaAdapter", "to_ollama_messages"]


def to_ollama_messages(msgs: Sequence[Message]) -> list[dict]:
    """Neutral messages ??native ``/api/chat`` messages (built on ``to_openai_messages``)."""
    out: list[dict] = []
    for m, w in zip(msgs, to_openai_messages(msgs)):
        w = dict(w)
        if m.images:
            w["content"] = m.content
            w["images"] = [im.to_base64() for im in m.images]
        if w.get("tool_calls"):
            w["content"] = m.content
            w["tool_calls"] = [{"function": {"name": c.name, "arguments": dict(c.arguments)}} for c in m.tool_calls]
        if m.role == "tool":
            w.pop("tool_call_id", None)
            if m.name:
                w["tool_name"] = m.name
        out.append(w)
    return out


class OllamaAdapter(_LocalOpenAIStyle):
    """``local/ollama``."""

    provider = "ollama"
    default_base_url = _LOCAL_URLS["ollama"]

    def __init__(self, profile, *, http=None, resolve_secret=None):
        super().__init__(profile, http=http, resolve_secret=resolve_secret)
        api = str(profile.option("api", "native") or "native").lower()
        if api not in ("native", "openai"):
            raise ConfigError(f"profile {profile.id}: options.api must be native or openai")
        if self.base_url.endswith("/v1"):
            self.root, self.native = self.base_url[:-3], False
        else:
            self.root, self.native = self.base_url, api == "native"

    # /v1 path reuses the OpenAI-style adapter on root + /v1
    def _chat_url(self) -> str:
        return f"{self.root}/v1/chat/completions"

    def _models_url(self) -> str:
        return f"{self.root}/v1/models"

    # -- chat ------------------------------------------------------------------------
    def _chat(self, msgs, tools, *, max_tokens, temperature, stop, timeout, on_delta) -> ChatReply:
        if not self.native:
            return super()._chat(msgs, tools, max_tokens=max_tokens, temperature=temperature, stop=stop,
                                 timeout=timeout, on_delta=on_delta)
        try:
            return self._native_chat(msgs, tools, max_tokens=max_tokens, temperature=temperature, stop=stop,
                                     timeout=timeout, on_delta=on_delta)
        except LLMHTTPError as e:
            if e.status == 404 and "model" not in e.body.lower():
                self.native = False                   # only /v1 is reachable here
                return super()._chat(msgs, tools, max_tokens=max_tokens, temperature=temperature, stop=stop,
                                     timeout=timeout, on_delta=on_delta)
            raise

    def _native_chat(self, msgs, tools, *, max_tokens, temperature, stop, timeout, on_delta) -> ChatReply:
        stream = on_delta is not None
        body: dict[str, Any] = {"model": self._model(), "messages": to_ollama_messages(msgs), "stream": stream}
        opts: dict[str, Any] = {}
        ctx = self.profile.option("context_size")
        if ctx:
            opts["num_ctx"] = int(ctx)
        if max_tokens:
            opts["num_predict"] = int(max_tokens)
        if temperature is not None:
            opts["temperature"] = temperature
        if stop:
            opts["stop"] = list(stop)
        if opts:
            body["options"] = opts
        if tools:
            body["tools"] = _openai_tools(tools)
        think = self.profile.option("think")
        if think is not None:
            body["think"] = bool(think)
        body.update(self._extra_body())
        resp = self._send("POST", f"{self.root}/api/chat", body=body, headers=self._headers(), timeout=timeout,
                          stream=stream, tools_sent=bool(tools))
        if not stream:
            return self._native_reply(self._json(resp))
        return self._native_stream(resp, on_delta, tools_sent=bool(tools))

    def _native_reply(self, data: Any) -> ChatReply:
        try:
            msg = data["message"]
            content = str(msg.get("content") or "")
            calls = [self._call(i, c) for i, c in enumerate(msg.get("tool_calls") or [])]
        except (KeyError, TypeError, AttributeError) as e:
            raise self._bad_response("reply is not in /api/chat format", e) from e
        return ChatReply(text=content, tool_calls=calls,
                         usage=Usage(int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)),
                         raw=data, stop_reason=str(data.get("done_reason") or ("stop" if data.get("done") else "")))

    @staticmethod
    def _call(i: int, c: Any) -> ToolCall:
        fn = (c or {}).get("function") or {}
        return ToolCall(id=str(c.get("id") or f"call_{i}"), name=str(fn.get("name") or ""),
                        arguments=_parse_args(fn.get("arguments")))

    def _native_stream(self, resp: Any, on_delta: Callable[[str], None], *, tools_sent: bool) -> ChatReply:
        text: list[str] = []
        calls: list[ToolCall] = []
        last: dict = {}
        for line in _lines(resp):
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except ValueError as e:
                raise self._bad_response("stream line is not JSON", e) from e
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                self._raise_http(500, json.dumps(chunk, ensure_ascii=False), None, tools_sent=tools_sent)
            msg = chunk.get("message") or {}
            piece = msg.get("content")
            if isinstance(piece, str) and piece:
                text.append(piece)
                on_delta(piece)
            for c in msg.get("tool_calls") or []:
                calls.append(self._call(len(calls), c))
            last = chunk
            if chunk.get("done"):
                break
        if not last:
            raise self._bad_response("empty streamed reply")
        return ChatReply(text="".join(text), tool_calls=calls,
                         usage=Usage(int(last.get("prompt_eval_count") or 0), int(last.get("eval_count") or 0)),
                         raw={"stream": True, "last": last},
                         stop_reason=str(last.get("done_reason") or ("stop" if last.get("done") else "")))

    # -- models ----------------------------------------------------------------------
    def _list(self, *, timeout: float) -> list[ModelInfo]:
        try:
            tags = self._get_json(f"{self.root}/api/tags", headers=self._headers(), timeout=timeout)
        except LLMHTTPError as e:
            if e.status == 404:
                return super()._list(timeout=timeout)      # only /v1 is exposed
            raise
        loaded: set[str] | None
        try:
            ps = self._get_json(f"{self.root}/api/ps", headers=self._headers(), timeout=timeout)
            loaded = {str(m.get("name") or m.get("model")) for m in (ps.get("models") or []) if isinstance(m, dict)}
        except (LLMError, AttributeError):
            loaded = None
        out = []
        for m in (tags.get("models") or []) if isinstance(tags, dict) else []:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or m.get("model") or "")
            if not name:
                continue
            out.append(ModelInfo(id=name, loaded=None if loaded is None else name in loaded, kind=model_kind(name)))
        return out

    def _model_listed(self, models) -> bool:
        want = self.profile.model
        if not want:
            return True
        names = {m.id for m in models}
        return want in names or (":" not in want and f"{want}:latest" in names)
