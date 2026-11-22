"""urllib transport, proxy bypass and the command line, against tiny servers bound to 127.0.0.1.

Each server listens on 127.0.0.1 with an OS-chosen port, runs in a thread of
the test process and is shut down when the test ends. Every other address is
blocked for the duration of the tests.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from _fakehttp import block_network
from ntu_easy_llm import cli
from ntu_easy_llm.adapters import get_adapter
from ntu_easy_llm.adapters._http import UrllibTransport, is_local_url
from ntu_easy_llm.agent import LLMHTTPError
from ntu_easy_llm.errors import ErrorKind
from ntu_easy_llm.profiles import Profile


class _Server:
    """A ThreadingHTTPServer on 127.0.0.1:<free port> that records requests."""

    def __init__(self, handler_cls):
        self.requests: list[dict] = []
        outer = self

        class H(handler_cls):
            def log_message(self, *a):  # keep test output quiet
                pass

            def _record(self, body):
                outer.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "body": body})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class _ModelServer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, obj, headers=None):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._record(None)
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": "example-model"}]})
        else:
            self._send(404, {"error": "not here"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self._record(body)
        text = body["messages"][-1]["content"]
        if text == "quota":
            return self._send(429, {"error": {"code": "insufficient_quota"}})
        if text == "slow":
            return self._send(429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "3"})
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in ("echo", ": ", text):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            done = {"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3}}
            self.wfile.write(f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
            self.close_connection = True
            return
        self._send(200, {"choices": [{"finish_reason": "stop", "message": {"content": f"echo: {text}"}}],
                         "usage": {"prompt_tokens": 2, "completion_tokens": 3}})


class _ProxyServer(BaseHTTPRequestHandler):
    def _any(self):
        self._record(None)
        self.send_response(502)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_CONNECT = _any


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def server():
    s = _Server(_ModelServer)
    yield s
    s.close()


@pytest.fixture
def proxy(monkeypatch):
    s = _Server(_ProxyServer)
    for name in ("NO_PROXY", "no_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", s.url)
    monkeypatch.setenv("http_proxy", s.url)
    yield s
    s.close()


def local_profile(server, **options):
    return Profile("srv", "local/openai_compatible", model="example-model", base_url=f"{server.url}/v1",
                   options=options)


# --------------------------------------------------------------------------- #
# transport / proxy
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url,local", [
    ("http://127.0.0.1:1234/v1", True), ("http://localhost:11434", True), ("http://[::1]:8000/v1", True),
    ("http://192.168.0.5:8000/v1", True), ("http://10.1.2.3/v1", True), ("http://172.20.0.2:8000", True),
    ("http://169.254.10.1/v1", True), ("http://100.101.102.103:8000/v1", True), ("http://my-nas:8000", True),
    ("http://box.local:1234/v1", True), ("http://gpu.lan/v1", True),
    ("https://api.example.com/v1", False), ("http://8.8.8.8/v1", False), ("not a url", False),
])
def test_is_local_url(url, local):
    assert is_local_url(url) is local


def test_local_source_bypasses_the_system_proxy(server, proxy):
    a = get_adapter(local_profile(server))
    assert a.direct and a.chat([{"role": "user", "content": "hi"}]).text == "echo: hi"
    assert proxy.requests == [] and server.requests[-1]["path"] == "/v1/chat/completions"
    # the same URL through a transport that honours the system proxy really goes to the proxy
    resp = UrllibTransport(direct=False).request("GET", f"{server.url}/v1/models", timeout=5)
    assert resp.status_code == 502 and proxy.requests and proxy.requests[0]["method"] == "GET"


def test_real_sse_stream_and_http_errors(server):
    a = get_adapter(local_profile(server))
    pieces: list[str] = []
    r = a.chat([{"role": "user", "content": "hi"}], on_delta=pieces.append, timeout=5)
    assert pieces == ["echo", ": ", "hi"] and r.text == "echo: hi" and r.finish == "stop"
    assert r.usage.output_tokens == 3 and server.requests[-1]["body"]["stream"] is True
    with pytest.raises(LLMHTTPError) as ei:
        a.chat([{"role": "user", "content": "slow"}], timeout=5)
    assert ei.value.kind is ErrorKind.RATE_LIMITED and ei.value.retry_after == 3.0
    with pytest.raises(LLMHTTPError) as ei:
        a.chat([{"role": "user", "content": "quota"}], on_delta=lambda s: None, timeout=5)
    assert ei.value.kind is ErrorKind.QUOTA_EXHAUSTED


def test_probe_against_real_server_only_reads(server):
    res = get_adapter(local_profile(server)).probe(timeout=5)
    assert res.ok and [m.id for m in res.models] == ["example-model"]
    assert all(r["method"] == "GET" for r in server.requests)


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #

@pytest.fixture
def config(server, tmp_path):
    cfg = {"version": 1, "default": "srv",
           "profiles": {"srv": {"source": "local/openai_compatible", "base_url": f"{server.url}/v1",
                                "model": "example-model"},
                        "gw": {"source": "remote/api_key/openai_compatible", "base_url": f"{server.url}/v1",
                               "api_key_env": "EXAMPLE_GATEWAY_KEY", "model": "example-model"},
                        "missing": {"source": "local/openai_compatible", "base_url": f"{server.url}/v1",
                                    "model": "not-there"}},
           "chains": {"both": ["srv", "gw"]}}
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return str(path)


def test_cli_version_and_catalog(capsys):
    assert cli.main(["--version"]) == 0 and capsys.readouterr().out.strip().startswith("0.6")
    assert cli.main(["catalog", "--locale", "en"]) == 0
    out = capsys.readouterr().out
    assert "remote/api_key/openai" in out and "[available]" in out
    assert cli.main(["catalog", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["children"][0]["path"] == "local"
    assert cli.main([]) == 0


def test_cli_models_probe_chat(config, server, capsys):
    assert cli.main(["models", "--config", config]) == 0
    assert "example-model" in capsys.readouterr().out
    assert cli.main(["models", "--config", config, "--profile", "srv", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "example-model"
    assert cli.main(["probe", "--config", config, "--profile", "srv"]) == 0
    assert capsys.readouterr().out.startswith("ok")
    assert not [r for r in server.requests if r["method"] == "POST"]
    assert cli.main(["probe", "--config", config, "--profile", "srv", "--send", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["chat", "--config", config, "--profile", "srv", "hello", "--system", "be brief"]) == 0
    assert capsys.readouterr().out.strip() == "echo: hello"
    assert server.requests[-1]["body"]["messages"][0] == {"role": "system", "content": "be brief"}
    assert cli.main(["chat", "--config", config, "--stream", "hi"]) == 0
    assert capsys.readouterr().out.strip() == "echo: hi"


def test_cli_key_comes_from_the_environment_only(config, server, monkeypatch, capsys):
    monkeypatch.delenv("EXAMPLE_GATEWAY_KEY", raising=False)
    assert cli.main(["chat", "--config", config, "--profile", "gw", "hi"]) == 1
    err = capsys.readouterr().err
    assert "auth_invalid" in err and "EXAMPLE_GATEWAY_KEY" in err
    monkeypatch.setenv("EXAMPLE_GATEWAY_KEY", "example-gateway-value")
    assert cli.main(["chat", "--config", config, "--profile", "gw", "hi"]) == 0
    assert server.requests[-1]["headers"]["Authorization"] == "Bearer example-gateway-value"
    assert "example-gateway-value" not in capsys.readouterr().out


def test_cli_failures_and_exit_codes(config, tmp_path, capsys):
    assert cli.main(["chat", "--config", config, "quota"]) == 1
    assert "quota_exhausted" in capsys.readouterr().err
    assert cli.main(["probe", "--config", config, "--profile", "missing"]) == 0     # gateway-style: lenient
    assert "not in the server's model list" in capsys.readouterr().out
    assert cli.main(["chat", "--config", config, "--profile", "both", "hi"]) == 2   # chains need the router
    assert "chain" in capsys.readouterr().err
    assert cli.main(["models", "--config", str(tmp_path / "nope.json")]) == 2
    assert "config error" in capsys.readouterr().err


def test_cli_catalog_runs_offline_in_a_fresh_process():
    # The child writes UTF-8 (some availability notes are bilingual); read it as UTF-8 whatever the console code page is.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-m", "ntu_easy_llm.cli", "catalog", "--locale", "en"],
                         capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert out.returncode == 0 and "local/ollama" in out.stdout


def test_importing_the_adapters_loads_no_sdk():
    code = ("import sys, ntu_easy_llm, ntu_easy_llm.adapters\n"
            "import ntu_easy_llm.adapters.openai_style, ntu_easy_llm.adapters.anthropic, "
            "ntu_easy_llm.adapters.ollama, ntu_easy_llm.adapters.classifiers, ntu_easy_llm.cli\n"
            "from ntu_easy_llm.catalog import catalog; catalog()\n"
            "from ntu_easy_llm.errors import classify; classify('openai', status=429)\n"
            "print(sorted({'openai','anthropic','google.genai','llmop'} & set(sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60)
    assert out.stdout.strip() == "[]"
