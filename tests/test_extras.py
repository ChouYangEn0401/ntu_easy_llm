"""pyproject optional extras: the login packages and LLOP come from GitHub release wheels by URL (no network)."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from ntu_easy_llm.adapters.subscription import EXTRAS, PACKAGES

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

pytestmark = pytest.mark.skipif(not PYPROJECT.exists(), reason="needs the source checkout")

# extra -> (distribution name, GitHub repo, tag, version)
LOGINS = {
    "claude-login": ("claude-subscription", "ClaudeLogin", "v0.5.1", "0.5.1"),
    "copilot": ("github-copilot-login", "GithubCopilotLogin", "v0.2.0", "0.2.0"),
    "chatgpt": ("chatgpt-login", "ChatGPTLogin", "v0.2.0", "0.2.0"),
}
URL_REQ = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(\[[a-z0-9,_-]+\])? @ (?P<url>\S+)$")
WHEEL_URL = re.compile(r"^https://github\.com/ChouYangEn0401/(?P<repo>[A-Za-z0-9_.-]+)/releases/download/"
                       r"(?P<tag>[^/]+)/(?P<file>(?P<dist>[A-Za-z0-9_]+)-(?P<ver>[^-]+)-py3-none-any\.whl)$")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@pytest.fixture(scope="module")
def extras() -> dict[str, list[str]]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["optional-dependencies"]


def _parse(req: str) -> tuple[str, dict]:
    m = URL_REQ.match(req)
    assert m, f"not a 'name @ url' requirement (never a bare name): {req!r}"
    u = WHEEL_URL.match(m["url"])
    assert u, f"not a GitHub release wheel URL: {m['url']}"
    assert _norm(u["dist"]) == _norm(m["name"]), "wheel file name and requirement name differ"
    return _norm(m["name"]), u.groupdict()


@pytest.mark.parametrize("extra", sorted(LOGINS))
def test_login_extra_pins_release_wheel(extras, extra):
    name, repo, tag, ver = LOGINS[extra]
    reqs = extras[extra]
    assert len(reqs) == 1
    got, u = _parse(reqs[0])
    assert got == name and u["repo"] == repo and u["tag"] == tag and u["ver"] == ver


def test_subscriptions_extra_is_all_three(extras):
    assert sorted(extras["subscriptions"]) == sorted(r for e in LOGINS for r in extras[e])


def test_every_url_extra_is_a_release_wheel(extras):
    for extra in ("llop", "subscriptions", *LOGINS):
        for req in extras[extra]:
            _parse(req)


def test_extras_cover_every_subscription_source(extras):
    assert set(EXTRAS.values()) == set(LOGINS)
    for provider, extra in EXTRAS.items():
        assert _norm(LOGINS[extra][0]) == _norm(PACKAGES[provider][1])
