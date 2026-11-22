"""0.6 contract: adapter registry, FakeAdapter, catalog, sessions and stores. Offline."""
from __future__ import annotations

import json
import threading

import pytest

from ntu_easy_llm.adapters import (Adapter, Capabilities, ModelInfo, ProbeResult, adapter_status, get_adapter,
                                   register_adapter, registered_sources, unregister_adapter)
from ntu_easy_llm.adapters.fake import FakeAdapter, FakeScriptExhausted, fake_error
from ntu_easy_llm.agent import ChatReply, LLMConnectionError, LLMError, LLMHTTPError, LLMResponseError
from ntu_easy_llm.catalog import FIELD_KINDS, catalog, find, leaves, list_llop_profiles
from ntu_easy_llm.conversation import ChatSession, JsonlSessionStore, MemorySessionStore, SessionStore
from ntu_easy_llm.errors import AdapterNotFound, ErrorKind
from ntu_easy_llm.profiles import Profile, load
from ntu_easy_llm.types import ImagePart, Message
from ntu_easy_llm.waterfall import Switched

PNG = b"\x89PNG\r\n\x1a\nfake"
USER = [{"role": "user", "content": "hi"}]


# --------------------------------------------------------------------------- #
# FakeAdapter
# --------------------------------------------------------------------------- #

def test_fake_adapter_follows_script_and_records_calls():
    a = FakeAdapter(["one", {"text": "", "tool_calls": [{"id": "c1", "name": "f", "arguments": {"x": 1}}]},
                     ErrorKind.QUOTA_EXHAUSTED, {"error": "rate_limited", "retry_after": 2}],
                    profile=Profile("p1", "local/fake"))
    assert isinstance(a, Adapter)
    r = a.chat(USER, max_tokens=5)
    assert r.text == "one" and r.finish == "stop"
    r = a.chat(USER)
    assert r.finish == "tool_calls" and r.tool_calls[0].arguments == {"x": 1}
    with pytest.raises(LLMHTTPError) as ei:
        a.chat(USER)
    assert ei.value.kind is ErrorKind.QUOTA_EXHAUSTED and ei.value.profile_id == "p1"
    with pytest.raises(LLMError) as ei:
        a.chat(USER)
    assert ei.value.kind is ErrorKind.RATE_LIMITED and ei.value.retry_after == 2
    with pytest.raises(FakeScriptExhausted):
        a.chat(USER)
    assert len(a.calls) == 5 and a.calls[0]["max_tokens"] == 5
    assert isinstance(a.calls[0]["messages"][0], Message)


@pytest.mark.parametrize("kind", list(ErrorKind))
def test_fake_error_every_kind(kind):
    e = fake_error(kind, profile_id="p")
    assert isinstance(e, LLMError) and e.kind is kind and e.profile_id == "p"
    if kind in (ErrorKind.TIMEOUT, ErrorKind.CONNECTION):
        assert isinstance(e, LLMConnectionError)
    if kind is ErrorKind.BAD_RESPONSE:
        assert isinstance(e, LLMResponseError)


def test_fake_adapter_capabilities_default_and_delta():
    a = FakeAdapter([], default="hello big world", capabilities=Capabilities(tools=False, vision=False, stop=False))
    with pytest.raises(LLMError) as ei:
        a.chat(USER, tools=[{"name": "f"}])
    assert ei.value.kind is ErrorKind.TOOLS_UNSUPPORTED
    with pytest.raises(LLMError) as ei:
        a.chat([Message("user", "look", images=[ImagePart("image/png", PNG)])])
    assert ei.value.kind is ErrorKind.BAD_REQUEST
    chunks: list[str] = []
    r = a.chat(USER, stop=["\n"], on_delta=chunks.append)
    assert "".join(chunks) == "hello big world" and r.warnings
    assert a.chat(USER).warnings == []                    # default reply is not shared/mutated


def test_fake_adapter_exceptions_callables_models_probe():
    boom = LLMHTTPError(500, "x", "fake")
    a = FakeAdapter([boom, lambda msgs, tools, **kw: f"echo:{msgs[-1].content}", ChatReply(text="r")],
                    models=["m1", {"id": "m2", "loaded": True}, ModelInfo("m3", kind="embedding")])
    with pytest.raises(LLMHTTPError):
        a.chat(USER)
    assert a.chat(USER).text == "echo:hi"
    assert [m.id for m in a.list_models()] == ["m1", "m2", "m3"]
    res = a.probe()
    assert isinstance(res, ProbeResult) and res.ok and [m.id for m in res.models] == ["m1", "m2", "m3"]
    assert a.probe(send=True).ok
    bad = FakeAdapter([ErrorKind.AUTH_INVALID], models=["m"])
    res = bad.probe(send=True)
    assert not res.ok and res.kind is ErrorKind.AUTH_INVALID
    assert not FakeAdapter([], models=ErrorKind.CONNECTION).probe().ok


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

