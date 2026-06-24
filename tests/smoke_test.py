#!/usr/bin/env python
"""ntu_easy_llm — 部署煙霧測試 (smoke test)

一個檔案就能確認整包 API 是否「接線正確 + 真的能用」。把這支檔案複製到
**安裝了 ntu_easy_llm 的另一個專案**裡執行，即可在部署前快速體檢。

兩段式測試
==========
* 離線段 (預設)  : 不需金鑰、不連網、不花 token。驗證所有公開名稱可 import、
                  lazy 載入、response parser、AES/RSA 金鑰加解密、Session
                  存檔/讀回/壓縮、Adapter 建構、錯誤處理。
* 線上段 (--live): 需要專案根目錄有 .env (chatgpt= / gemini= / anthropic=)。
                  對每個有金鑰的平台實際發 1 次最小請求，驗證 ask / async /
                  adapter / dispatcher / list_models / Session 真的會回應。

用法
====
    python smoke_test.py                  # 只跑離線段 (安全、免費)
    python smoke_test.py --live           # 離線 + 對所有有金鑰的平台實打
    python smoke_test.py --live --only chatgpt
    python smoke_test.py --live --web     # 額外測 web_search (較慢、較貴)

退出碼 0 = 全過；非 0 = 有失敗 (方便接 CI / 部署 gate)。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

# --- 讓本檔在「已安裝套件」與「repo 內 (未安裝，用 ./src)」兩種情境都能跑 ----
try:
    import ntu_easy_llm  # noqa: F401  下游專案 pip install 後就是這樣用
except ModuleNotFoundError:
    _src = Path(__file__).resolve().parent.parent / "src"
    if _src.is_dir():
        sys.path.insert(0, str(_src))
    import ntu_easy_llm  # noqa: F401


# =============================================================================
# 輸出環境設定
# =============================================================================
import io  # noqa: E402

# 真正的終端機走 Windows 主控台的 Unicode 路徑，中文可正常顯示、不用動它。
# 但一旦被導向檔案 / 管線 (CI log)，stdout 會退回 cp950 byte 編碼，遇到
# 非 cp950 字元時在這台 Windows 上會直接 segfault (而非乾淨報錯)。
# 因此「只在非 TTY」時把 stdout/stderr 重新包成 UTF-8，兩種情境都安全。
if not sys.stdout.isatty():
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8",
            errors="backslashreplace", line_buffering=True,
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8",
            errors="backslashreplace", line_buffering=True,
        )
    except Exception:  # noqa: BLE001 — 包不起來就維持原樣
        pass

# 只在真正的終端機 (TTY) 著色；被導向檔案 / CI log 時輸出乾淨純文字。
if os.name == "nt":
    os.system("")  # 讓 Windows 終端機吃 ANSI 色碼
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_GREEN = "\033[32m" if _COLOR else ""
_RED = "\033[31m" if _COLOR else ""
_YELLOW = "\033[33m" if _COLOR else ""
_DIM = "\033[2m" if _COLOR else ""
_RESET = "\033[0m" if _COLOR else ""

_results: list[tuple[str, str, str]] = []  # (status, name, detail)


class Skip(Exception):
    """在測試函式內 raise 表示「條件不足，略過」而非失敗。"""


def check(name: str, fn) -> None:
    """執行單一檢查並記錄結果。"""
    try:
        detail = fn() or ""
        _results.append(("PASS", name, str(detail)))
        line = f"{_GREEN}  [PASS]{_RESET} {name}"
        if detail:
            line += f"  {_DIM}{detail}{_RESET}"
        print(line)
    except Skip as exc:
        _results.append(("SKIP", name, str(exc)))
        print(f"{_YELLOW}  [SKIP]{_RESET} {name}  {_DIM}略過：{exc}{_RESET}")
    except Exception as exc:  # noqa: BLE001 — smoke test 要吃下所有錯誤
        _results.append(("FAIL", name, f"{type(exc).__name__}: {exc}"))
        print(f"{_RED}  [FAIL]{_RESET} {name}")
        print(f"{_RED}     -> {type(exc).__name__}: {exc}{_RESET}")
        if os.environ.get("SMOKE_VERBOSE"):
            traceback.print_exc()


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# =============================================================================
# 共用工具
# =============================================================================

def _write_env(dirpath: Path, mapping: dict[str, str]) -> None:
    lines = []
    for k, v in mapping.items():
        # 值可能含等號/換行以外字元；用引號包起來比較安全
        lines.append(f'{k}="{v}"')
    (dirpath / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


class _chdir:
    """暫時切換工作目錄 (load_api_key 從 cwd 往上找 .env)。"""

    def __init__(self, target: Path):
        self.target = target
        self._old: str | None = None

    def __enter__(self):
        self._old = os.getcwd()
        os.chdir(self.target)
        return self

    def __exit__(self, *exc):
        if self._old:
            os.chdir(self._old)


# =============================================================================
# 離線段：不需金鑰、不連網
# =============================================================================

def offline_tests() -> None:
    section("離線段：API 接線、lazy 載入、解析、加解密、Session")

    # --- 版本與公開名稱 ---
    def _version():
        v = ntu_easy_llm.__version__
        assert isinstance(v, str) and v, "版本字串應為非空 str"
        return f"v{v}"

    check("__version__ 可讀且為字串", _version)

    def _all_importable():
        missing = []
        for name in ntu_easy_llm.__all__:
            if name == "__version__":
                continue
            try:
                getattr(ntu_easy_llm, name)
            except Exception as e:  # noqa: BLE001
                missing.append(f"{name} ({e})")
        assert not missing, f"無法取得：{missing}"
        return f"{len(ntu_easy_llm.__all__) - 1} 個公開名稱全部可載入"

    check("__all__ 列出的每個公開名稱皆可 lazy 載入", _all_importable)

    # --- response parsers (用假物件，不連網) ---
    def _parsers():
        from ntu_easy_llm import (
            parse_anthropic_response,
            parse_gemini_response,
            parse_openai_completion,
            parse_openai_response,
        )
        assert parse_openai_response(SimpleNamespace(output_text=" hi ")) == "hi"
        assert parse_openai_response(SimpleNamespace(output_text=None)) == ""
        comp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=" yo "))]
        )
        assert parse_openai_completion(comp) == "yo"
        assert parse_gemini_response(SimpleNamespace(text=" g ")) == "g"
        ant = SimpleNamespace(content=[SimpleNamespace(text=" a ")])
        assert parse_anthropic_response(ant) == "a"
        assert parse_anthropic_response(SimpleNamespace(content=[])) == ""
        return "4 種 parser + None/空值處理正確"

    check("response parser 正確萃取 + 處理 None/空值", _parsers)

    # --- 金鑰加解密：用真實 .env 探索路徑 (假金鑰) ---
    def _key_roundtrip():
        from ntu_easy_llm import KeyMaterial, aes_encrypt, load_api_key

        plain_secret = "sk-plain-FAKE-123"
        aes_secret = "sk-aes-FAKE-456"
        aes_pw = "hunter2"

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_env(d, {
                "plainkey": plain_secret,
                "aeskey": aes_encrypt(aes_secret, aes_pw),
                "AES_PW": aes_pw,
            })
            with _chdir(d):
                assert load_api_key("plainkey") == plain_secret
                assert KeyMaterial.plain("plainkey").resolve() == plain_secret
                assert KeyMaterial.aes("aeskey", "AES_PW").resolve() == aes_secret
        return "plain + AES 經由 .env 探索 -> resolve 還原成功"

    check("load_api_key / KeyMaterial.plain / .aes 端對端還原", _key_roundtrip)

    def _rsa_roundtrip():
        # RSA PEM 含換行，不適合塞 .env；直接測策略 + encrypt/decrypt 對稱性
        from ntu_easy_llm import rsa_encrypt
        from ntu_easy_llm.core.cryptions import RSADecryptStrategy
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        secret = "sk-rsa-FAKE-789"
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / ".env").write_text(f'RSA_PEM="{priv_pem}"\n', encoding="utf-8")
            with _chdir(d):
                cipher = rsa_encrypt(secret, pub_pem)
                assert RSADecryptStrategy("RSA_PEM").decrypt(cipher) == secret
        return "rsa_encrypt -> RSADecryptStrategy 對稱還原成功"

    check("rsa_encrypt / RSADecryptStrategy 對稱還原", _rsa_roundtrip)

    def _lazy_resolve():
        # 建構不該讀 .env / 不該丟錯；要到 resolve() 才真的去找 key
        from ntu_easy_llm import KeyMaterial
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_env(d, {"exists": "v"})
            with _chdir(d):
                km = KeyMaterial.plain("does_not_exist")  # 建構：不應丟錯
                raised = False
                try:
                    km.resolve()  # 取值：此時才應丟錯
                except Exception:
                    raised = True
                assert raised, "resolve() 找不到 key 時應丟錯"
        return "建構不讀 .env，resolve() 才取值 (first-touch)"

    check("KeyMaterial 是 lazy 的 (建構≠取值)", _lazy_resolve)

    # --- Session 存檔 / 讀回 / 壓縮 (用 stub 取代真實 API 呼叫) ---
    def _session_persist():
        from ntu_easy_llm import LLMSession
        s = LLMSession("chatgpt", model_name="gpt-4.1")
        s._dispatch = lambda messages: "stub-reply"  # 攔截真實呼叫
        s.ask("hi")
        assert s.message_count == 2, "一次 ask 應產生 user+assistant 2 則"
        assert s.history[0] == {"role": "user", "content": "hi"}
        with tempfile.TemporaryDirectory() as td:
            path = s.save(sessions_dir=td)
            assert Path(path).exists()
            s2 = LLMSession.resume(s.session_id, sessions_dir=td)
            assert s2.session_id == s.session_id
            assert s2.history == s.history
            assert s2.provider == "CHATGPT"
        return "save -> resume 後 id / history / provider 一致"

    check("LLMSession.save / resume 往返保真", _session_persist)

    def _session_compress():
        from ntu_easy_llm import LLMSession
        s = LLMSession("anthropic")
        s._dispatch = lambda messages: "summary-or-reply"
        for i in range(6):
            s.ask(f"q{i}")          # 6 來回 = 12 則
        assert s.message_count == 12
        s.compress_history(keep_last=2)   # 保留最後 2 來回 = 4 則 + 1 則摘要
        assert s.message_count == 5, f"壓縮後應為 5 則，實得 {s.message_count}"
        assert s.history[0]["role"] == "system", "壓縮後第一則應為 system 摘要"
        return "12 則 -> compress(keep_last=2) -> 5 則 (含 system 摘要)"

    check("LLMSession.compress_history 壓縮邏輯正確", _session_compress)

    def _session_async():
        from ntu_easy_llm import LLMSession
        s = LLMSession("gemini")
        s._dispatch = lambda messages: "async-reply"  # 攔截真實呼叫
        box: dict = {}
        fut = s.ask_async("hi", end_callback=lambda t: box.setdefault("cb", t))
        assert isinstance(fut, Future), "ask_async 應回傳 Future"
        result = fut.result(timeout=10)
        assert result == "async-reply"
        assert box.get("cb") == "async-reply", "end_callback 未收到回應"
        assert s.message_count == 2, "async ask 也應寫入歷史 (user+assistant)"
        return "Future + end_callback 觸發，歷史正確更新"

    check("LLMSession.ask_async (背景執行緒 + callback)", _session_async)

    # --- Adapter 建構 (帶假金鑰，不呼叫 .ask 不連網) ---
    def _adapters_construct():
        from ntu_easy_llm import (
            AnthropicAdapter,
            ChatGPTAdapter,
            GeminiAdapter,
            LLMAdapter,
        )
        cg = ChatGPTAdapter("fake", "gpt-4.1")
        gm = GeminiAdapter("fake", "gemini-2.5-flash")
        an = AnthropicAdapter("fake", "claude-haiku-4-5-20251001")
        for a in (cg, gm, an):
            assert isinstance(a, LLMAdapter)
            assert hasattr(a, "ask")
            assert a.api_key == "fake"
        return "3 個 Adapter 皆為 LLMAdapter 子類且帶 api_key/ask"

    check("Adapter 建構正確 (不連網)", _adapters_construct)

    # --- 錯誤處理 ---
    def _error_handling():
        from ntu_easy_llm import LLMSession, ask
        for bad in ("nope", ""):
            try:
                LLMSession(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"LLMSession({bad!r}) 應丟 ValueError")
        try:
            ask("BADPROVIDER", "k", "p", "m")  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError("ask() 未知 provider 應丟 ValueError")
        return "未知 provider 在 Session 與 ask() 皆丟 ValueError"

    check("未知 provider 會明確報錯", _error_handling)


# =============================================================================
# 線上段：需 .env 金鑰，會花少量 token
# =============================================================================

# 用最便宜/最快的模型，prompt 也壓到最小
_LIVE_MODELS = {
    "chatgpt": "gpt-4.1-mini",
    "gemini": "gemini-2.5-flash-lite",
    "anthropic": "claude-haiku-4-5-20251001",
}
_TINY_PROMPT = "Reply with exactly one word: OK"


def _available_providers(only: str | None) -> list[str]:
    from ntu_easy_llm import load_api_key
    out = []
    for p in ("chatgpt", "gemini", "anthropic"):
        if only and p != only:
            continue
        try:
            load_api_key(p)
            out.append(p)
        except Exception:
            pass
    return out


def live_tests(only: str | None, web: bool) -> None:
    section("線上段：對有金鑰的平台實際發送請求")

    providers = _available_providers(only)
    if not providers:
        check(
            "找到至少一個平台的金鑰",
            lambda: (_ for _ in ()).throw(
                Skip("找不到 .env 或其中沒有可用金鑰 (chatgpt/gemini/anthropic)")
            ),
        )
        return

    print(f"{_DIM}  偵測到金鑰：{', '.join(providers)}{_RESET}")

    import ntu_easy_llm as L

    ask_blocking = {
        "chatgpt": L.ask_chatgpt,
        "gemini": L.ask_gemini,
        "anthropic": L.ask_anthropic,
    }
    ask_async = {
        "chatgpt": L.ask_chatgpt_async,
        "gemini": L.ask_gemini_async,
        "anthropic": L.ask_anthropic_async,
    }
    list_models = {
        "chatgpt": L.list_chatgpt_models,
        "gemini": L.list_gemini_models,
        "anthropic": L.list_anthropic_models,
    }
    adapters = {
        "chatgpt": L.ChatGPTAdapter,
        "gemini": L.GeminiAdapter,
        "anthropic": L.AnthropicAdapter,
    }
    dispatch_name = {"chatgpt": "CHATGPT", "gemini": "GEMINI", "anthropic": "ANTHROPIC"}

    # check() 會「立即」同步執行傳入的函式，所以下面的巢狀函式不需要
    # default-arg 綁定 p/model/key —— 它們在進到下一圈迴圈前就跑完了。
    for p in providers:
        model = _LIVE_MODELS[p]
        key = L.load_api_key(p)

        def _list():
            models = list_models[p](L.load_api_key(p))
            assert models, "回傳清單為空"
            return f"{len(models)} 個模型"

        def _blocking():
            return _ask_ok(ask_blocking[p](_TINY_PROMPT, model_name=model))

        def _adapter():
            return _ask_ok(adapters[p](key, model).ask(_TINY_PROMPT))

        def _dispatch():
            return _ask_ok(L.ask(dispatch_name[p], key, _TINY_PROMPT, model))

        check(f"[{p}] list_models 回傳非空清單", _list)
        check(f"[{p}] ask (阻斷) 回傳非空字串", _blocking)
        check(f"[{p}] ask_async + end_callback 觸發",
              lambda: _check_async(ask_async[p], model))
        check(f"[{p}] Adapter.ask 回傳非空字串", _adapter)
        check(f"[{p}] 統一 dispatcher ask() 回傳非空字串", _dispatch)

        if web and p in ("chatgpt", "gemini"):
            def _websearch():
                return _ask_ok(ask_blocking[p](
                    "今天台北天氣如何？用一句話。",
                    model_name=model, web_search=True,
                ))
            check(f"[{p}] web_search=True 可運作", _websearch)

    # --- Session 多輪記憶 + save/resume (挑一個可用平台) ---
    sp = providers[0]
    check(
        f"[{sp}] LLMSession 多輪對話保有記憶",
        lambda sp=sp: _check_session_memory(sp),
    )


def _ask_ok(r) -> str:
    """斷言回應為非空字串，並回傳一段簡短預覽當作 check 的 detail。"""
    assert isinstance(r, str) and r.strip(), f"回應不是非空字串：{r!r}"
    one = " ".join(r.split())
    return f'回應："{one[:40]}{"..." if len(one) > 40 else ""}"'


def _check_async(async_fn, model) -> str:
    box: dict = {}
    fut = async_fn(
        _TINY_PROMPT,
        model_name=model,
        end_callback=lambda t: box.setdefault("cb", t),
    )
    assert isinstance(fut, Future), "async 版本應回傳 Future"
    result = fut.result(timeout=60)
    assert isinstance(result, str) and result.strip(), f"回應不是非空字串：{result!r}"
    # end_callback 在 worker thread 觸發，future 完成後應已被呼叫
    assert box.get("cb") == result, "end_callback 未收到與 .result() 相同的字串"
    return "Future.result() 與 end_callback 一致"


def _check_session_memory(provider: str) -> str:
    import ntu_easy_llm as L
    model = _LIVE_MODELS[provider]
    s = L.LLMSession(provider, model_name=model)
    s.ask("我的幸運數字是 42，請記住。")
    answer = s.ask("我剛剛說的幸運數字是多少？只回數字。")
    assert "42" in answer, f"模型未記得先前內容，回應：{answer!r}"
    # 順便驗真實 save/resume
    with tempfile.TemporaryDirectory() as td:
        s.save(sessions_dir=td)
        s2 = L.LLMSession.resume(s.session_id, sessions_dir=td)
        assert s2.message_count == s.message_count
    return f"跨輪記得 42 ({s.message_count} 則歷史)，save/resume OK"


# =============================================================================
# 進入點
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="ntu_easy_llm 部署煙霧測試")
    parser.add_argument("--live", action="store_true",
                        help="額外對有金鑰的平台實際發送請求 (需 .env，會花 token)")
    parser.add_argument("--only", choices=["chatgpt", "gemini", "anthropic"],
                        help="線上段只測指定平台")
    parser.add_argument("--web", action="store_true",
                        help="線上段額外測 web_search (較慢/較貴)")
    args = parser.parse_args()

    print(f"ntu_easy_llm smoke test  ({Path(ntu_easy_llm.__file__).parent})")

    offline_tests()
    if args.live:
        live_tests(args.only, args.web)
    else:
        print(f"\n{_DIM}（未加 --live，已略過線上實打測試。要驗證真實 API 呼叫請執行："
              f" python {Path(__file__).name} --live）{_RESET}")

    # --- 總結 ---
    passed = sum(1 for s, *_ in _results if s == "PASS")
    failed = sum(1 for s, *_ in _results if s == "FAIL")
    skipped = sum(1 for s, *_ in _results if s == "SKIP")
    section("總結")
    print(f"  {_GREEN}通過 {passed}{_RESET}   "
          f"{_RED}失敗 {failed}{_RESET}   "
          f"{_YELLOW}略過 {skipped}{_RESET}")
    if failed:
        print(f"\n{_RED}失敗項目：{_RESET}")
        for status, name, detail in _results:
            if status == "FAIL":
                print(f"  {_RED}[FAIL] {name}{_RESET}  - {detail}")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
