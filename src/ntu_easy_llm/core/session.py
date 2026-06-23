"""Session management for multi-turn LLM conversations.

``LLMSession`` lets you maintain a full conversation history across multiple
``ask()`` calls and optionally persist it to disk for later resumption.

Quick start
-----------
    >>> from ntu_easy_llm import LLMSession

    # --- new conversation ---
    >>> session = LLMSession("chatgpt")
    >>> print(session.ask("Hello! My name is Alice."))
    >>> print(session.ask("What is my name?"))   # still knows "Alice"

    # --- save and resume later ---
    >>> session.save()
    >>> session2 = LLMSession.resume(session.session_id)
    >>> print(session2.ask("Recap our conversation."))

    # --- non-blocking with end_callback ---
    >>> session.ask_async(
    ...     "Summarise today's AI news",
    ...     end_callback=lambda text: open("result.txt", "w").write(text),
    ... )
"""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from .utils import _call_anthropic, _call_chatgpt, _call_gemini, _resolve_api_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: dict[str, str] = {
    "CHATGPT": "gpt-4.1",
    "GEMINI": "gemini-2.5-flash",
    "ANTHROPIC": "claude-haiku-4-5-20251001",
}

ProviderStr = Literal["chatgpt", "gemini", "anthropic", "CHATGPT", "GEMINI", "ANTHROPIC"]


# ---------------------------------------------------------------------------
# LLMSession
# ---------------------------------------------------------------------------

