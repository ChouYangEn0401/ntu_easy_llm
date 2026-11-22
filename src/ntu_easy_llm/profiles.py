"""Profiles: named LLM settings loaded from JSON / YAML / a dict (added in 0.6).

A *profile* is one way to reach one model: which source (a path in the
catalog tree, e.g. ``"remote/api_key/openai"``), which model, where, and *a
reference to* the key. A *chain* is an ordered list of profiles with an
optional waterfall policy (:mod:`ntu_easy_llm.waterfall`).

Secrets never live in the config
--------------------------------
A config only names where the key is:

- ``api_key_env: SOME_ENV_VAR``  → read from the environment when needed, or
- ``secret: "<scheme>:<ref>"``   → ``env:NAME`` is built in; any other scheme
  (``vault:main``, ``keyring:x``…) is handed to the caller's ``resolve_secret``.

A literal key anywhere in the config — a field called ``api_key`` / ``token`` /
``password``…, or a value that looks like a key — raises :class:`ConfigError`
(the message names the field, never the value). This holds for files *and*
dicts. Code that already holds a key passes ``resolve_secret=`` instead.

Format (``version: 1``; JSON and YAML are the same schema)::

    {"version": 1,
     "default": "chat",
     "profiles": {
       "home":   {"source": "local/lmstudio", "base_url": "http://127.0.0.1:1234/v1",
                  "model": "example-model", "options": {"context_size": 8192}},
       "main":   {"source": "remote/api_key/openai", "api_key_env": "EXAMPLE_OPENAI_KEY",
                  "model": "example-model"},
       "relay":  {"source": "remote/api_key/llop", "llop_profile": "default", "model": "any"}},
     "chains": {"chat": {"members": ["main", "home"], "waterfall": {"mode": "auto"}}}}

Profile keys: ``source`` (required), ``model``, ``label``, ``base_url`` or
``base_url_env``, ``api_key_env`` or ``secret``, ``options``. Any other key
(``llop_profile``, ``transport``, ``effort``…) is a source-specific option and
is merged into ``options``. Keys starting with ``_`` are comments and ignored.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .errors import ConfigError
from .waterfall import WaterfallPolicy

__all__ = [
    "Profile", "ProfileSet", "ChainSpec", "SecretRef",
    "load", "make_secret_resolver", "looks_like_secret",
    "SOURCE_PATTERN", "CONFIG_VERSION", "ConfigError",
]

CONFIG_VERSION = 1

#: ``local/<provider>`` or ``remote/api_key/<provider>`` or ``remote/subscription/<provider>``.
SOURCE_PATTERN = re.compile(r"^(local/[a-z0-9_]+|remote/(api_key|subscription)/[a-z0-9_]+)$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_REF_RE = re.compile(r"^([a-z][a-z0-9_+-]{0,31}):(\S{1,256})$")

_FORBIDDEN_FIELDS = frozenset({
    "api_key", "apikey", "key", "token", "access_token", "refresh_token", "auth_token", "bearer",
    "password", "passwd", "pass", "secret_key", "client_secret", "private_key", "authorization",
})
_SECRET_PATTERNS = tuple(re.compile(p) for p in (
    r"\bsk-[A-Za-z0-9_\-]{16,}",           # OpenAI-style and many gateways (sk-..., sk-proj-..., sk-ant-...)
    r"\bAIza[0-9A-Za-z_\-]{30,}",          # Google API keys
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",       # GitHub tokens
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",     # Slack
    r"\bAKIA[0-9A-Z]{16}\b",               # AWS access key id
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}",
))

_CORE_KEYS = ("source", "model", "label", "base_url", "base_url_env", "api_key_env", "secret", "options")


def looks_like_secret(value: str) -> bool:
    """True when ``value`` looks like an API key / token / private key (heuristic)."""
    return isinstance(value, str) and any(p.search(value) for p in _SECRET_PATTERNS)


# =============================================================================
# Secret references
# =============================================================================

@dataclass(frozen=True)
class SecretRef:
    """A pointer to a secret: ``env:NAME`` or ``<scheme>:<ref>`` for the caller's resolver."""

    ref: str

    def __post_init__(self) -> None:
        m = _REF_RE.match(str(self.ref or ""))
        if not m or looks_like_secret(self.ref):
            raise ConfigError("secret must be a reference like 'env:NAME' or '<scheme>:<name>'")
        if m.group(1) == "env" and not _ENV_RE.match(m.group(2)):
            raise ConfigError("secret 'env:' reference needs a valid environment variable name")

    @property
    def scheme(self) -> str:
        return self.ref.split(":", 1)[0]

    @property
    def name(self) -> str:
        return self.ref.split(":", 1)[1]

    @classmethod
    def env(cls, name: str) -> "SecretRef":
        return cls(f"env:{name}")

    def __str__(self) -> str:
        return self.ref


