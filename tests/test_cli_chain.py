"""``ntu_easy_llm chat --chain``: runs a waterfall chain from the profiles file (no network)."""
from __future__ import annotations

import json

from ntu_easy_llm import cli


def _config(tmp_path, mode: str) -> str:
    data = {
        "version": 1,
        "default": "main",
        "profiles": {
            "main": {"source": "local/fake", "model": "fake-1",
                     "options": {"script": [{"error": "quota_exhausted", "message": "used up"}]}},
            "backup": {"source": "local/fake", "model": "fake-2", "options": {"default": "from backup"}},
        },
        "chains": {"chat": {"members": ["main", "backup"], "waterfall": {"mode": mode}}},
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_chain_auto_switches_and_reports_the_member(tmp_path, capsys):
    code = cli.main(["chat", "--config", _config(tmp_path, "auto"), "--chain", "chat", "hello"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out.strip() == "from backup"
    assert "[backup]" in err
    assert "notice:" in err


def test_chain_off_keeps_the_first_members_error(tmp_path, capsys):
    code = cli.main(["chat", "--config", _config(tmp_path, "off"), "--chain", "chat", "hello"])
    out, err = capsys.readouterr()
    assert code == 1
    assert "from backup" not in out


def test_profile_mode_is_unchanged(tmp_path, capsys):
    code = cli.main(["chat", "--config", _config(tmp_path, "auto"), "--profile", "backup", "hi"])
    out, err = capsys.readouterr()
    assert code == 0 and out.strip() == "from backup"
    assert "[backup]" not in err
