"""0.6 Anthropic Messages API adapter. Offline: scripted transport, real network blocked."""
from __future__ import annotations

import pytest

from _fakehttp import FakeResponse, FakeTransport, anthropic_sse, block_network
from ntu_easy_llm.adapters import get_adapter
from ntu_easy_llm.adapters.anthropic import DEFAULT_MAX_TOKENS, AnthropicAdapter
from ntu_easy_llm.agent import LLMHTTPError, Tool, ToolCall
from ntu_easy_llm.errors import ErrorKind
from ntu_easy_llm.profiles import Profile
from ntu_easy_llm.types import ImagePart, Message

USER = [{"role": "user", "content": "hi"}]
ENV = {"EXAMPLE_KEY": "example-secret-value"}
TOOL = Tool("add", "add a number", {"type": "object", "properties": {"n": {"type": "number"}}})


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


def make(http, **options) -> AnthropicAdapter:
    p = Profile("claude-api", "remote/api_key/anthropic", model="example-model", secret="env:EXAMPLE_KEY",
                options=options)
    return get_adapter(p, http=http, environ=ENV)


def reply(content, stop="end_turn", usage=(10, 4)):
    return FakeResponse(200, {"type": "message", "role": "assistant", "content": content, "stop_reason": stop,
                              "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}})


def test_request_shape_and_text_reply():
    t = FakeTransport(("POST", "/v1/messages", reply([{"type": "text", "text": "hello"}])))
    a = make(t)
    assert isinstance(a, AnthropicAdapter) and a.direct is False
    r = a.chat([{"role": "system", "content": "be brief"}, *USER], stop=["END"], temperature=0.5)
    req = t.sent[0]
    assert req["url"].endswith("/v1/messages")
    assert req["headers"]["x-api-key"] == "example-secret-value" and req["headers"]["anthropic-version"]
    body = req["json"]
    assert body["system"] == "be brief" and body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_tokens"] == DEFAULT_MAX_TOKENS and body["stop_sequences"] == ["END"]
    assert body["temperature"] == 0.5
    assert r.text == "hello" and r.finish == "stop" and r.usage.output_tokens == 4


def test_max_tokens_option_and_tool_use():
    t = FakeTransport(("POST", "/v1/messages", reply([{"type": "text", "text": "adding"},
                                                     {"type": "tool_use", "id": "tu1", "name": "add",
                                                      "input": {"n": 2}}], stop="tool_use")))
    a = make(t, max_tokens=300)
    history = [*USER, {"role": "assistant", "content": "", "tool_calls": [ToolCall("tu0", "add", {"n": 1})]},
               {"role": "tool", "tool_call_id": "tu0", "name": "add", "content": "1"}]
    r = a.chat(history, tools=[TOOL])
    body = t.sent[0]["json"]
    assert body["max_tokens"] == 300 and body["tools"][0]["input_schema"]["type"] == "object"
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu0", "content": "1"}
    assert r.finish == "tool_calls" and r.tool_calls == [ToolCall("tu1", "add", {"n": 2})]


def test_images_become_base64_blocks():
    t = FakeTransport(("POST", "/v1/messages", reply([{"type": "text", "text": "a cat"}])))
    make(t).chat([Message("user", "what?", images=[ImagePart("image/png", b"\x89PNGfake")])])
    blocks = t.sent[0]["json"]["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["type"] == "base64" and blocks[1] == {"type": "text", "text": "what?"}


def test_max_tokens_hit_without_text_is_thinking():
    t = FakeTransport(("POST", "/v1/messages", reply([], stop="max_tokens")))
    assert make(t).chat(USER).finish == "thinking"


def test_stream_text_and_tool_json():
    events = anthropic_sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 11, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "tu1", "name": "add", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"n":'}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": " 5}"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 20}},
        {"type": "message_stop"})
    t = FakeTransport(("POST", "/v1/messages", FakeResponse(200, lines=["event: ping", 'data: {"type": "ping"}', "",
                                                                         *events])))
    pieces: list[str] = []
    r = make(t).chat(USER, tools=[TOOL], on_delta=pieces.append)
    assert t.sent[0]["json"]["stream"] is True and t.sent[0]["stream"] is True
    assert pieces == ["Hel", "lo"] and r.text == "Hello"
    assert r.tool_calls == [ToolCall("tu1", "add", {"n": 5})] and r.finish == "tool_calls"
    assert (r.usage.input_tokens, r.usage.output_tokens) == (11, 20)


def test_stream_error_event_is_classified():
    lines = anthropic_sse({"type": "message_start", "message": {"usage": {}}},
                          {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    t = FakeTransport(("POST", "/v1/messages", FakeResponse(200, lines=lines)))
    with pytest.raises(LLMHTTPError) as ei:
        make(t).chat(USER, on_delta=lambda s: None)
    assert ei.value.kind is ErrorKind.OVERLOADED and ei.value.profile_id == "claude-api"


@pytest.mark.parametrize("status,body,kind", [
    (529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, ErrorKind.OVERLOADED),
    (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
     ErrorKind.AUTH_INVALID),
    (403, {"type": "error", "error": {"type": "permission_error", "message": "no"}}, ErrorKind.PERMISSION),
    (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}, ErrorKind.RATE_LIMITED),
    (400, {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "Your credit balance is too low to access the API."}},
     ErrorKind.QUOTA_EXHAUSTED),
    (400, {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "prompt is too long: 300000 tokens > 200000 maximum"}},
     ErrorKind.CONTEXT_TOO_LONG),
    (404, {"type": "error", "error": {"type": "not_found_error", "message": "model: example-missing"}},
     ErrorKind.MODEL_NOT_FOUND),
    (413, {"type": "error", "error": {"type": "request_too_large", "message": "too big"}},
     ErrorKind.CONTEXT_TOO_LONG),
    (500, {"type": "error", "error": {"type": "api_error", "message": "oops"}}, ErrorKind.SERVER_ERROR),
])
def test_errors(status, body, kind):
    t = FakeTransport(("POST", "/v1/messages", FakeResponse(status, body, headers={"retry-after": "4"})))
    with pytest.raises(LLMHTTPError) as ei:
        make(t).chat(USER)
    assert ei.value.kind is kind and ei.value.profile_id == "claude-api" and ei.value.retry_after == 4.0


def test_models_and_probe():
    t = FakeTransport(("GET", "/v1/models", FakeResponse(200, {"data": [
        {"type": "model", "id": "example-model", "display_name": "Example"}], "has_more": False})),
        ("POST", "/v1/messages", reply([{"type": "text", "text": "ok"}])))
    a = make(t)
    ms = a.list_models()
    assert ms[0].id == "example-model" and ms[0].label == "Example"
    assert t.sent[0]["headers"]["x-api-key"] == "example-secret-value"
    assert a.probe().ok and t.posts() == []
    assert a.probe(send=True).ok and len(t.posts()) == 1
