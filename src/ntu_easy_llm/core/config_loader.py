"""Configuration and environment file loading utilities.

Provides smart .env file discovery that works across different Python
environments (venv, PyInstaller exe, Docker, etc.) and loads API keys
from environment variables with proper error handling.

Key features:
- Automatic .env file discovery (walks up directory tree)
- Support for PyInstaller executables
- Clear error messages when keys are missing
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Iterable, Optional
from dotenv import dotenv_values


def _is_executable_mode() -> bool:
    """
    判斷是否為 PyInstaller / cx_Freeze 等 exe 環境
    """
    return getattr(sys, "frozen", False)


def _get_executable_dir() -> Path:
    """
    取得 exe 所在目錄
    """
    return Path(sys.executable).resolve().parent


def _iter_parent_dirs(start: Path) -> Iterable[Path]:
    """
    從 start 開始一路往上 yield parent dirs
    """
    current = start.resolve()
    while True:
        yield current
        if current.parent == current:
            break
        current = current.parent


def find_env_file(
    env_filename: str = ".env",
    explicit_path: Optional[str | Path] = None,
) -> Path:
    """
    尋找 env 檔案的統一入口

    搜尋順序：
    1. explicit_path（若指定）
    2. exe 所在目錄（若為 exe）
    3. current working directory
    4. cwd 往上一路找
    """

    # 1️⃣ 明確指定（最高優先）
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Specified env file not found: {p}")
        return p

    # 2️⃣ exe 模式（未來用）
    if _is_executable_mode():
        exe_dir = _get_executable_dir()
        candidate = exe_dir / env_filename
        if candidate.exists():
            return candidate

    # 3️⃣ 從 cwd 往上找
    cwd = Path.cwd()
    for parent in _iter_parent_dirs(cwd):
        candidate = parent / env_filename
        if candidate.exists():
            return candidate

    # 4️⃣ 找不到 → 明確錯誤
    raise FileNotFoundError(
        f"Cannot find '{env_filename}'.\n"
        f"Searched from: {cwd}\n\n"
        f"You can:\n"
        f"1. Place {env_filename} in your project directory\n"
        f"2. Or pass explicit env_path\n"
    )


def load_api_key(
    tag: str,
    env_path: Optional[str | Path] = None,
) -> str:
    """
    讀取指定 tag 的 API KEY

    Parameters
    ----------
    tag : str
        e.g. "CHATGPT_API_KEY", "GEMINI_API_KEY"
    env_path : Optional[str | Path]
        明確指定 env 檔案位置
    """

    env_file = find_env_file(explicit_path=env_path)
    env_vars = dotenv_values(env_file)

    value = env_vars.get(tag)
    if not value:
        raise RuntimeError(
            f"'{tag}' not found in env file: {env_file}\n"
            f"Please check your .env configuration."
        )

    return value


if __name__ == "__main__":
    print(load_api_key(tag="chatgpt"))
    print(load_api_key(tag="gemini"))
