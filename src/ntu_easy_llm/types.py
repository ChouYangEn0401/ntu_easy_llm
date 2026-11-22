"""Provider-neutral message types (added in 0.6).

:class:`Message` and :class:`ImagePart` are the neutral conversation format
every 0.6 adapter accepts. Plain dicts in the 0.5 ``agent`` format are still
accepted everywhere (``{"role": ..., "content": ...}``); :func:`as_message`
converts either into a :class:`Message`.

``Tool``, ``ToolCall``, ``Usage`` and ``ChatReply`` are the very same classes as
in :mod:`ntu_easy_llm.agent` (re-exported, not copies), so replies from the old
``agent.chat`` and from the new adapters are interchangeable.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

from .agent import ChatReply, Tool, ToolCall, Usage, _call, _get

__all__ = [
    "Message", "ImagePart", "Role", "ROLES",
    "Tool", "ToolCall", "Usage", "ChatReply",
    "FINISH_VALUES", "normalize_finish",
    "as_message", "as_messages",
]

Role = Literal["system", "user", "assistant", "tool"]
ROLES: tuple[str, ...] = ("system", "user", "assistant", "tool")

#: Values of :attr:`ChatReply.finish`.
#: ``"thinking"`` = the token limit was hit before any visible text (reasoning models).
FINISH_VALUES: tuple[str, ...] = ("", "stop", "length", "thinking", "tool_calls")

_STOP = {"stop", "end_turn", "stop_sequence", "eos", "complete", "completed"}
_LENGTH = {"length", "max_tokens", "max_output_tokens", "model_length"}
_TOOLS = {"tool_calls", "tool_use", "function_call"}


def normalize_finish(raw: str | None, *, text: str = "", has_tool_calls: bool = False) -> str:
    """Provider ``finish_reason`` / ``stop_reason`` → one of :data:`FINISH_VALUES`.

    Unknown or missing reasons give ``""``.
    """
    r = str(raw or "").strip().lower()
    if has_tool_calls or r in _TOOLS:
        return "tool_calls"
    if r in _LENGTH:
        return "length" if text.strip() else "thinking"
    if r in _STOP:
        return "stop"
    return ""


@dataclass(frozen=True, repr=False)
class ImagePart:
    """An image attached to a user message. ``repr`` never prints the bytes."""

    mime: str
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("ImagePart.data must be bytes")
        if not str(self.mime).lower().startswith("image/"):
            raise ValueError(f"ImagePart.mime must be an image/* type, got {self.mime!r}")
        object.__setattr__(self, "data", bytes(self.data))
        object.__setattr__(self, "mime", str(self.mime).lower())

    def __repr__(self) -> str:
        return f"ImagePart(mime={self.mime!r}, size={self.size})"

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def to_base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    def to_data_url(self) -> str:
        return f"data:{self.mime};base64,{self.to_base64()}"

    def describe(self) -> dict:
        """What a session log stores instead of the bytes."""
        return {"mime": self.mime, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_base64(cls, mime: str, b64: str) -> "ImagePart":
        try:
            return cls(mime, base64.b64decode(b64, validate=True))
        except (binascii.Error, ValueError) as e:
            raise ValueError("image data is not valid base64") from e

    @classmethod
    def from_data_url(cls, url: str) -> "ImagePart":
        """``data:image/png;base64,...`` → ImagePart (remote http(s) URLs are not fetched)."""
        if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
            raise ValueError("only data:<mime>;base64,... image URLs are supported")
        head, b64 = url[5:].split(";base64,", 1)
        return cls.from_base64(head, b64)


@dataclass
class Message:
    """One turn of a conversation.

    - ``images`` only on ``user`` messages.
    - ``tool_calls`` only on ``assistant`` messages; ``tool_call_id`` / ``name``
      on ``tool`` results.
    - ``meta`` is never sent to a model (profile id, model, usage, timestamps…).
    """

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    images: list[ImagePart] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}; use one of {', '.join(ROLES)}")
        self.content = "" if self.content is None else str(self.content)
        self.tool_calls = [_call(c) for c in (self.tool_calls or [])]
        self.images = list(self.images or [])
        for im in self.images:
            if not isinstance(im, ImagePart):
                raise TypeError("Message.images must contain ImagePart objects")
        if self.images and self.role != "user":
            raise ValueError("only user messages may carry images")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only assistant messages may carry tool_calls")
        self.meta = dict(self.meta or {})

    def to_dict(self) -> dict:
        """The 0.5 ``agent`` dict shape (images as ImagePart objects, meta omitted)."""
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [{"id": c.id, "name": c.name, "arguments": dict(c.arguments)} for c in self.tool_calls]
        if self.role == "tool":
            d["tool_call_id"] = self.tool_call_id
            if self.name:
                d["name"] = self.name
        if self.images:
            d["images"] = list(self.images)
        return d


def _parts_to_text_and_images(parts: Iterable[Any]) -> tuple[str, list[ImagePart]]:
    """OpenAI / Anthropic style content parts → (text, images)."""
    texts: list[str] = []
    images: list[ImagePart] = []
    for p in parts:
        if isinstance(p, str):
            texts.append(p)
            continue
        if isinstance(p, ImagePart):
            images.append(p)
            continue
        if not isinstance(p, Mapping):
            raise ValueError(f"unsupported content part: {type(p).__name__}")
        t = p.get("type")
        if t in ("text", "input_text"):
            texts.append(str(p.get("text") or ""))
        elif t == "image_url":
            url = p.get("image_url")
            url = url.get("url") if isinstance(url, Mapping) else url
            images.append(ImagePart.from_data_url(str(url or "")))
        elif t == "image" and isinstance(p.get("source"), Mapping):
            src = p["source"]
            if src.get("type") != "base64":
                raise ValueError("only base64 image sources are supported")
            images.append(ImagePart.from_base64(str(src.get("media_type") or ""), str(src.get("data") or "")))
        else:
            raise ValueError(f"unsupported content part type: {t!r}")
    return "".join(texts), images


def as_message(m: Any) -> Message:
    """A :class:`Message`, a 0.5-style dict, or an object with the same attributes → :class:`Message`.

    A list ``content`` (OpenAI ``text`` / ``image_url`` data-URL parts, or
    Anthropic ``text`` / base64 ``image`` blocks) is split into text and
    :class:`ImagePart` s. Anything else in a list raises ``ValueError`` instead
    of being dropped silently.
    """
    if isinstance(m, Message):
        return m
    role = _get(m, "role", "user") or "user"
    content = _get(m, "content", "")
    images = list(_get(m, "images", None) or [])
    if isinstance(content, (list, tuple)):
        content, extra = _parts_to_text_and_images(content)
        images += extra
    return Message(role=role, content=content or "", tool_calls=list(_get(m, "tool_calls", None) or []),
                   tool_call_id=str(_get(m, "tool_call_id", "") or ""), name=str(_get(m, "name", "") or ""),
                   images=images, meta=dict(_get(m, "meta", None) or {}))


def as_messages(messages: Iterable[Any]) -> list[Message]:
    """Convert a whole conversation with :func:`as_message`."""
    return [as_message(m) for m in messages]
