"""HTTP adapter for ``remote/api_key/anthropic`` (the Anthropic Messages API, ``/v1/messages``).

Messages go through :func:`ntu_easy_llm.agent.to_anthropic_messages` (system
messages become the top-level ``system``; images become base64 ``image``
blocks; consecutive tool results are merged into one user turn). ``stop``
maps to ``stop_sequences``. ``max_tokens`` is required by the API: when
neither the call nor ``options.max_tokens`` gives one, 4096 is sent.
``on_delta`` streams with server-sent events. No SDK is imported.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ..agent import (ANTHROPIC_VERSION, DEFAULT_BASE_URLS, ChatReply, LLMResponseError, ToolCall, Usage,
                     _anthropic_reply, _anthropic_tools, _parse_args, to_anthropic_messages)
from ._http import HttpAdapter, _lines, iter_sse, model_kind
from .base import Capabilities, ModelInfo

__all__ = ["AnthropicAdapter", "DEFAULT_MAX_TOKENS"]

DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter(HttpAdapter):
    """``remote/api_key/anthropic``."""

    provider = "anthropic"
    default_base_url = DEFAULT_BASE_URLS["anthropic"]
    needs_key = True
    base_capabilities = Capabilities(tools=True, vision=None, stop=True, stream=True, usage=True)

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key(), "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json"}

    def _chat(self, msgs, tools, *, max_tokens, temperature, stop, timeout, on_delta) -> ChatReply:
        system, wire = to_anthropic_messages(msgs)
        body: dict[str, Any] = {"model": self._model(), "max_tokens": int(max_tokens or DEFAULT_MAX_TOKENS),
                                "messages": wire}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        if stop:
            body["stop_sequences"] = list(stop)
        if tools:
            body["tools"] = _anthropic_tools(tools)
        stream = on_delta is not None
        if stream:
            body["stream"] = True
        body.update(self._extra_body())
        headers = self._headers()
        if stream:
            headers["accept"] = "text/event-stream"
        resp = self._send("POST", f"{self.base_url}/v1/messages", body=body, headers=headers, timeout=timeout,
                          stream=stream, tools_sent=bool(tools))
        if stream:
            return self._read_stream(resp, on_delta, tools_sent=bool(tools))
        return self._reply(self._json(resp))

    def _reply(self, data: Any) -> ChatReply:
        try:
            return _anthropic_reply(data)
        except LLMResponseError as e:
            e.profile_id = self.profile.id
            raise

    def _read_stream(self, resp: Any, on_delta: Callable[[str], None], *, tools_sent: bool) -> ChatReply:
        text: list[str] = []
        blocks: dict[int, dict[str, Any]] = {}
        stop_reason = ""
        usage = {"input_tokens": 0, "output_tokens": 0}
        events = 0
        raw_lines: list[str] = []

        def tee():
            for line in _lines(resp):
                if len(raw_lines) < 2000:
                    raw_lines.append(line)
                yield line

        for event, data in iter_sse(tee()):
            try:
                ev = json.loads(data)
            except ValueError as e:
                raise self._bad_response("stream event is not JSON", e) from e
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type") or event
            events += 1
            if etype == "error":
                self._raise_http(500, json.dumps(ev, ensure_ascii=False), None, tools_sent=tools_sent)
            elif etype == "message_start":
                u = (ev.get("message") or {}).get("usage") or {}
                usage["input_tokens"] = int(u.get("input_tokens") or 0)
                usage["output_tokens"] = int(u.get("output_tokens") or 0)
            elif etype == "content_block_start":
                block = dict(ev.get("content_block") or {})
                block.setdefault("partial_json", "")
                blocks[int(ev.get("index", len(blocks)))] = block
                if block.get("type") == "text" and block.get("text"):
                    text.append(block["text"])
                    on_delta(block["text"])
            elif etype == "content_block_delta":
                delta = ev.get("delta") or {}
                block = blocks.setdefault(int(ev.get("index", 0)), {"type": "text", "partial_json": ""})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    text.append(delta["text"])
                    on_delta(delta["text"])
                elif delta.get("type") == "input_json_delta":
                    block["partial_json"] += str(delta.get("partial_json") or "")
            elif etype == "message_delta":
                d = ev.get("delta") or {}
                if d.get("stop_reason"):
                    stop_reason = str(d["stop_reason"])
                u = ev.get("usage") or {}
                if u.get("output_tokens") is not None:
                    usage["output_tokens"] = int(u["output_tokens"])
            elif etype == "message_stop":
                break
        if events == 0:
            whole = "\n".join(raw_lines).strip()
            if whole.startswith("{"):            # the server answered without streaming
                try:
                    data = json.loads(whole)
                except ValueError as e:
                    raise self._bad_response("reply is not JSON", e) from e
                reply = self._reply(data)
                if reply.text:
                    on_delta(reply.text)
                return reply
            raise self._bad_response("empty streamed reply")
        calls = []
        for _i, b in sorted(blocks.items()):
            if b.get("type") == "tool_use":
                args = _parse_args(b["partial_json"]) if b.get("partial_json") else dict(b.get("input") or {})
                calls.append(ToolCall(id=str(b.get("id") or ""), name=str(b.get("name") or ""), arguments=args))
        return ChatReply(text="".join(text), tool_calls=calls,
                         usage=Usage(usage["input_tokens"], usage["output_tokens"]),
                         raw={"stream": True, "events": events}, stop_reason=stop_reason)

    def _list(self, *, timeout: float) -> list[ModelInfo]:
        data = self._get_json(f"{self.base_url}/v1/models?limit=1000", headers=self._headers(), timeout=timeout)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise self._bad_response("model list is not in the expected format")
        return [ModelInfo(id=str(m["id"]), label=str(m.get("display_name") or ""), kind=model_kind(str(m["id"])))
                for m in items if isinstance(m, dict) and m.get("id")]
