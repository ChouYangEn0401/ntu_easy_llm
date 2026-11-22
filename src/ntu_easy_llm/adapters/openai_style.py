"""HTTP adapters for everything that speaks OpenAI Chat Completions (``/chat/completions``).

==============================  =============================  ==========================
source                          class                          default base_url
==============================  =============================  ==========================
``remote/api_key/openai``       :class:`OpenAIAdapter`         the OpenAI API
``remote/api_key/gemini``       :class:`GeminiAdapter`         Gemini's OpenAI-compatible endpoint
``remote/api_key/openai_compatible`` :class:`OpenAICompatibleAdapter`  (required)
``local/lmstudio``              :class:`LMStudioAdapter`       ``http://127.0.0.1:1234/v1``
``local/vllm``                  :class:`VLLMAdapter`           ``http://127.0.0.1:8000/v1``
``local/llamacpp``              :class:`LlamaCppAdapter`       ``http://127.0.0.1:8080/v1``
``local/openai_compatible``     :class:`LocalOpenAICompatibleAdapter` (required)
==============================  =============================  ==========================

Messages go through :func:`ntu_easy_llm.agent.to_openai_messages` (the same
converter the 0.5 ``agent.chat`` uses; images become ``image_url`` data-URL
parts). ``on_delta`` switches to ``stream: true`` and reads server-sent events;
a server that ignores ``stream`` and answers with one JSON body still works
(``on_delta`` then gets the whole text once).

Options read from the profile: ``max_tokens``, ``temperature``, ``timeout``,
``tools`` / ``vision`` (local: ``false`` turns the feature off, ``true``
promises it), ``extra_body`` (merged into every request body as is),
``proxy`` (``auto`` / ``none`` / ``system``). ``context_size`` is a load-time
setting on these servers and is not sent.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from ..agent import (DEFAULT_BASE_URLS, ChatReply, LLMHTTPError, LLMResponseError, Tool, ToolCall, Usage,
                     _openai_reply, _openai_tools, _parse_args, to_openai_messages)
from ..catalog import _LOCAL_URLS
from ..types import Message
from ._http import HttpAdapter, _lines, iter_sse, model_kind
from .base import Capabilities, ModelInfo

__all__ = ["OpenAIStyleAdapter", "OpenAIAdapter", "GeminiAdapter", "OpenAICompatibleAdapter",
           "LMStudioAdapter", "VLLMAdapter", "LlamaCppAdapter", "LocalOpenAICompatibleAdapter"]


class OpenAIStyleAdapter(HttpAdapter):
    """Shared Chat Completions logic. Subclasses only change class attributes and model listing."""

    provider = "openai_compatible"
    #: request field for the reply cap (OpenAI's newer models reject ``max_tokens``)
    max_tokens_field = "max_tokens"
    #: ask for a usage chunk at the end of a stream (``stream_options.include_usage``)
    stream_usage = False
    base_capabilities = Capabilities(tools=None, vision=None, stop=True, stream=True, usage=True)

    # -- request ---------------------------------------------------------------------
    def _headers(self, *, stream: bool = False) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if stream:
            h["Accept"] = "text/event-stream"
        key = self._key()
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _body(self, msgs: list[Message], tools: list[Tool], *, max_tokens, temperature, stop, stream) -> dict:
        body: dict[str, Any] = {"model": self._model(), "messages": to_openai_messages(msgs)}
        if max_tokens:
            body[self.max_tokens_field] = int(max_tokens)
        if temperature is not None:
            body["temperature"] = temperature
        if stop:
            body["stop"] = list(stop)
        if tools:
            body["tools"] = _openai_tools(tools)
        if stream:
            body["stream"] = True
            if self.stream_usage:
                body["stream_options"] = {"include_usage": True}
        body.update(self._extra_body())
        return body

    def _chat(self, msgs, tools, *, max_tokens, temperature, stop, timeout, on_delta) -> ChatReply:
        stream = on_delta is not None
        body = self._body(msgs, tools, max_tokens=max_tokens, temperature=temperature, stop=stop, stream=stream)
        resp = self._send("POST", self._chat_url(), body=body, headers=self._headers(stream=stream),
                          timeout=timeout, stream=stream, tools_sent=bool(tools))
        if stream:
            return self._read_stream(resp, on_delta, tools_sent=bool(tools))
        return self._reply(self._json(resp))

    def _reply(self, data: Any) -> ChatReply:
        try:
            return _openai_reply(self.provider, data)
        except LLMResponseError as e:
            e.profile_id = self.profile.id
            raise

    # -- streaming -------------------------------------------------------------------
    def _read_stream(self, resp: Any, on_delta: Callable[[str], None], *, tools_sent: bool) -> ChatReply:
        raw_lines: list[str] = []

        def tee() -> Iterator[str]:
            for line in _lines(resp):
                if len(raw_lines) < 2000:
                    raw_lines.append(line)
                yield line

        text: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish = ""
        usage: dict = {}
        last: Any = None
        chunks = 0
        for _event, data in iter_sse(tee()):
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError as e:
                raise self._bad_response("stream chunk is not JSON", e) from e
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error") and not chunk.get("choices"):
                self._stream_error(chunk, tools_sent=tools_sent)
            chunks += 1
            last = chunk
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    text.append(piece)
                    on_delta(piece)
                for i, tc in enumerate(delta.get("tool_calls") or []):
                    slot = calls.setdefault(int(tc.get("index", i)), {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = str(tc["id"])
                    fn = tc.get("function") or {}
                    name = fn.get("name")
                    if name and name != slot["name"]:
                        slot["name"] += str(name)
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        slot["arguments"] = json.dumps(args, ensure_ascii=False)
                    elif args:
                        slot["arguments"] += str(args)
                if choice.get("finish_reason"):
                    finish = str(choice["finish_reason"])
        if chunks == 0:
            # the server ignored stream=true and sent one JSON body
            whole = "\n".join(raw_lines).strip()
            if whole.startswith("{"):
                try:
                    data = json.loads(whole)
                except ValueError as e:
                    raise self._bad_response("reply is not JSON", e) from e
                reply = self._reply(data)
                if reply.text:
                    on_delta(reply.text)
                return reply
            if not whole:
                raise self._bad_response("empty streamed reply")
        tool_calls = [ToolCall(id=c["id"] or f"call_{i}", name=c["name"], arguments=_parse_args(c["arguments"]))
                      for i, c in sorted(calls.items())]
        return ChatReply(text="".join(text), tool_calls=tool_calls,
                         usage=Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)),
                         raw={"stream": True, "chunks": chunks, "last": last}, stop_reason=finish)

    def _stream_error(self, chunk: dict, *, tools_sent: bool) -> None:
        """An ``{"error": ...}`` event in the middle of a stream (status unknown → code if numeric, else 400)."""
        err = chunk.get("error")
        code = err.get("code") if isinstance(err, dict) else None
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 400
        if not 400 <= status < 600:
            status = 400
        self._raise_http(status, json.dumps(chunk, ensure_ascii=False), None, tools_sent=tools_sent)

    # -- models ----------------------------------------------------------------------
    def _models_url(self) -> str:
        return f"{self.base_url}/models"

    def _list(self, *, timeout: float) -> list[ModelInfo]:
        data = self._get_json(self._models_url(), headers=self._headers(), timeout=timeout)
        return [self._model_info(item) for item in _items(data) if _item_id(item)]

    def _model_info(self, item: dict) -> ModelInfo:
        mid = _item_id(item)
        return ModelInfo(id=mid, label=str(item.get("display_name") or ""), kind=model_kind(mid))


def _items(data: Any) -> list[dict]:
    """``data`` (OpenAI) or ``models`` (LM Studio, Ollama) list of a models response."""
    if isinstance(data, dict):
        data = data.get("data", data.get("models"))
    return [d for d in (data or []) if isinstance(d, dict)] if isinstance(data, list) else []


def _item_id(item: dict) -> str:
    return str(item.get("id") or item.get("key") or item.get("model_key") or item.get("name") or "")


# =============================================================================
# Remote
# =============================================================================

class OpenAIAdapter(OpenAIStyleAdapter):
    """``remote/api_key/openai`` — the OpenAI API (Chat Completions)."""

    provider = "openai"
    default_base_url = DEFAULT_BASE_URLS["openai"]
    needs_key = True
    max_tokens_field = "max_completion_tokens"
    stream_usage = True
    base_capabilities = Capabilities(tools=True, vision=None, stop=True, stream=True, usage=True)


class GeminiAdapter(OpenAIStyleAdapter):
    """``remote/api_key/gemini`` — Google's OpenAI-compatible endpoint for Gemini."""

    provider = "gemini"
    default_base_url = DEFAULT_BASE_URLS["gemini"]
    needs_key = True
    base_capabilities = Capabilities(tools=True, vision=None, stop=True, stream=True, usage=True)

    def _model_info(self, item: dict) -> ModelInfo:
        mid = _item_id(item)
        if mid.startswith("models/"):         # the compatible endpoint lists "models/<id>" 【推測】
            mid = mid[len("models/"):]
        return ModelInfo(id=mid, label=str(item.get("display_name") or ""), kind=model_kind(mid))

    def _model_listed(self, models) -> bool:
        want = self.profile.model.removeprefix("models/")
        return not want or any(m.id == want for m in models)


