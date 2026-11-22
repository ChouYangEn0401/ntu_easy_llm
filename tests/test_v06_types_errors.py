"""0.6 contract: neutral types, ErrorKind / classify, error attributes, import isolation. Offline."""
from __future__ import annotations

import subprocess
import sys

import pytest

import ntu_easy_llm as L
from ntu_easy_llm import agent
from ntu_easy_llm.agent import ChatReply, LLMConnectionError, LLMError, LLMHTTPError, LLMResponseError, ToolCall
from ntu_easy_llm.errors import (DEFAULT_TRIGGERS, ErrorKind, classify, describe_kind, kind_of, parse_retry_after,
                                 register_classifier)
from ntu_easy_llm.types import ImagePart, Message, as_message, as_messages, normalize_finish

PNG = b"\x89PNG\r\n\x1a\nfake-bytes"


# --------------------------------------------------------------------------- #
# import isolation / compatibility
# --------------------------------------------------------------------------- #

def test_new_modules_do_not_load_sdks():
    code = ("import sys, ntu_easy_llm, ntu_easy_llm.types, ntu_easy_llm.errors, ntu_easy_llm.profiles, "
            "ntu_easy_llm.conversation, ntu_easy_llm.catalog, ntu_easy_llm.adapters.base, "
            "ntu_easy_llm.adapters.fake, ntu_easy_llm.waterfall\n"
            "from ntu_easy_llm.catalog import catalog; catalog()\n"
            "print(sorted({'openai','anthropic','google.genai','llmop','yaml'} & set(sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_old_api_still_importable():
    from ntu_easy_llm import LLMSession, ask  # noqa: F401
    from ntu_easy_llm.agent import Tool, chat, probe  # noqa: F401


def test_types_reexport_the_same_classes():
    from ntu_easy_llm import types as T
    assert T.ChatReply is agent.ChatReply and T.ToolCall is agent.ToolCall
    assert T.Tool is agent.Tool and T.Usage is agent.Usage


def test_chat_reply_new_fields_have_defaults():
    r = ChatReply(text="x")
    assert r.finish == "" and r.warnings == []
    assert ChatReply().warnings is not ChatReply().warnings


def test_top_level_lazy_names():
    assert L.Message is Message
    assert L.ErrorKind is ErrorKind
    from ntu_easy_llm.profiles import load
    assert L.load_profiles is load


# --------------------------------------------------------------------------- #
# Message / ImagePart
# --------------------------------------------------------------------------- #

def test_image_part_repr_hides_bytes_and_describes():
    im = ImagePart("image/PNG", PNG)
    assert "fake-bytes" not in repr(im) and "size=" in repr(im)
    assert im.mime == "image/png"
    d = im.describe()
    assert d["size"] == len(PNG) and len(d["sha256"]) == 64
    assert ImagePart.from_data_url(im.to_data_url()) == im


def test_image_part_validation():
    with pytest.raises(ValueError):
        ImagePart("text/plain", b"x")
    with pytest.raises(TypeError):
        ImagePart("image/png", "not bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ImagePart.from_data_url("https://example.com/a.png")
    with pytest.raises(ValueError):
        ImagePart.from_base64("image/png", "%%%")


def test_message_rules():
    with pytest.raises(ValueError):
        Message("robot")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Message("assistant", images=[ImagePart("image/png", PNG)])
    with pytest.raises(ValueError):
        Message("user", tool_calls=[ToolCall("c", "f")])
    m = Message("assistant", tool_calls=[{"id": "c1", "name": "f", "arguments": '{"a": 1}'}])
    assert m.tool_calls == [ToolCall("c1", "f", {"a": 1})]


def test_as_message_dicts_objects_and_parts():
    m = as_message({"role": "tool", "tool_call_id": "c1", "name": "f", "content": "ok"})
    assert (m.role, m.tool_call_id, m.name) == ("tool", "c1", "f")
    url = ImagePart("image/png", PNG).to_data_url()
    m = as_message({"role": "user", "content": [{"type": "text", "text": "look "},
                                                {"type": "image_url", "image_url": {"url": url}},
                                                {"type": "text", "text": "here"}]})
    assert m.content == "look here" and m.images[0].data == PNG
    import base64
    m = as_message({"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.b64encode(PNG).decode()}}]})
    assert m.images[0].mime == "image/png"
    with pytest.raises(ValueError):
        as_message({"role": "user", "content": [{"type": "audio", "data": "x"}]})
    assert [x.role for x in as_messages([{"role": "system", "content": "s"}, Message("user", "u")])] == \
        ["system", "user"]


def test_message_to_dict_feeds_old_converters():
    msgs = [Message("system", "s"), Message("user", "u"),
            Message("assistant", tool_calls=[ToolCall("c0", "f", {"x": 1})]),
            Message("tool", "ok", tool_call_id="c0", name="f")]
    wire = agent.to_openai_messages(msgs)          # objects with attributes are accepted
    assert wire[2]["tool_calls"][0]["function"]["name"] == "f"
    assert agent.to_openai_messages([m.to_dict() for m in msgs]) == wire


@pytest.mark.parametrize("raw,text,calls,want", [
    ("stop", "hi", False, "stop"), ("end_turn", "hi", False, "stop"),
    ("length", "partial", False, "length"), ("max_tokens", "", False, "thinking"),
    ("tool_use", "", True, "tool_calls"), ("", "", True, "tool_calls"), ("weird", "x", False, ""),
])
def test_normalize_finish(raw, text, calls, want):
    assert normalize_finish(raw, text=text, has_tool_calls=calls) == want


# --------------------------------------------------------------------------- #
# ErrorKind / classify
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status,body,kind", [
    (429, '{"error":{"code":"insufficient_quota"}}', ErrorKind.QUOTA_EXHAUSTED),
    (429, '{"error":{"code":"rate_limit_exceeded"}}', ErrorKind.RATE_LIMITED),
    (401, "", ErrorKind.AUTH_INVALID),
    (401, '{"error":"key_exhausted"}', ErrorKind.QUOTA_EXHAUSTED),
    (400, '{"error":{"status":"INVALID_ARGUMENT","reason":"API_KEY_INVALID"}}', ErrorKind.AUTH_INVALID),
    (400, "Your credit balance is too low", ErrorKind.QUOTA_EXHAUSTED),
    (402, "", ErrorKind.QUOTA_EXHAUSTED),
    (403, "encryption_required", ErrorKind.PERMISSION),
    (400, "Context size has been exceeded.", ErrorKind.CONTEXT_TOO_LONG),
    (404, '{"error":"model_not_found"}', ErrorKind.MODEL_NOT_FOUND),
    (404, "Not Found", ErrorKind.BAD_REQUEST),
    (422, "bad things", ErrorKind.BAD_REQUEST),
    (503, "", ErrorKind.OVERLOADED), (529, "", ErrorKind.OVERLOADED),
    (524, "", ErrorKind.TIMEOUT), (504, "", ErrorKind.TIMEOUT),
    (530, "", ErrorKind.CONNECTION), (500, "", ErrorKind.SERVER_ERROR),
])
def test_classify_generic_rules(status, body, kind):
    assert classify("openai_compatible", status=status, body=body)[0] is kind


def test_classify_tools_and_exceptions():
    assert classify(status=400, body="this model does not support tools", tools_sent=True)[0] \
        is ErrorKind.TOOLS_UNSUPPORTED
    assert classify(status=400, body="this model does not support tools")[0] is ErrorKind.BAD_REQUEST
    assert classify(exc=TimeoutError("x"))[0] is ErrorKind.TIMEOUT
    assert classify(exc=ConnectionRefusedError())[0] is ErrorKind.CONNECTION
    assert classify(exc=ValueError("bad json"))[0] is ErrorKind.BAD_RESPONSE
    assert classify(exc=RuntimeError("??"))[0] is ErrorKind.UNKNOWN
    assert classify()[0] is ErrorKind.UNKNOWN


def test_retry_after_parsing():
    assert parse_retry_after({"Retry-After": "7"}) == 7.0
    assert parse_retry_after({"retry-after-ms": "1500"}) == 1.5
    assert parse_retry_after({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}, now=1445412470.0) == 10.0
    assert parse_retry_after({"Retry-After": "soon"}) is None
    assert parse_retry_after(None) is None
    assert classify(status=429, headers={"Retry-After": "3"}) == (ErrorKind.RATE_LIMITED, 3.0)


def test_register_classifier_runs_first_and_falls_through():
    def rules(*, status, body, headers, exc, tools_sent):
        if status == 404 and body == "Not Found":
            return ErrorKind.LINK_CHANGED, None
        return None
    register_classifier("demo_relay", rules)
    try:
        assert classify("demo_relay", status=404, body="Not Found")[0] is ErrorKind.LINK_CHANGED
        assert classify("DEMO_RELAY", status=401)[0] is ErrorKind.AUTH_INVALID
        assert classify("other", status=404, body="Not Found")[0] is ErrorKind.BAD_REQUEST
    finally:
        register_classifier("demo_relay", None)
    assert classify("demo_relay", status=404, body="Not Found")[0] is ErrorKind.BAD_REQUEST


def test_error_classes_carry_kind_retry_after_profile():
    e = LLMHTTPError(429, '{"error":{"code":"insufficient_quota"}}', "openai", headers={"Retry-After": "12"})
    assert (e.kind, e.retry_after, e.status) == (ErrorKind.QUOTA_EXHAUSTED, 12.0, 429)
    assert e.headers == {"Retry-After": "12"}
    assert LLMHTTPError(401).kind is ErrorKind.AUTH_INVALID           # old call shape still works
    assert LLMHTTPError(500, kind="trust").kind is ErrorKind.TRUST       # explicit kind wins
    assert LLMConnectionError("x").kind is ErrorKind.CONNECTION
    assert LLMConnectionError("x", kind=ErrorKind.TIMEOUT).kind is ErrorKind.TIMEOUT
    assert LLMResponseError("x").kind is ErrorKind.BAD_RESPONSE
    base = LLMError("x")
    assert (base.kind, base.retry_after, base.profile_id) == (ErrorKind.UNKNOWN, None, "")
    assert LLMError("x", profile_id="p1").profile_id == "p1"
    assert kind_of(e) is ErrorKind.QUOTA_EXHAUSTED and kind_of(TimeoutError()) is ErrorKind.TIMEOUT
    assert isinstance(e, LLMError)


def test_agent_chat_errors_now_have_kinds():
    class Resp:
        status_code, text, headers = 429, '{"error":{"code":"rate_limit_exceeded"}}', {"retry-after": "2"}

        def json(self):
            return {}

    class Sess:
        def __init__(self, resp=None, exc=None):
            self.resp, self.exc = resp, exc

        def post(self, url, json=None, headers=None, timeout=None):
            if self.exc:
                raise self.exc
            return self.resp

    with pytest.raises(LLMHTTPError) as ei:
        agent.chat("openai", "m", [{"role": "user", "content": "hi"}], api_key="k", session=Sess(Resp()))
    assert ei.value.kind is ErrorKind.RATE_LIMITED and ei.value.retry_after == 2.0
    with pytest.raises(LLMConnectionError) as ei:
        agent.chat("openai", "m", [{"role": "user", "content": "hi"}], api_key="k",
                   session=Sess(exc=TimeoutError("timed out")))
    assert ei.value.kind is ErrorKind.TIMEOUT


def test_default_triggers_and_labels():
    assert DEFAULT_TRIGGERS == {ErrorKind.QUOTA_EXHAUSTED, ErrorKind.AUTH_INVALID, ErrorKind.RATE_LIMITED}
    assert describe_kind("quota_exhausted") == "額度用完"
    assert describe_kind(ErrorKind.AUTH_INVALID, "en") == "key invalid"
    assert str(ErrorKind.TRUST) == "trust"


def test_errors_module_reexports_lazily():
    from ntu_easy_llm import errors
    assert errors.LLMError is LLMError
    from ntu_easy_llm.waterfall import FallbackRequired
    assert errors.FallbackRequired is FallbackRequired
