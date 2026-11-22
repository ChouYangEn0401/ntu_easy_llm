"""The source catalog: the tree a settings UI walks (added in 0.6).

::

    local/                      a server on this machine or the LAN; no account, no billing
      ollama  lmstudio  vllm  llamacpp  openai_compatible
    remote/
      api_key/                  a key from the provider
        openai  anthropic  gemini  openai_compatible  llop
      subscription/             a CLI already signed in on this machine
        claude  github_copilot  chatgpt

Every leaf's ``path`` is exactly the ``source`` of a
:class:`~ntu_easy_llm.profiles.Profile`. ``fields`` are what the connection
step asks for, ``detail_fields`` the optional tuning step. ``available`` says
whether an adapter for it is installed and usable *on this machine* (with the
reason when not), so a UI can grey a choice out instead of hiding it.

Building the catalog never imports a provider SDK or an optional extra.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any, Iterator

__all__ = ["FieldSpec", "SourceNode", "catalog", "find", "leaves", "list_llop_profiles", "FIELD_KINDS"]

#: ``FieldSpec.kind`` values.
#: ``secret`` fields are collected by the UI and stored by the *caller* (vault / environment);
#: the profile then holds only a reference (``api_key_env`` / ``secret``).
FIELD_KINDS: tuple[str, ...] = ("text", "url", "secret", "int", "float", "bool", "choice", "model", "llop_profile")


@dataclass(frozen=True)
class FieldSpec:
    """One input a UI shows. ``name`` is the profile key it fills (``options`` keys for detail fields)."""

    name: str
    kind: str
    required: bool = False
    secret: bool = False
    default: Any = None
    choices: tuple[Any, ...] = ()
    label: str = ""
    help: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "required": self.required, "secret": self.secret,
                "default": self.default, "choices": list(self.choices), "label": self.label, "help": self.help}


@dataclass(frozen=True)
class SourceNode:
    """A node of the catalog tree. Leaves (no ``children``) are sources."""

    path: str
    label: str
    children: tuple["SourceNode", ...] = ()
    fields: tuple[FieldSpec, ...] = ()
    detail_fields: tuple[FieldSpec, ...] = ()
    available: bool = True
    unavailable_reason: str = ""
    description: str = ""
    extra: str = ""                   # pip extra this source needs, e.g. "llop"

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def walk(self) -> Iterator["SourceNode"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, path: str) -> "SourceNode | None":
        path = path.strip("/")
        for n in self.walk():
            if n.path == path:
                return n
        return None

    def leaves(self) -> list["SourceNode"]:
        return [n for n in self.walk() if n.is_leaf and n.path]

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"path": self.path, "label": self.label, "available": self.available}
        if self.unavailable_reason:
            d["unavailable_reason"] = self.unavailable_reason
        if self.description:
            d["description"] = self.description
        if self.extra:
            d["extra"] = self.extra
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        else:
            d["fields"] = [f.to_dict() for f in self.fields]
            d["detail_fields"] = [f.to_dict() for f in self.detail_fields]
        return d


# --------------------------------------------------------------------------- #
# Text (zh-TW / en)
# --------------------------------------------------------------------------- #

_T: dict[str, dict[str, str]] = {
    "zh-TW": {
        "local": "本地（這台或區網的伺服器）", "remote": "遠端服務",
        "remote/api_key": "API 金鑰", "remote/subscription": "訂閱登入（這台已登入的官方 CLI）",
        "local/ollama": "Ollama", "local/lmstudio": "LM Studio", "local/vllm": "vLLM", "local/llamacpp": "llama.cpp",
        "local/openai_compatible": "其他 OpenAI 相容伺服器",
        "remote/api_key/openai": "OpenAI", "remote/api_key/anthropic": "Anthropic（Claude API）",
        "remote/api_key/gemini": "Google Gemini", "remote/api_key/openai_compatible": "其他 OpenAI 相容服務",
        "remote/api_key/llop": "LLOP（本地模型發布服務）",
        "remote/subscription/claude": "Claude（訂閱）", "remote/subscription/github_copilot": "GitHub Copilot（訂閱）",
        "remote/subscription/chatgpt": "ChatGPT（訂閱）",
        "f.base_url": "伺服器網址", "f.model": "模型", "f.secret": "API 金鑰",
        "f.secret.help": "金鑰由你的程式存進保管箱或環境變數；設定檔裡只寫引用（api_key_env 或 secret）。",
        "f.llop_profile": "LLOP 設定檔",
        "f.llop_profile.help": "先在這台的終端機用 LLOP 的 SDK 註冊；這裡只選註冊好的設定檔名稱。",
        "d.max_tokens": "回覆長度上限（token）", "d.temperature": "溫度", "d.timeout": "逾時（秒）",
        "d.context_size": "Context 大小（token）", "d.tools": "工具呼叫", "d.effort": "思考強度",
        "d.transport": "傳輸方式", "d.transport.help": "auto＝有簽章金鑰與釘選就加密，否則明文",
        "d.tools.help": "留空＝自動（看伺服器與模型）",
        "na.adapter": "這個版本還沒有這個來源的 adapter",
        "na.extra": "要先安裝 ntu_easy_llm[{extra}]",
        "na.none": "目前沒有可用的服務",
    },
    "en": {
        "local": "Local (this machine or LAN server)", "remote": "Remote service",
        "remote/api_key": "API key", "remote/subscription": "Subscription (official CLI signed in here)",
        "local/ollama": "Ollama", "local/lmstudio": "LM Studio", "local/vllm": "vLLM", "local/llamacpp": "llama.cpp",
        "local/openai_compatible": "Other OpenAI-compatible server",
        "remote/api_key/openai": "OpenAI", "remote/api_key/anthropic": "Anthropic (Claude API)",
        "remote/api_key/gemini": "Google Gemini", "remote/api_key/openai_compatible": "Other OpenAI-compatible service",
        "remote/api_key/llop": "LLOP (local model relay)",
        "remote/subscription/claude": "Claude (subscription)",
        "remote/subscription/github_copilot": "GitHub Copilot (subscription)",
        "remote/subscription/chatgpt": "ChatGPT (subscription)",
        "f.base_url": "Server URL", "f.model": "Model", "f.secret": "API key",
        "f.secret.help": "Your program stores the key in a vault or environment variable; "
                         "the config holds only a reference (api_key_env or secret).",
        "f.llop_profile": "LLOP profile",
        "f.llop_profile.help": "Enroll with the LLOP SDK in a terminal on this machine first, then pick the profile.",
        "d.max_tokens": "Max reply tokens", "d.temperature": "Temperature", "d.timeout": "Timeout (s)",
        "d.context_size": "Context size (tokens)", "d.tools": "Tool calling", "d.effort": "Effort",
        "d.transport": "Transport", "d.transport.help": "auto = encrypted when a signing key and pin exist, else plain",
        "d.tools.help": "empty = automatic (depends on server and model)",
        "na.adapter": "no adapter for this source in this version",
        "na.extra": "install ntu_easy_llm[{extra}] first",
        "na.none": "nothing available yet",
    },
}

# local provider -> default base_url (None = the user must give one)
_LOCAL_URLS = {
    "ollama": "http://127.0.0.1:11434",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "llamacpp": "http://127.0.0.1:8080/v1",
    "openai_compatible": None,
}
_API_KEY = ("openai", "anthropic", "gemini", "openai_compatible", "llop")
_SUBSCRIPTION = ("claude", "github_copilot", "chatgpt")
_EXTRA_MODULE = {"llop": "llmop"}       # pip extra -> top-level module it installs


def _t(locale: str, key: str, **kw: Any) -> str:
    table = _T.get(locale) or _T["en"]
    return table.get(key, _T["en"].get(key, key)).format(**kw)


def _availability(path: str, extra: str, locale: str) -> tuple[bool, str]:
    if extra:
        module = _EXTRA_MODULE.get(extra, extra)
        if importlib.util.find_spec(module) is None:     # top-level find_spec does not import the module
            return False, _t(locale, "na.extra", extra=extra)
    from .adapters.base import adapter_status, registered_sources
    if path not in registered_sources():
        return False, _t(locale, "na.adapter")
    ok, reason = adapter_status(path)
    return ok, reason


def _details(locale: str, *, local: bool, subscription: bool = False, effort: bool = False,
             transport: bool = False) -> tuple[FieldSpec, ...]:
    out: list[FieldSpec] = []
    if not subscription:
        out.append(FieldSpec("max_tokens", "int", label=_t(locale, "d.max_tokens")))
        out.append(FieldSpec("temperature", "float", label=_t(locale, "d.temperature")))
    out.append(FieldSpec("timeout", "float", label=_t(locale, "d.timeout")))
    if local:
        out.append(FieldSpec("context_size", "int", label=_t(locale, "d.context_size")))
        out.append(FieldSpec("tools", "bool", label=_t(locale, "d.tools"), help=_t(locale, "d.tools.help")))
    if effort:
        out.append(FieldSpec("effort", "choice", choices=("low", "medium", "high"), label=_t(locale, "d.effort")))
    if transport:
        out.append(FieldSpec("transport", "choice", default="auto", choices=("auto", "e2e", "plain"),
                             label=_t(locale, "d.transport"), help=_t(locale, "d.transport.help")))
    return tuple(out)


def _model(locale: str, *, required: bool = True, default: Any = None) -> FieldSpec:
    return FieldSpec("model", "model", required=required, default=default, label=_t(locale, "f.model"))


def _secret(locale: str, *, required: bool) -> FieldSpec:
    return FieldSpec("secret", "secret", required=required, secret=True, label=_t(locale, "f.secret"),
                     help=_t(locale, "f.secret.help"))


def _leaf(path: str, locale: str, fields: tuple[FieldSpec, ...], details: tuple[FieldSpec, ...],
          extra: str = "") -> SourceNode:
    ok, reason = _availability(path, extra, locale)
    return SourceNode(path=path, label=_t(locale, path), fields=fields, detail_fields=details,
                      available=ok, unavailable_reason="" if ok else reason, extra=extra)


def _group(path: str, locale: str, children: list[SourceNode]) -> SourceNode:
    ok = any(c.available for c in children)
    return SourceNode(path=path, label=_t(locale, path), children=tuple(children), available=ok,
                      unavailable_reason="" if ok else _t(locale, "na.none"))


def catalog(*, locale: str = "zh-TW") -> SourceNode:
    """The whole tree (root ``path == ""``). Built fresh each call so availability is current."""
    local_children = []
    for p, url in _LOCAL_URLS.items():
        fields = (FieldSpec("base_url", "url", required=url is None, default=url, label=_t(locale, "f.base_url")),
                  _model(locale))
        if p in ("vllm", "openai_compatible"):
            fields += (_secret(locale, required=False),)
        local_children.append(_leaf(f"local/{p}", locale, fields, _details(locale, local=True)))

    api_children = []
    for p in _API_KEY:
        path = f"remote/api_key/{p}"
        if p == "llop":
            api_children.append(_leaf(path, locale,
                                      (FieldSpec("llop_profile", "llop_profile", required=True, default="default",
                                                 label=_t(locale, "f.llop_profile"),
                                                 help=_t(locale, "f.llop_profile.help")),
                                       _model(locale, required=False, default="any")),
                                      _details(locale, local=False, transport=True), extra="llop"))
            continue
        fields = (_secret(locale, required=True), _model(locale))
        if p == "openai_compatible":
            fields = (FieldSpec("base_url", "url", required=True, label=_t(locale, "f.base_url")),) + fields
        api_children.append(_leaf(path, locale, fields, _details(locale, local=False)))

    sub_children = [
        _leaf(f"remote/subscription/{p}", locale, (_model(locale, required=False),),
              _details(locale, local=False, subscription=True, effort=p in ("claude", "chatgpt")))
        for p in _SUBSCRIPTION
    ]

    remote = _group("remote", locale, [_group("remote/api_key", locale, api_children),
                                       _group("remote/subscription", locale, sub_children)])
    local = _group("local", locale, local_children)
    return SourceNode(path="", label="", children=(local, remote), available=local.available or remote.available)


def find(path: str, *, locale: str = "zh-TW") -> SourceNode | None:
    """The catalog node at ``path`` (e.g. ``"remote/api_key/openai"``), or ``None``."""
    return catalog(locale=locale).find(path)


def leaves(*, locale: str = "zh-TW") -> list[SourceNode]:
    """Every source (leaf) in tree order."""
    return catalog(locale=locale).leaves()


def list_llop_profiles() -> list[str]:
    """Names of the LLOP SDK profiles enrolled on this machine; ``[]`` without the ``llop`` extra.

    Imports the LLOP SDK only when it is installed.
    """
    if importlib.util.find_spec("llmop") is None:
        return []
    try:
        from llmop.sdk import list_profiles  # type: ignore[import-not-found]
    except ImportError:
        return []
    return [str(p) for p in list_profiles()]