class OpenAICompatibleAdapter(OpenAIStyleAdapter):
    """``remote/api_key/openai_compatible`` — any gateway that speaks ``/chat/completions``.

    The key is optional (some gateways need none); ``probe(send=False)`` does not
    fail when the model is missing from ``/models`` (gateways often accept aliases).
    """

    provider = "openai_compatible"
    strict_model_check = False
    base_capabilities = Capabilities(tools=None, vision=None, stop=True, stream=True, usage=False)


# =============================================================================
# Local
# =============================================================================

class _LocalOpenAIStyle(OpenAIStyleAdapter):
    default_timeout = 300.0                    # local models can take a while before the first byte

    def _model_info(self, item: dict) -> ModelInfo:
        mid = _item_id(item)
        ctx = item.get("max_model_len") or item.get("max_context_length") or item.get("context_length")
        return ModelInfo(id=mid, context=int(ctx) if isinstance(ctx, int) else None, loaded=True,
                         kind=model_kind(mid))


class VLLMAdapter(_LocalOpenAIStyle):
    """``local/vllm`` — vLLM's OpenAI-compatible server. Every listed model is loaded (``max_model_len`` → context)."""

    provider = "vllm"
    default_base_url = _LOCAL_URLS["vllm"]
    stream_usage = True


class LlamaCppAdapter(_LocalOpenAIStyle):
    """``local/llamacpp`` — llama.cpp ``llama-server`` (tools need ``--jinja`` on the server)."""

    provider = "llamacpp"
    default_base_url = _LOCAL_URLS["llamacpp"]


