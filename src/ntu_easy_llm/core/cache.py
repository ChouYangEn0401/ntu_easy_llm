"""Persistent prompt/result cache — ask once, reuse forever.

``ResponseCache`` is a small, thread-safe ``key -> answer`` map backed by a JSON
file. Pass it to :func:`ask_chatgpt` / :func:`ask_many` (etc.) and an already
answered question is returned from disk instead of hitting the API again, so
re-runs skip work you've already paid for.

Two ways to key an entry:

* **Automatic** — the cache hashes ``(provider, model, prompt, web_search)``.
  Zero config; identical calls hit the cache.
* **Semantic** — you pass your own ``cache_key`` (e.g. an address string, an
  ``"isi||name"`` id, or ``"|".join(sorted([a, b]))``). Use this when the prompt
  text varies run to run but the *thing you are asking about* is stable. This is
  the "turn what I've asked into a mapping" pattern.

Examples
--------
    >>> from ntu_easy_llm import ask_chatgpt, ResponseCache
    >>> cache = ResponseCache("my_project")
    >>> ask_chatgpt("What is RAG?", cache=cache)         # calls the API, stores it
    >>> ask_chatgpt("What is RAG?", cache=cache)         # served from disk, no API

    # Semantic key — build your own question/answer mapping:
    >>> ask_chatgpt(build_prompt(addr), cache=cache, cache_key=addr)

    # Use it directly as a mapping too:
    >>> key = cache.make_key("chatgpt", "gpt-4.1", "hi")
    >>> key in cache, cache.get(key)
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path


class ResponseCache:
    """A thread-safe, JSON-backed ``str -> str`` cache for LLM answers."""

    DEFAULT_DIR: Path = Path.home() / ".ntu_easy_llm" / "cache"

    def __init__(
        self,
        name: str = "default",
        cache_dir: Path | str | None = None,
        autosave: bool = True,
    ) -> None:
        """Open (or create) a cache file.

        Parameters
        ----------
        name:
            File stem; the cache lives at ``<cache_dir>/<name>.json``.
        cache_dir:
            Directory for the file. Defaults to ``~/.ntu_easy_llm/cache/``.
        autosave:
            When *True* (default), every :meth:`set` flushes to disk immediately
            (crash-safe, resumable). Set *False* for big batches and call
            :meth:`save` yourself — ``ask_many`` always saves once at the end.
        """
        base = Path(cache_dir) if cache_dir else self.DEFAULT_DIR
        self.path: Path = base / f"{name}.json"
        self.autosave = autosave
        self._lock = threading.RLock()
        self._data: dict[str, str] = {}

        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = {str(k): str(v) for k, v in loaded.items()}
            except Exception:  # noqa: BLE001 — a corrupt cache must never crash callers
                self._data = {}

    # ------------------------------------------------------------------ #
    # keying
    # ------------------------------------------------------------------ #

    @staticmethod
    def make_key(
        provider: str,
        model: str,
        prompt: str,
        web_search: bool = False,
    ) -> str:
        """Derive a stable hash key from the call parameters."""
        raw = json.dumps(
            [provider, model, prompt, bool(web_search)],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ #
    # mapping API
    # ------------------------------------------------------------------ #

    def get(self, key: str) -> str | None:
        """Return the cached answer for *key*, or ``None`` if absent."""
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        """Store *value* under *key* (flushes to disk if ``autosave``)."""
        with self._lock:
            self._data[key] = value
            if self.autosave:
                self._flush()

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        """Drop every entry (flushes if ``autosave``)."""
        with self._lock:
            self._data.clear()
            if self.autosave:
                self._flush()

    def save(self) -> Path:
        """Persist the cache to disk and return its path."""
        with self._lock:
            self._flush()
        return self.path

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _flush(self) -> None:
        """Write the whole cache atomically (tmp file + replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def __repr__(self) -> str:
        return f"ResponseCache(path={self.path!s}, entries={len(self)})"