SecretResolver = Callable[["SecretRef | str"], str]


def make_secret_resolver(*, environ: Mapping[str, str] | None = None,
                         resolve_secret: Callable[[str], str | None] | None = None) -> SecretResolver:
    """Build the function adapters call to turn a :class:`SecretRef` into the secret.

    ``env:NAME`` reads ``environ`` (default ``os.environ``) at call time; other
    schemes go to ``resolve_secret(ref_string)``. Missing → :class:`ConfigError`
    naming the reference (never a value).
    """

    def resolve(ref: SecretRef | str) -> str:
        r = ref if isinstance(ref, SecretRef) else SecretRef(str(ref))
        if r.scheme == "env":
            env = os.environ if environ is None else environ
            value = env.get(r.name)
            if not value:
                raise ConfigError(f"environment variable {r.name} is not set")
            return str(value)
        if resolve_secret is None:
            raise ConfigError(f"no resolver for secret scheme {r.scheme!r} (pass resolve_secret=)")
        value = resolve_secret(r.ref)
        if not value:
            raise ConfigError(f"secret {r.ref!r} could not be resolved")
        return str(value)

    return resolve


# =============================================================================
# Profile / chain
# =============================================================================

def _freeze(d: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(d or {}))


@dataclass(frozen=True)
class Profile:
    """One configured way to reach one model. Immutable; use :meth:`replace` for variants."""

    id: str
    source: str
    model: str = ""
    base_url: str | None = None
    secret: SecretRef | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    label: str = ""
    base_url_env: str = ""          # where base_url came from (kept for error messages)

    def __post_init__(self) -> None:
        if not _ID_RE.match(str(self.id or "")):
            raise ConfigError(f"profile id {self.id!r}: use letters, digits, '_', '.', '-' (max 64)")
        if not SOURCE_PATTERN.match(str(self.source or "")):
            raise ConfigError(f"profile {self.id}: source {self.source!r} is not a catalog path "
                              "(local/<p>, remote/api_key/<p>, remote/subscription/<p>)")
        if isinstance(self.secret, str):
            object.__setattr__(self, "secret", SecretRef(self.secret))
        if self.base_url is not None:
            object.__setattr__(self, "base_url", _check_url(self.base_url, f"profiles.{self.id}.base_url"))
        object.__setattr__(self, "options", _freeze(self.options))

    @property
    def family(self) -> str:
        """``"local"`` or ``"remote"``."""
        return self.source.split("/", 1)[0]

    @property
    def auth(self) -> str:
        """``"api_key"`` / ``"subscription"`` for remote sources, ``""`` for local."""
        parts = self.source.split("/")
        return parts[1] if len(parts) == 3 else ""

    @property
    def provider(self) -> str:
        """Last path segment, e.g. ``"openai"``."""
        return self.source.rsplit("/", 1)[-1]

    def option(self, name: str, default: Any = None) -> Any:
        return self.options.get(name, default)

    def replace(self, **changes: Any) -> "Profile":
        return replace(self, **changes)

    def to_dict(self) -> dict:
        """Config-file shape (round-trips through :func:`load`)."""
        d: dict[str, Any] = {"source": self.source}
        if self.model:
            d["model"] = self.model
        if self.label:
            d["label"] = self.label
        if self.base_url_env:
            d["base_url_env"] = self.base_url_env
        elif self.base_url:
            d["base_url"] = self.base_url
        if self.secret is not None:
            if self.secret.scheme == "env":
                d["api_key_env"] = self.secret.name
            else:
                d["secret"] = self.secret.ref
        if self.options:
            d["options"] = dict(self.options)
        return d


@dataclass(frozen=True)
class ChainSpec:
    """An ordered list of profile ids with a waterfall policy (default: off)."""

    id: str
    members: tuple[str, ...]
    waterfall: WaterfallPolicy = field(default_factory=WaterfallPolicy)
    label: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"members": list(self.members)}
        if self.waterfall.enabled or self.waterfall != WaterfallPolicy():
            d["waterfall"] = self.waterfall.to_dict()
        if self.label:
            d["label"] = self.label
        return d