class LocalOpenAICompatibleAdapter(_LocalOpenAIStyle):
    """``local/openai_compatible`` — any OpenAI-compatible server on this machine or the LAN."""

    provider = "openai_compatible"
    strict_model_check = False
    base_capabilities = Capabilities(tools=None, vision=None, stop=True, stream=True, usage=False)

    def _model_info(self, item: dict) -> ModelInfo:
        mid = _item_id(item)
        return ModelInfo(id=mid, kind=model_kind(mid))


class LMStudioAdapter(_LocalOpenAIStyle):
    """``local/lmstudio`` — LM Studio. Chat on ``/v1``; models from LM Studio's own REST API
    (``/api/v1/models``, then ``/api/v0/models``), which says which ones are loaded. Falls back to
    ``/v1/models`` (no loaded flag) on versions without it. 【推測：欄位名照公開文件，未實測】
    """

    provider = "lmstudio"
    default_base_url = _LOCAL_URLS["lmstudio"]

    def _root(self) -> str:
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    def _list(self, *, timeout: float) -> list[ModelInfo]:
        for path in ("/api/v1/models", "/api/v0/models"):
            try:
                data = self._get_json(self._root() + path, headers=self._headers(), timeout=timeout)
            except LLMHTTPError as e:
                if e.status in (404, 405):
                    continue
                raise
            items = _items(data)
            if items:
                return [self._lms_info(i) for i in items if _item_id(i)]
        return super()._list(timeout=timeout)

    def _lms_info(self, item: dict) -> ModelInfo:
        mid = _item_id(item)
        mtype = str(item.get("type") or "").lower()
        caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
        if "loaded_instances" in item:
            loaded: bool | None = bool(item.get("loaded_instances"))
        elif "state" in item:
            loaded = str(item.get("state")).lower() == "loaded"
        else:
            loaded = None
        vision = caps.get("vision") if isinstance(caps.get("vision"), bool) else (True if mtype == "vlm" else None)
        tools = caps.get("trained_for_tool_use") if isinstance(caps.get("trained_for_tool_use"), bool) else None
        ctx = item.get("max_context_length")
        kind = "embedding" if mtype.startswith("embedding") else ("chat" if mtype in ("llm", "vlm") else model_kind(mid))
        return ModelInfo(id=mid, label=str(item.get("display_name") or ""), context=ctx if isinstance(ctx, int) else None,
                         loaded=loaded, vision=vision, tools=tools, kind=kind)
