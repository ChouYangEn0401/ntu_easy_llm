"""0.6 Ollama adapter (native /api/chat with a /v1 fallback). Offline: scripted transport, network blocked."""
from __future__ import annotations

import json

import pytest

from _fakehttp import FakeResponse, FakeTransport, block_network, openai_body, sse
from ntu_easy_llm.adapters import get_adapter
from ntu_easy_llm.adapters.ollama import OllamaAdapter
from ntu_easy_llm.agent import LLMHTTPError, Tool, ToolCall
from ntu_easy_llm.errors import ConfigError, ErrorKind
from ntu_easy_llm.profiles import Profile
from ntu_easy_llm.types import ImagePart, Message

USER = [{"role": "user", "content": "hi"}]
TOOL = Tool("add", "add a number", {"type": "object", "properties": {"n": {"type": "number"}}})


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


def make(http, *, base_url=None, model="example-model", **options) -> OllamaAdapter:
    return get_adapter(Profile("home", "local/ollama", model=model, base_url=base_url, options=options), http=http)


def native(content="hi", *, calls=(), done_reason="stop", counts=(7, 2)) -> FakeResponse:
    return FakeResponse(200, {"model": "example-model", "message": {"role": "assistant", "content": content,
                                                                    "tool_calls": list(calls)},
                              "done": True, "done_reason": done_reason,
                              "prompt_eval_count": counts[0], "eval_count": counts[1]})


def test_native_request_shape():
    t = FakeTransport(("POST", "/api/chat", native("hello")))
    a = make(t, context_size=8192, max_tokens=64, temperature=0.1)
    assert a.direct is True and a.native is True
    r = a.chat(USER, stop=["###"])
    req = t.sent[0]
    assert req["url"] == "http://127.0.0.1:11434/api/chat"
    body = req["json"]
    assert body["stream"] is False and "think" not in body
    assert body["options"] == {"num_ctx": 8192, "num_predict": 64, "temperature": 0.1, "stop": ["###"]}
    assert r.text == "hello" and r.finish == "stop" and (r.usage.input_tokens, r.usage.output_tokens) == (7, 2)


def test_think_option_images_and_tools_shape():
    calls = [{"function": {"name": "add", "arguments": {"n": 4}}}]
    t = FakeTransport(("POST", "/api/chat", native("", calls=calls)))
    a = make(t, think=False)
    history = [Message("user", "look", images=[ImagePart("image/png", b"\x89PNGfake")]),
               Message("assistant", "", tool_calls=[ToolCall("c0", "add", {"n": 1})]),
               Message("tool", "1", tool_call_id="c0", name="add")]
    r = a.chat(history, tools=[TOOL])
    body = t.sent[0]["json"]
    assert body["think"] is False and body["tools"][0]["function"]["name"] == "add"
    m0, m1, m2 = body["messages"]
    assert m0["content"] == "look" and isinstance(m0["images"][0], str)
    assert m1["tool_calls"] == [{"function": {"name": "add", "arguments": {"n": 1}}}]
    assert m2 == {"role": "tool", "content": "1", "tool_name": "add"}
    assert r.finish == "tool_calls" and r.tool_calls == [ToolCall("call_0", "add", {"n": 4})]


def test_length_without_text_is_thinking():
    t = FakeTransport(("POST", "/api/chat", native("", done_reason="length")))
    assert make(t).chat(USER).finish == "thinking"


def test_native_stream_ndjson():
    lines = [json.dumps({"message": {"content": "Hel"}, "done": False}),
             json.dumps({"message": {"content": "lo"}, "done": False}),
             json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop",
                         "prompt_eval_count": 3, "eval_count": 2})]
    t = FakeTransport(("POST", "/api/chat", FakeResponse(200, lines=lines)))
    pieces: list[str] = []
    r = make(t).chat(USER, on_delta=pieces.append)
    assert t.sent[0]["json"]["stream"] is True and t.sent[0]["stream"] is True
    assert pieces == ["Hel", "lo"] and r.text == "Hello" and r.finish == "stop" and r.usage.output_tokens == 2