@dataclass(frozen=True)
class ProfileSet:
    """Everything one config file defines. Look things up by id; build adapters with :meth:`adapter`."""

    profiles: Mapping[str, Profile]
    chains: Mapping[str, ChainSpec] = field(default_factory=dict)
    default: str = ""
    environ: Mapping[str, str] | None = field(default=None, repr=False, compare=False)
    resolve_secret: Callable[[str], str | None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", _freeze(self.profiles))
        object.__setattr__(self, "chains", _freeze(self.chains))

    def names(self) -> list[str]:
        """Profile ids then chain ids, in file order."""
        return list(self.profiles) + list(self.chains)

    def get(self, profile_id: str) -> Profile:
        try:
            return self.profiles[profile_id]
        except KeyError:
            raise ConfigError(f"no profile named {profile_id!r}") from None

    def chain(self, chain_id: str) -> ChainSpec:
        try:
            return self.chains[chain_id]
        except KeyError:
            raise ConfigError(f"no chain named {chain_id!r}") from None

    def resolve(self, name: str | None = None) -> tuple[list[Profile], WaterfallPolicy]:
        """A profile or chain id (``None`` → ``default``) → (members in order, policy).

        A single profile gives ``([profile], WaterfallPolicy())`` — waterfall off.
        """
        name = name or self.default
        if not name:
            raise ConfigError("no name given and the config has no default")
        if name in self.chains:
            spec = self.chains[name]
            return [self.profiles[m] for m in spec.members], spec.waterfall
        return [self.get(name)], WaterfallPolicy()

    def secret_resolver(self) -> SecretResolver:
        return make_secret_resolver(environ=self.environ, resolve_secret=self.resolve_secret)

    def adapter(self, name: str | None = None, *, http: Any = None) -> Any:
        """The adapter for one *profile* (``None`` → default, which must be a profile).

        Chains need the router (a later 0.6 pre-release); asking for one here raises ConfigError.
        """
        name = name or self.default
        if name in self.chains:
            raise ConfigError(f"{name!r} is a chain; chains are run by the router, not a single adapter")
        from .adapters.base import get_adapter
        return get_adapter(self.get(name), http=http, environ=self.environ, resolve_secret=self.resolve_secret)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"version": CONFIG_VERSION}
        if self.default:
            d["default"] = self.default
        d["profiles"] = {k: p.to_dict() for k, p in self.profiles.items()}
        if self.chains:
            d["chains"] = {k: c.to_dict() for k, c in self.chains.items()}
        return d


# =============================================================================
# Loading
# =============================================================================

def _check_url(url: Any, where: str) -> str:
    if not isinstance(url, str) or not url.strip() or any(c.isspace() for c in url.strip()):
        raise ConfigError(f"{where}: must be an http(s) URL")
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{where}: must be an http(s) URL")
    if parts.username or parts.password:
        raise ConfigError(f"{where}: credentials in the URL are not allowed; use api_key_env / secret")
    if looks_like_secret(url):
        raise ConfigError(f"{where}: the URL seems to contain a key; use api_key_env / secret")
    return url.rstrip("/")


