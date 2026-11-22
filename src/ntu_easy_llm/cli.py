"""Command line: ``ntu_easy_llm``.

::

    ntu_easy_llm -V | --version
    ntu_easy_llm catalog [--json] [--locale zh-TW|en]
    ntu_easy_llm models --config FILE [--profile P] [--json]
    ntu_easy_llm probe  --config FILE [--profile P] [--send] [--json]
    ntu_easy_llm chat   --config FILE [--profile P] [--system TEXT] [--max-tokens N]
                        [--temperature T] [--stream] [--chain C] "text"

``--config`` is a profiles file (JSON, or YAML with ``ntu_easy_llm[yaml]``);
``--profile`` defaults to the file's ``default``. Keys come from the
environment variables / secret references the file names — never from the
command line. ``chat`` runs one profile, or with ``--chain`` a waterfall chain
(``ProfileSet.router``); the member that answered and any switch notice go to stderr.

Exit codes: 0 ok, 1 the provider or network failed (or probe said not ok),
2 configuration problem.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from ._version import __version__


def _out(text: str = "", *, err: bool = False, end: str = "\n") -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(text + end)
    stream.flush()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ntu_easy_llm", description="ntu_easy_llm command line")
    p.add_argument("-V", "--version", action="store_true", help="show the version")
    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("catalog", help="list the source tree and what this machine can use")
    c.add_argument("--json", action="store_true", help="print JSON")
    c.add_argument("--locale", default="zh-TW", help="zh-TW (default) or en")

    def with_config(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", required=True, help="profiles file (.json / .yaml)")
        sp.add_argument("--profile", default=None, help="profile id (default: the file's default)")

    m = sub.add_parser("models", help="list the models a profile's server offers (read-only)")
    with_config(m)
    m.add_argument("--json", action="store_true", help="print JSON")

    pr = sub.add_parser("probe", help="check a profile works (read-only unless --send)")
    with_config(pr)
    pr.add_argument("--send", action="store_true", help="also send one tiny chat request (spends tokens)")
    pr.add_argument("--json", action="store_true", help="print JSON")

    ch = sub.add_parser("chat", help="send one message with a profile and print the reply")
    with_config(ch)
    ch.add_argument("text", help="the message")
    ch.add_argument("--system", default="", help="system prompt")
    ch.add_argument("--max-tokens", type=int, default=None)
    ch.add_argument("--temperature", type=float, default=None)
    ch.add_argument("--stream", action="store_true", help="print the reply as it arrives")
    ch.add_argument("--chain", default=None,
                    help="run a chain (waterfall) from the file instead of one profile; prints which member answered")
    return p


def _adapter(args: argparse.Namespace) -> Any:
    from .profiles import load
    return load(args.config).adapter(args.profile)


def _cmd_catalog(args: argparse.Namespace) -> int:
    from .catalog import catalog
    root = catalog(locale=args.locale)
    if args.json:
        _out(json.dumps(root.to_dict(), ensure_ascii=False, indent=2))
        return 0
    en = not str(args.locale).lower().startswith("zh")
    yes, no = ("available", "not available") if en else ("可用", "不可用")

    def show(node: Any, depth: int) -> None:
        if node.path:
            line = f"{'  ' * (depth - 1)}{node.path}  {node.label}"
            if node.is_leaf:
                line += f"  [{yes}]" if node.available else f"  [{no}: {node.unavailable_reason}]"
            _out(line)
        for child in node.children:
            show(child, depth + 1)
    show(root, 0)
    return 0


def _model_line(m: Any) -> str:
    bits = [m.id]
    if m.label and m.label != m.id:
        bits.append(f"({m.label})")
    if m.loaded is not None:
        bits.append("[loaded]" if m.loaded else "[not loaded]")
    if m.context:
        bits.append(f"ctx={m.context}")
    if m.kind != "chat":
        bits.append(m.kind)
    return "  ".join(bits)


def _model_dict(m: Any) -> dict:
    return {"id": m.id, "label": m.label, "context": m.context, "loaded": m.loaded, "vision": m.vision,
            "tools": m.tools, "kind": m.kind}


def _cmd_models(args: argparse.Namespace) -> int:
    models = _adapter(args).list_models()
    if args.json:
        _out(json.dumps([_model_dict(m) for m in models], ensure_ascii=False, indent=2))
    else:
        for m in models:
            _out(_model_line(m))
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    res = _adapter(args).probe(send=args.send)
    if args.json:
        _out(json.dumps({"ok": res.ok, "reason": res.reason, "kind": str(res.kind) if res.kind else None,
                         "latency_s": res.latency_s, "models": [_model_dict(m) for m in res.models]},
                        ensure_ascii=False, indent=2))
    else:
        took = f" ({res.latency_s:.2f}s)" if res.latency_s is not None else ""
        if res.ok:
            _out(f"ok{took}" + (f": {res.reason}" if res.reason else "") + f"; {len(res.models)} model(s) listed")
        else:
            _out(f"failed [{res.kind}]{took}: {res.reason}")
    return 0 if res.ok else 1


def _cmd_chat(args: argparse.Namespace) -> int:
    if args.chain:
        from .profiles import load
        adapter = load(args.config).router(args.chain)
    else:
        adapter = _adapter(args)
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.text})
    on_delta = (lambda piece: _out(piece, end="")) if args.stream else None
    reply = adapter.chat(messages, max_tokens=args.max_tokens, temperature=args.temperature, on_delta=on_delta)
    if args.stream:
        _out("")
    else:
        _out(reply.text)
    for call in reply.tool_calls:
        _out(f"[tool call] {call.name} {json.dumps(call.arguments, ensure_ascii=False)}")
    for w in reply.warnings:
        _out(f"warning: {w}", err=True)
    if reply.finish in ("length", "thinking"):
        _out(f"note: the reply stopped early ({reply.finish})", err=True)
    if args.chain:
        if getattr(reply, "notice", ""):
            _out(f"notice: {reply.notice}", err=True)
        if getattr(reply, "profile_id", ""):
            _out(f"[{reply.profile_id}]", err=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # never crash on a console that cannot show a character
        except (AttributeError, ValueError):
            pass
    parser = _parser()
    args = parser.parse_args(argv)
    if args.version:
        _out(__version__)
        return 0
    if not args.command:
        parser.print_help()
        return 0
    from .agent import LLMError
    from .errors import ConfigError
    handler = {"catalog": _cmd_catalog, "models": _cmd_models, "probe": _cmd_probe, "chat": _cmd_chat}[args.command]
    try:
        return handler(args)
    except ConfigError as e:
        _out(f"config error: {e}", err=True)
        return 2
    except LLMError as e:
        _out(f"error [{e.kind}]: {e}", err=True)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