def test_get_adapter_for_fake_profile_from_config():
    ps = load({"profiles": {"f": {"source": "local/fake", "model": "fake-x",
                                  "options": {"script": ["a", {"error": "auth_invalid"}], "default": "z"}}}},
              environ={})
    a = ps.adapter()
    assert a.chat(USER).text == "a"
    with pytest.raises(LLMError) as ei:
        a.chat(USER)
    assert ei.value.kind is ErrorKind.AUTH_INVALID and ei.value.profile_id == "f"
    assert a.chat(USER).text == "z"
    assert [m.id for m in a.list_models()] == ["fake-x"]
    assert "local/fake" in registered_sources()


def test_unknown_source_and_register_unregister():
    p = Profile("o", "remote/api_key/openai", secret="env:EXAMPLE_KEY")
    with pytest.raises(AdapterNotFound):
        get_adapter(p)
    seen = {}

    def factory(profile, *, http=None, resolve_secret):
        seen["secret"] = resolve_secret(profile.secret)
        return FakeAdapter(["from-custom"], profile=profile)
    register_adapter("remote/api_key/openai", factory)
    try:
        with pytest.raises(ValueError):
            register_adapter("remote/api_key/openai", factory)
        a = get_adapter(p, environ={"EXAMPLE_KEY": "value"})
        assert a.chat(USER).text == "from-custom" and seen["secret"] == "value"
        assert adapter_status("remote/api_key/openai") == (True, "")
    finally:
        unregister_adapter("remote/api_key/openai")
    assert adapter_status("remote/api_key/openai")[0] is False


def test_factory_check_blocks_use():
    def factory(profile, **kw):
        return FakeAdapter([], profile=profile)
    factory.check = lambda: (False, "dependency missing")
    register_adapter("remote/subscription/claude", factory)
    try:
        assert adapter_status("remote/subscription/claude") == (False, "dependency missing")
        with pytest.raises(AdapterNotFound, match="dependency missing"):
            get_adapter(Profile("c", "remote/subscription/claude"))
        node = find("remote/subscription/claude", locale="en")
        assert not node.available and node.unavailable_reason == "dependency missing"
    finally:
        unregister_adapter("remote/subscription/claude")


# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #

def test_catalog_tree_shape():
    root = catalog()
    paths = [n.path for n in leaves()]
    assert paths == ["local/ollama", "local/lmstudio", "local/vllm", "local/llamacpp", "local/openai_compatible",
                     "remote/api_key/openai", "remote/api_key/anthropic", "remote/api_key/gemini",
                     "remote/api_key/openai_compatible", "remote/api_key/llop",
                     "remote/subscription/claude", "remote/subscription/github_copilot",
                     "remote/subscription/chatgpt"]
    assert [c.path for c in root.children] == ["local", "remote"]
    assert [c.path for c in root.find("remote").children] == ["remote/api_key", "remote/subscription"]
    for leaf in leaves():
        Profile("x", leaf.path)                      # every leaf is a valid profile source
        for f in leaf.fields + leaf.detail_fields:
            assert f.kind in FIELD_KINDS
    openai = find("remote/api_key/openai")
    assert any(f.secret and f.required for f in openai.fields)
    assert find("local/ollama").fields[0].default.startswith("http://127.0.0.1")
    assert find("local/openai_compatible").fields[0].required
    json.dumps(root.to_dict())                       # serialisable for web UIs


def test_catalog_availability_and_llop_extra(monkeypatch):
    llop = find("remote/api_key/llop", locale="en")
    assert llop.extra == "llop" and not llop.available
    import importlib.util
    if importlib.util.find_spec("llmop") is None:
        assert "ntu_easy_llm[llop]" in llop.unavailable_reason
        assert list_llop_profiles() == []
    assert not find("remote/api_key/openai").available       # no HTTP adapters in this pre-release
    assert find("remote/api_key/openai", locale="en").unavailable_reason == "no adapter for this source in this version"
    assert catalog(locale="en").find("local").label.startswith("Local")
    assert find("nope") is None


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #

def _fill(s: ChatSession) -> None:
    s.add({"role": "system", "content": "sys"})
    s.add({"role": "user", "content": "q1"})
    s.add(Message("assistant", "a1", meta={"profile": "main", "usage": {"input_tokens": 3}}))
    s.add_event(Switched("main", to_id="home", reason=ErrorKind.QUOTA_EXHAUSTED))
    s.add(Message("user", "q2", images=[ImagePart("image/png", PNG)]))
    s.add({"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "f", "arguments": {}}]})
    s.add({"role": "tool", "tool_call_id": "c1", "name": "f", "content": "ok"})
    s.add({"role": "assistant", "content": "a2"})


@pytest.mark.parametrize("make_store", [lambda tmp: MemorySessionStore(), lambda tmp: JsonlSessionStore(tmp)])
def test_store_round_trip(tmp_path, make_store):
    store = make_store(tmp_path)
    assert isinstance(store, SessionStore)
    s = ChatSession("demo-1", meta={"title": "t"}).bind(store)
    _fill(s)
    back = store.load("demo-1")
    assert back is not None and back.store is store
    assert [m.role for m in back.messages] == [m.role for m in s.messages]
    assert back.meta == {"title": "t"}
    assert back.messages[2].meta["profile"] == "main"
    assert back.messages[3].images == [] and back.messages[3].meta["images"][0]["size"] == len(PNG)
    assert back.messages[4].tool_calls[0].name == "f"
    assert back.events[0]["event"] == "switched" and back.events[0]["_after"] == 3
    recs = list(back.to_records())
    assert [r["type"] for r in recs][:5] == ["session", "message", "message", "message", "event"]
    back.add({"role": "user", "content": "q3"})              # bound: appended to the store
    assert len(store.load("demo-1").messages) == 8
    info = store.list()
    assert info[0].id == "demo-1" and info[0].message_count == 8 and info[0].meta == {"title": "t"}
    store.save(back)                                           # full rewrite keeps everything
    assert len(store.load("demo-1").messages) == 8
    store.delete("demo-1")
    assert store.load("demo-1") is None and store.list() == []


def test_jsonl_store_skips_torn_lines_and_rejects_bad_ids(tmp_path):
    store = JsonlSessionStore(tmp_path)
    s = ChatSession("t1").bind(store)
    s.add({"role": "user", "content": "hi"})
    with (tmp_path / "t1.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"v": 1, "type": "message", "role": "assis')       # crash mid-write
    back = store.load("t1")
    assert len(back.messages) == 1 and back.skipped_records == 1
    back.add({"role": "assistant", "content": "after the crash"})   # starts on a fresh line
    again = store.load("t1")
    assert [m.content for m in again.messages] == ["hi", "after the crash"] and again.skipped_records == 1
    for bad in ("../x", "a/b", "", ".hidden", "a..b"):
        with pytest.raises(ValueError):
            store.load(bad)
    assert store.load("missing") is None


def test_unknown_record_types_are_skipped():
    recs = [{"v": 1, "type": "session", "id": "x"}, {"v": 9, "type": "future-thing"},
            {"v": 1, "type": "message", "role": "user", "content": "hi"}]
    s = ChatSession.from_records(recs)
    assert s.id == "x" and len(s.messages) == 1


def test_window_keeps_system_and_whole_turns():
    s = ChatSession()
    _fill(s)
    assert s.window(None) == s.messages
    one = lambda m: 10                                         # noqa: E731
    w = s.window(45, estimate=one)                             # system + last turn (4 msgs) = 50 > 45
    assert [m.content for m in w] == ["sys", "q2", "", "ok", "a2"]
    w = s.window(1000, estimate=one)
    assert len(w) == len(s.messages)
    w = s.window(50, estimate=one)
    assert w[0].role == "system" and w[1].content == "q2"
    w = s.window(70, estimate=one)
    assert [m.content for m in w][:3] == ["sys", "q1", "a1"]


def test_session_add_is_thread_safe():
    store = MemorySessionStore()
    s = ChatSession("conc").bind(store)
    threads = [threading.Thread(target=lambda i=i: [s.add({"role": "user", "content": f"{i}-{j}"})
                                                   for j in range(50)]) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(s.messages) == 200 and len(store.load("conc").messages) == 200