def _scan_for_secrets(node: Any, path: str) -> None:
    """Reject literal secrets anywhere in the document (field names and values)."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            key = str(k)
            sub = f"{path}.{key}" if path else key
            norm = key.strip().lower().replace("-", "_")
            if norm in _FORBIDDEN_FIELDS and v not in (None, ""):
                raise ConfigError(f"{sub}: a config may not contain the key itself — "
                                  "write api_key_env: <ENV_VAR_NAME> or secret: <scheme>:<name>")
            _scan_for_secrets(v, sub)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _scan_for_secrets(v, f"{path}[{i}]")
    elif isinstance(node, str) and looks_like_secret(node):
        raise ConfigError(f"{path}: this value looks like a key; a config may only name it "
                          "(api_key_env / secret)")


def _read_source(source: str | os.PathLike | Mapping) -> Mapping:
    if isinstance(source, Mapping):
        return source
    if isinstance(source, str) and source.lstrip().startswith("{"):
        text, suffix = source, ".json"
    else:
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as e:
            raise ConfigError(f"cannot read config file {path.name}: {e.strerror or e}") from None
        suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise ConfigError("YAML configs need PyYAML: pip install 'ntu_easy_llm[yaml]'") from None
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ConfigError(f"config is not valid YAML: {e}") from None
    else:
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ConfigError(f"config is not valid JSON: {e}") from None
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise ConfigError("config must be an object at the top level")
    return data


def _parse_profile(pid: str, raw: Any, environ: Mapping[str, str]) -> Profile:
    where = f"profiles.{pid}"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: must be an object")
    raw = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    if "source" not in raw:
        raise ConfigError(f"{where}: missing 'source' (e.g. local/ollama, remote/api_key/openai)")
    options = raw.get("options") or {}
    if not isinstance(options, Mapping):
        raise ConfigError(f"{where}.options: must be an object")
    options = dict(options)
    for k, v in raw.items():
        if k in _CORE_KEYS:
            continue
        if k in options:
            raise ConfigError(f"{where}: {k!r} is given both at the top level and in options")
        options[k] = v
    if raw.get("api_key_env") and raw.get("secret"):
        raise ConfigError(f"{where}: give api_key_env or secret, not both")
    secret: SecretRef | None = None
    if raw.get("api_key_env"):
        name = str(raw["api_key_env"])
        if not _ENV_RE.match(name):
            raise ConfigError(f"{where}.api_key_env: not a valid environment variable name")
        secret = SecretRef.env(name)
    elif raw.get("secret"):
        secret = SecretRef(str(raw["secret"]))
    base_url = raw.get("base_url")
    base_url_env = str(raw.get("base_url_env") or "")
    if base_url and base_url_env:
        raise ConfigError(f"{where}: give base_url or base_url_env, not both")
    if base_url_env:
        if not _ENV_RE.match(base_url_env):
            raise ConfigError(f"{where}.base_url_env: not a valid environment variable name")
        value = environ.get(base_url_env)
        base_url = _check_url(value, f"{where}.base_url_env ({base_url_env})") if value else None
    elif base_url is not None:
        base_url = _check_url(base_url, f"{where}.base_url")
    return Profile(id=pid, source=str(raw["source"]), model=str(raw.get("model") or ""),
                   base_url=base_url or None, secret=secret, options=options,
                   label=str(raw.get("label") or ""), base_url_env=base_url_env)


def _parse_chain(cid: str, raw: Any, profiles: Mapping[str, Profile]) -> ChainSpec:
    where = f"chains.{cid}"
    if not _ID_RE.match(cid):
        raise ConfigError(f"{where}: chain id: use letters, digits, '_', '.', '-' (max 64)")
    if isinstance(raw, (list, tuple)):
        raw = {"members": list(raw)}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: must be an object or a list of profile ids")
    unknown = sorted(k for k in raw if k not in ("members", "waterfall", "label") and not str(k).startswith("_"))
    if unknown:
        raise ConfigError(f"{where}: unknown keys {', '.join(unknown)}")
    members = raw.get("members")
    if not isinstance(members, (list, tuple)) or not members:
        raise ConfigError(f"{where}.members: must be a non-empty list of profile ids")
    members = tuple(str(m) for m in members)
    missing = [m for m in members if m not in profiles]
    if missing:
        raise ConfigError(f"{where}.members: unknown profiles {', '.join(missing)}")
    if len(set(members)) != len(members):
        raise ConfigError(f"{where}.members: a profile is listed twice")
    try:
        policy = WaterfallPolicy.from_dict(raw.get("waterfall"))
    except ConfigError as e:
        raise ConfigError(f"{where}.{e}") from None
    return ChainSpec(id=cid, members=members, waterfall=policy, label=str(raw.get("label") or ""))


def load(source: str | os.PathLike | Mapping, *, environ: Mapping[str, str] | None = None,
         resolve_secret: Callable[[str], str | None] | None = None) -> ProfileSet:
    """Read a config (a dict, a ``.json`` / ``.yaml`` / ``.yml`` path, or JSON text) into a :class:`ProfileSet`.

    ``environ`` (default ``os.environ``) is used for ``base_url_env`` now and
    for ``env:`` secrets later; ``resolve_secret`` handles any other secret
    scheme. Secrets are *not* read here — only when an adapter needs one.
    YAML needs the ``yaml`` extra. Every problem raises :class:`ConfigError`.
    """
    data = _read_source(source)
    _scan_for_secrets(data, "")
    env = os.environ if environ is None else environ
    unknown = sorted(k for k in data if k not in ("version", "default", "profiles", "chains")
                     and not str(k).startswith("_") and k != "$schema")
    if unknown:
        raise ConfigError(f"unknown top-level keys: {', '.join(unknown)}")
    version = data.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ConfigError(f"unsupported config version {version!r} (this library reads version {CONFIG_VERSION})")
    raw_profiles = data.get("profiles") or {}
    if not isinstance(raw_profiles, Mapping):
        raise ConfigError("profiles: must be an object of id → profile")
    profiles = {str(pid): _parse_profile(str(pid), raw, env) for pid, raw in raw_profiles.items()
                if not str(pid).startswith("_")}
    raw_chains = data.get("chains") or {}
    if not isinstance(raw_chains, Mapping):
        raise ConfigError("chains: must be an object of id → chain")
    chains = {str(cid): _parse_chain(str(cid), raw, profiles) for cid, raw in raw_chains.items()
              if not str(cid).startswith("_")}
    clash = sorted(set(profiles) & set(chains))
    if clash:
        raise ConfigError(f"ids used for both a profile and a chain: {', '.join(clash)}")
    default = str(data.get("default") or "")
    if default and default not in profiles and default not in chains:
        raise ConfigError(f"default {default!r} is neither a profile nor a chain")
    if not default and len(profiles) == 1 and not chains:
        default = next(iter(profiles))
    return ProfileSet(profiles=profiles, chains=chains, default=default,
                      environ=environ, resolve_secret=resolve_secret)