def test_native_stream_error_line():
    lines = [json.dumps({"message": {"content": "a"}, "done": False}),
             json.dumps({"error": "the input length exceeds the context length"})]
    t = FakeTransport(("POST", "/api/chat", FakeResponse(200, lines=lines)))
    with pytest.raises(LLMHTTPError) as ei:
        make(t).chat(USER, on_delta=lambda s: None)
    assert ei.value.kind is ErrorKind.CONTEXT_TOO_LONG and ei.value.profile_id == "home"


@pytest.mark.parametrize("status,body,tools,kind", [
    (404, {"error": 'model "example-model" not found, try pulling it first'}, False, ErrorKind.MODEL_NOT_FOUND),
    (400, {"error": "registry.example/library/example-model does not support tools"}, True,
     ErrorKind.TOOLS_UNSUPPORTED),
    (400, {"error": "the input length exceeds the context length"}, False, ErrorKind.CONTEXT_TOO_LONG),
    (500, {"error": "llama runner process has terminated"}, False, ErrorKind.SERVER_ERROR),
])
def test_native_errors(status, body, tools, kind):
    t = FakeTransport(("POST", "/api/chat", FakeResponse(status, body)))
    with pytest.raises(LLMHTTPError) as ei:
        make(t).chat(USER, tools=[TOOL] if tools else None)
    assert ei.value.kind is kind and ei.value.profile_id == "home"


def test_v1_base_url_and_api_option_use_openai_style():
    t = FakeTransport(("POST", "/v1/chat/completions", FakeResponse(200, openai_body("via v1"))))
    a = make(t, base_url="http://127.0.0.1:11434/v1")
    assert a.native is False and a.chat(USER).text == "via v1"
    assert t.sent[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    b = make(t, api="openai")
    assert b.native is False and b.chat(USER).text == "via v1"
    with pytest.raises(ConfigError):
        make(t, api="grpc")


def test_v1_stream():
    t = FakeTransport(("POST", "/v1/chat/completions", FakeResponse(200, lines=sse(
        {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}))))
    pieces: list[str] = []
    assert make(t, api="openai").chat(USER, on_delta=pieces.append).text == "x" and pieces == ["x"]


def test_native_404_without_model_falls_back_to_v1_once():
    t = FakeTransport(("POST", "/api/chat", FakeResponse(404, "404 page not found")),
                      ("POST", "/v1/chat/completions", FakeResponse(200, openai_body("fallback"))))
    a = make(t)
    assert a.chat(USER).text == "fallback" and a.native is False
    a.chat(USER)
    root = "http://127.0.0.1:11434"
    assert [r["url"] for r in t.posts()] == [f"{root}/api/chat", f"{root}/v1/chat/completions",
                                             f"{root}/v1/chat/completions"]


def test_models_from_tags_and_ps():
    tags = {"models": [{"name": "example-model:latest", "model": "example-model:latest"},
                       {"name": "example-embed:latest"}]}
    ps = {"models": [{"name": "example-model:latest"}]}
    t = FakeTransport(("GET", "/api/tags", FakeResponse(200, tags)), ("GET", "/api/ps", FakeResponse(200, ps)))
    a = make(t)
    ms = a.list_models()
    assert [(m.id, m.loaded, m.kind) for m in ms] == [("example-model:latest", True, "chat"),
                                                     ("example-embed:latest", False, "embedding")]
    res = a.probe()                                     # "example-model" matches "example-model:latest"
    assert res.ok and t.posts() == []


def test_models_without_ps_and_with_only_v1():
    t = FakeTransport(("GET", "/api/tags", FakeResponse(200, {"models": [{"name": "m:latest"}]})),
                      ("GET", "/api/ps", ConnectionResetError("reset")))
    assert make(t, model="m").list_models()[0].loaded is None
    t = FakeTransport(("GET", "/api/tags", FakeResponse(404, "")),
                      ("GET", "/v1/models", FakeResponse(200, {"data": [{"id": "m"}]})))
    assert [m.id for m in make(t, model="m").list_models()] == ["m"]


def test_probe_model_not_pulled():
    t = FakeTransport(("GET", "/api/tags", FakeResponse(200, {"models": [{"name": "other:latest"}]})),
                      ("GET", "/api/ps", FakeResponse(200, {"models": []})))
    res = make(t).probe()
    assert not res.ok and res.kind is ErrorKind.MODEL_NOT_FOUND