class LLMSession:
    """Stateful multi-turn conversation session.

    Each ``ask()`` call appends both the user message and the assistant
    response to an internal history list that is sent along with every
    subsequent request, giving the model full context of the conversation.

    Concurrency
    -----------
    A :class:`threading.RLock` serialises concurrent ``ask()`` calls on the
    same session.  This is intentional: the ordering of messages in a
    conversation matters.  If you need to run *independent* queries in
    parallel, create separate ``LLMSession`` instances.

    Attributes
    ----------
    session_id : str
        Unique identifier (UUID4).  Use this to resume the session later.
    provider : str
        Normalised provider name: ``"CHATGPT"``, ``"GEMINI"``, or
        ``"ANTHROPIC"``.
    model_name : str
        Model used by this session.
    web_search : bool
        Whether web search is enabled (ChatGPT and Gemini only).
    history : list[dict]
        Full message history in ``{"role": ..., "content": ...}`` format.
    created_at : str
        ISO-8601 timestamp of when the session was created.
    """

    DEFAULT_SESSIONS_DIR: Path = Path.home() / ".ntu_easy_llm" / "sessions"

    def __init__(
        self,
        provider: ProviderStr,
        model_name: str | None = None,
        api_key: str | None = None,
        web_search: bool = False,
        password: str | None = None,
        session_id: str | None = None,
        max_workers: int = 4,
    ) -> None:
        """Create a new session.

        Parameters
        ----------
        provider:
            ``"chatgpt"``, ``"gemini"``, or ``"anthropic"`` (case-insensitive).
        model_name:
            Model to use.  Falls back to the provider's recommended default.
        api_key:
            API key.  If *None*, the key is loaded from the .env file.
        web_search:
            Enable web search grounding (ChatGPT and Gemini only).
        password:
            AES password to decrypt an encrypted key stored in .env.
        session_id:
            Supply an existing UUID to re-use it (e.g. after loading from dict).
            Normally left as *None* so a fresh UUID is generated.
        max_workers:
            Thread-pool size for ``ask_async()``.
        """
        normalised = provider.upper()
        if normalised not in ("CHATGPT", "GEMINI", "ANTHROPIC"):
            raise ValueError(
                f"Unknown provider: {provider!r}. "
                "Use 'chatgpt', 'gemini', or 'anthropic'."
            )

        self._provider: str = normalised
        self.session_id: str = session_id or str(uuid.uuid4())
        self.model_name: str = model_name or _DEFAULT_MODELS[normalised]
        self.web_search: bool = web_search
        self.history: list[dict] = []
        self.created_at: str = datetime.now().isoformat()

        self._api_key_override: str | None = api_key
        self._password: str | None = password
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def resume(
        cls,
        session_id: str,
        sessions_dir: Path | str | None = None,
        api_key: str | None = None,
        password: str | None = None,
    ) -> "LLMSession":
        """Load and resume a session that was previously saved with :meth:`save`.

        Parameters
        ----------
        session_id:
            The UUID of the session to resume.
        sessions_dir:
            Directory where the JSON file lives.  Defaults to
            ``~/.ntu_easy_llm/sessions/``.
        api_key:
            Override the API key (useful when resuming on another machine).
        password:
            AES decryption password for the API key.

        Raises
        ------
        FileNotFoundError
            If no JSON file for *session_id* is found in *sessions_dir*.
        """
        dir_ = Path(sessions_dir) if sessions_dir else cls.DEFAULT_SESSIONS_DIR
        path = dir_ / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Session '{session_id}' not found in {dir_}.\n"
                "Make sure the session was saved with session.save()."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        session = cls._from_dict(data)
        session._api_key_override = api_key
        session._password = password
        return session

    # ------------------------------------------------------------------
    # Core ask methods
    # ------------------------------------------------------------------

    def ask(self, prompt: str) -> str:
        """Send a message and receive a response (blocking).

        The user message and the model's reply are both appended to
        :attr:`history` before this method returns.

        Parameters
        ----------
        prompt:
            Your message to the model.

        Returns
        -------
        str
            The model's response.
        """
        with self._lock:
            self.history.append({"role": "user", "content": prompt})
            response = self._dispatch(self.history)
            self.history.append({"role": "assistant", "content": response})
        return response

    def ask_async(
        self,
        prompt: str,
        end_callback: Callable[[str], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> "Future[str]":
        """Send a message without blocking the calling thread.

        The underlying :meth:`ask` call runs on a background thread from the
        session's private thread pool.  Provide *end_callback* to handle the
        response or subscribe to events (e.g. save to file, trigger next step).

        Parameters
        ----------
        prompt:
            Your message to the model.
        end_callback:
            ``end_callback(response: str)`` — invoked on the worker thread when
            the response is ready.
        on_error:
            ``on_error(exc: Exception)`` — invoked on the worker thread when
            the request fails.

        Returns
        -------
        Future[str]
            A :class:`~concurrent.futures.Future`.  Call ``.result()`` to
            block until the response arrives, or ignore it entirely if you
            only care about the end_callback.

        Examples
        --------
        Fire-and-forget with a file-write end_callback::

            session.ask_async(
                "Write a haiku about Python.",
                end_callback=lambda text: Path("haiku.txt").write_text(text),
            )

        Chained pipeline::

            def step2(text):
                session.ask_async(
                    f"Translate this to French: {text}",
                    end_callback=print,
                )

            session.ask_async("Describe the Eiffel Tower.", end_callback=step2)
        """
        future: Future[str] = self._executor.submit(self.ask, prompt)

        if end_callback or on_error:
            def _done(f: Future) -> None:
                exc = f.exception()
                if exc:
                    if on_error:
                        on_error(exc)
                else:
                    if end_callback:
                        end_callback(f.result())
            future.add_done_callback(_done)

        return future

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def compress_history(self, keep_last: int = 4) -> None:
        """Summarise old conversation turns to reduce token consumption.

        The oldest messages (everything except the most recent *keep_last*
        exchange pairs) are replaced by a single summary generated by the
        same LLM provider.  The recent messages are kept verbatim so the
        model retains the immediate context.

        Parameters
        ----------
        keep_last:
            Number of recent *exchange pairs* (user + assistant) to keep
            verbatim.  One pair = 2 messages, so ``keep_last=4`` preserves
            the last 8 messages.

        Notes
        -----
        - The summarisation call does **not** appear in :attr:`history`.
        - A ``"system"`` message holding the summary is prepended so the
          model knows what happened earlier.
        - Calls this method again as the conversation grows to keep history
          from ballooning.

        Example
        -------
            >>> session.compress_history(keep_last=3)
        """
        with self._lock:
            preserve_count = keep_last * 2   # user + assistant per pair
            if len(self.history) <= preserve_count:
                return

            old_messages = self.history[:-preserve_count]
            recent_messages = self.history[-preserve_count:]

            transcript = "\n".join(
                f"{m['role'].upper()}: {m['content']}"
                for m in old_messages
                if m["role"] != "system"
            )
            summary_prompt = (
                "Summarise the following conversation concisely in 3–5 sentences. "
                "Preserve all important context, names, decisions, and facts:\n\n"
                + transcript
            )

            summary = self._dispatch([{"role": "user", "content": summary_prompt}])

            self.history = [
                {"role": "system", "content": f"[Previous conversation summary]: {summary}"}
            ] + recent_messages

    def clear_history(self) -> None:
        """Erase all conversation history (start fresh without a new session)."""
        with self._lock:
            self.history.clear()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, sessions_dir: Path | str | None = None) -> Path:
        """Persist the session to a JSON file on disk.

        Parameters
        ----------
        sessions_dir:
            Directory to write the file into.  Defaults to
            ``~/.ntu_easy_llm/sessions/``.  The directory is created
            automatically if it does not exist.

        Returns
        -------
        Path
            Absolute path to the saved ``.json`` file.

        Example
        -------
            >>> path = session.save()
            >>> print(f"Saved to {path}")
        """
        dir_ = Path(sessions_dir) if sessions_dir else self.DEFAULT_SESSIONS_DIR
        dir_.mkdir(parents=True, exist_ok=True)
        path = dir_ / f"{self.session_id}.json"
        path.write_text(
            json.dumps(self._to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def provider(self) -> str:
        """Normalised provider name: ``"CHATGPT"``, ``"GEMINI"``, or ``"ANTHROPIC"``."""
        return self._provider

    @property
    def message_count(self) -> int:
        """Total number of messages in history (user + assistant + system)."""
        return len(self.history)

    def __repr__(self) -> str:
        return (
            f"LLMSession("
            f"id={self.session_id[:8]}…, "
            f"provider={self._provider}, "
            f"model={self.model_name}, "
            f"messages={self.message_count})"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dispatch(self, messages: list[dict]) -> str:
        """Route *messages* to the correct provider and return the response text."""
        api_key = _resolve_api_key(self._provider, self._api_key_override, self._password)

        if self._provider == "CHATGPT":
            return _call_chatgpt(api_key, messages, self.model_name, self.web_search)
        elif self._provider == "GEMINI":
            return _call_gemini(api_key, messages, self.model_name, self.web_search)
        elif self._provider == "ANTHROPIC":
            return _call_anthropic(api_key, messages, self.model_name)
        else:
            raise ValueError(f"Unknown provider: {self._provider!r}")

    def _to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "provider": self._provider,
            "model_name": self.model_name,
            "web_search": self.web_search,
            "history": self.history,
            "created_at": self.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> "LLMSession":
        session = cls(
            provider=data["provider"],
            model_name=data["model_name"],
            web_search=data.get("web_search", False),
            session_id=data["session_id"],
        )
        session.history = data.get("history", [])
        session.created_at = data.get("created_at", datetime.now().isoformat())
        return session
