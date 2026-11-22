"""ntu_easy_llm.router: waterfall routing over FakeAdapters (no network)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from ntu_easy_llm import load_profiles
from ntu_easy_llm.adapters import Adapter, Capabilities, FakeAdapter
from ntu_easy_llm.agent import LLMError
from ntu_easy_llm.conversation import ChatSession
from ntu_easy_llm.errors import ConfigError, ErrorKind
from ntu_easy_llm.profiles import Profile
from ntu_easy_llm.router import LLM, FallbackRequired, Router, WaterfallExhausted
from ntu_easy_llm.waterfall import Retrying, Skipped, Switched

Q = ErrorKind.QUOTA_EXHAUSTED
R = ErrorKind.RATE_LIMITED


def fake(pid: str, *script, caps: Capabilities | None = None, label: str = "") -> FakeAdapter:
    return FakeAdapter(list(script), profile=Profile(id=pid, source="local/fake", model=f"{pid}-model", label=label),
                       capabilities=caps)


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(members, mode="auto", **policy):
    waits: list[float] = []
    clock = Clock()
    r = Router(members, waterfall={"mode": mode, **policy} if mode else None,
               sleep=waits.append, clock=clock)
    return r, waits, clock


HI = [{"role": "user", "content": "hi"}]


def test_router_is_adapter_shaped():
    r, _, _ = make([fake("a", "x")], mode=None)
    assert isinstance(r, Adapter)
    assert r.profile.id == "a" and r.members == ("a",)
    assert LLM is Router


def test_auto_switches_on_quota_with_notice_and_session_event():
    a, b = fake("a", Q), fake("b", "from b", label="Backup")
    r, _, _ = make([a, b])
    s = ChatSession(messages=HI)
    res = r.chat(s)
    assert res.text == "from b" and res.profile_id == "b" and res.model == "b-model"
    assert res.switched is True
    assert "已改用 Backup" in res.notice and "額度用完" in res.notice
    assert any(isinstance(e, Switched) and e.to_id == "b" and e.reason is Q for e in res.events)
    # the session saw the switch and got the reply appended; both members got the same messages
    assert [e["event"] for e in s.events] == ["switched"]
    assert s.messages[-1].role == "assistant" and s.messages[-1].meta["profile_id"] == "b"
    assert a.calls[0]["messages"][0].content == b.calls[0]["messages"][0].content == "hi"


def test_429_twice_then_success_does_not_switch():
    a, b = fake("a", R, R, "ok"), fake("b", "never")
    r, waits, _ = make([a, b], retry_429=2)
    res = r.chat(HI)
    assert res.text == "ok" and res.profile_id == "a" and not res.switched and res.notice == ""
    assert len(a.calls) == 3 and b.calls == []
    assert len(waits) == 2 and sum(isinstance(e, Retrying) for e in res.events) == 2


def test_429_three_times_switches():
    a, b = fake("a", R, R, R), fake("b", "ok")
    r, waits, _ = make([a, b], retry_429=2)
    res = r.chat(HI)
    assert res.profile_id == "b" and res.switched and len(a.calls) == 3 and len(waits) == 2


def test_long_retry_after_is_not_waited():
    a, b = fake("a", {"error": "rate_limited", "retry_after": 120}), fake("b", "ok")
    r, waits, _ = make([a, b], max_retry_wait_s=30)
    res = r.chat(HI)
    assert res.profile_id == "b" and waits == [] and len(a.calls) == 1


def test_server_error_does_not_switch():
    a, b = fake("a", ErrorKind.SERVER_ERROR), fake("b", "never")
    r, _, _ = make([a, b])
    with pytest.raises(LLMError) as ei:
        r.chat(HI)
    assert ei.value.kind is ErrorKind.SERVER_ERROR and ei.value.profile_id == "a"
    assert b.calls == []


def test_off_raises_directly_without_retry():
    a, b = fake("a", R), fake("b", "never")
    r, waits, _ = make([a, b], mode=None)
    with pytest.raises(LLMError) as ei:
        r.chat(HI)
    assert ei.value.kind is R and waits == [] and b.calls == []
    a2 = fake("a2", Q)
    r2, _, _ = make([a2, fake("b2", "never")], mode="off")
    with pytest.raises(LLMError):
        r2.chat(HI)


def test_off_success_is_plain_result():
    r, _, _ = make([fake("a", "x"), fake("b", "y")], mode=None)
    res = r.chat(HI)
    assert res.text == "x" and res.profile_id == "a" and res.events == [] and not res.switched


def test_ask_raises_then_resume_continues():
    a, b, c = fake("a", ErrorKind.AUTH_INVALID), fake("b", "from b"), fake("c", "from c")
    r, _, _ = make([a, b, c], mode="ask")
    s = ChatSession(messages=HI)
    with pytest.raises(FallbackRequired) as ei:
        r.chat(s)
    p = ei.value.pending
    assert p.profile_id == "a" and p.reason is ErrorKind.AUTH_INVALID and p.candidates == ("b", "c")
    assert p.session_id == s.id and p.expires_at > 0 and r.pending() == [p]
    assert b.calls == [] and c.calls == []
    res = r.resume(p, use="c")
    assert res.text == "from c" and res.switched and "已改用 c" in res.notice
    assert b.calls == [] and r.pending() == []
    assert [e["event"] for e in s.events] == ["switched"]


def test_ask_resume_none_or_expired_raises_original():
    r, _, clock = make([fake("a", Q, Q), fake("b", "ok", "ok")], mode="ask", ask_ttl_s=60)
    with pytest.raises(FallbackRequired) as ei:
        r.chat(HI)
    with pytest.raises(LLMError) as gave_up:
        r.resume(ei.value.pending, use=None)
    assert gave_up.value.kind is Q and not isinstance(gave_up.value, FallbackRequired)
    # primary is cooling now: ask mode still asks first instead of silently using b
    with pytest.raises(FallbackRequired) as ei2:
        r.chat(HI)
    clock.t += 61
    with pytest.raises(LLMError) as expired:
        r.resume(ei2.value.pending, use="b")
    assert expired.value.kind is Q


def test_ask_resume_rejects_unknown_candidate():
    r, _, _ = make([fake("a", Q), fake("b", "ok")], mode="ask")
    with pytest.raises(FallbackRequired) as ei:
        r.chat(HI)
    with pytest.raises(ConfigError):
        r.resume(ei.value.pending, use="zzz")


def test_cooling_member_is_skipped_then_comes_back():
    a, b = fake("a", Q, "a again"), fake("b", "b1", "b2")
    r, _, clock = make([a, b], cooldown_s=900)
    assert r.chat(HI).profile_id == "b"
    st = {m.profile_id: m for m in r.status()}
    assert st["a"].cooling_until == clock.t + 900 and st["a"].last_error is Q and st["b"].cooling_until is None
    res = r.chat(HI)
    assert res.profile_id == "b" and res.switched and len(a.calls) == 1
    assert any(isinstance(e, Skipped) and e.profile_id == "a" for e in res.events)
    clock.t += 901
    res = r.chat(HI)
    assert res.profile_id == "a" and res.text == "a again" and not res.switched
    assert r.status()[0].cooling_until is None


def test_retry_after_sets_cooldown():
    r, _, clock = make([fake("a", {"error": "quota_exhausted", "retry_after": 50}), fake("b", "ok")])
    r.chat(HI)
    assert r.status()[0].cooling_until == clock.t + 50
    r.reset()
    assert r.status()[0].cooling_until is None


def test_trust_never_switches():
    a, b = fake("a", ErrorKind.TRUST), fake("b", "never")
    r, _, _ = make([a, b])
    with pytest.raises(LLMError) as ei:
        r.chat(HI)
    assert ei.value.kind is ErrorKind.TRUST and b.calls == []
    with pytest.raises(ConfigError):
        Router([a, b], waterfall={"mode": "auto", "triggers": ["trust"]})


def test_link_changed_is_raised_without_retry():
    a, b = fake("a", ErrorKind.LINK_CHANGED, "never"), fake("b", "never")
    r, waits, _ = make([a, b])
    with pytest.raises(LLMError) as ei:
        r.chat(HI)
    assert ei.value.kind is ErrorKind.LINK_CHANGED and len(a.calls) == 1 and waits == [] and b.calls == []


def test_exhausted_when_everyone_fails():
    r, _, _ = make([fake("a", Q), fake("b", ErrorKind.AUTH_INVALID)])
    with pytest.raises(WaterfallExhausted) as ei:
        r.chat(HI)
    assert [p for p, _ in ei.value.errors] == ["a", "b"] and ei.value.kind is ErrorKind.AUTH_INVALID


def test_member_without_tools_is_not_a_candidate():
    a = fake("a", Q)
    no_tools = fake("b", "never", caps=Capabilities(tools=False))
    c = fake("c", "ok")
    r, _, _ = make([a, no_tools, c])
    res = r.chat(HI, tools=[{"name": "f", "parameters": {"type": "object", "properties": {}}}])
    assert res.profile_id == "c" and no_tools.calls == []


def test_custom_triggers_add_server_error():
    r, _, _ = make([fake("a", ErrorKind.SERVER_ERROR), fake("b", "ok")],
                   triggers=["quota_exhausted", "server_error"])
    assert r.chat(HI).profile_id == "b"


def test_on_event_errors_are_ignored():
    seen = []

    def boom(ev):
        seen.append(ev.name)
        raise RuntimeError("observer broke")

    r = Router([fake("a", Q), fake("b", "ok")], waterfall={"mode": "auto"}, on_event=boom)
    assert r.chat(HI).text == "ok" and "switched" in seen


def test_profileset_router_from_config():
    ps = load_profiles({
        "default": "chat",
        "profiles": {
            "p1": {"source": "local/fake", "options": {"script": [{"error": "quota_exhausted"}]}},
            "p2": {"source": "local/fake", "options": {"script": ["second"]}},
        },
        "chains": {"chat": {"members": ["p1", "p2"], "waterfall": {"mode": "auto"}},
                   "plain": ["p1", "p2"]},
    })
    r = ps.router()
    assert r.chain_id == "chat" and r.members == ("p1", "p2") and r.policy.mode == "auto"
    res = r.chat(HI)
    assert res.text == "second" and res.profile_id == "p2" and res.switched
    single = ps.router("p2")
    assert single.members == ("p2",) and not single.policy.enabled and single.chain_id == ""
    assert ps.router("plain").policy.mode == "off"
    with pytest.raises(ConfigError):
        ps.adapter("chat")


def test_router_rejects_bad_members():
    with pytest.raises(ConfigError):
        Router([])
    with pytest.raises(ConfigError):
        Router(["needs-profiles"])
    with pytest.raises(ConfigError):
        Router([fake("a"), fake("a")])


def test_import_router_loads_no_sdk():
    code = ("import sys, ntu_easy_llm, ntu_easy_llm.router; "
            "bad = {'openai','anthropic','google.genai','llmop','claude_subscription',"
            "'github_copilot_login','chatgpt_login'} & set(sys.modules); assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True)
