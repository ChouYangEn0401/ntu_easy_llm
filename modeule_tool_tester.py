from pathlib import Path
from typing import Literal, List
import warnings

# =========================
# 1️⃣ 相對 helpers.py 的路徑版本
# =========================
def get_env_path_relative(env_filenames: List[str] = ["ccc.env"]) -> Path:
    """
    從 helpers.py 相對位置讀取 env。
    支援多個 filename，返回第一個存在的。
    """
    base_dir = Path(__file__).resolve().parent

    for fname in env_filenames:
        candidate = base_dir / fname
        if candidate.exists():
            return candidate.resolve()

    warnings.warn(f"相對路徑中找不到 {env_filenames}，返回第一個作為路徑")
    return (base_dir / env_filenames[0]).resolve()


# =========================
# 2️⃣ 固定 project-root 的版本
# =========================
def get_env_path_from_root(project_root: str, env_filenames: List[str] = ["ccc.env"]) -> Path:
    """
    從指定 project_root 讀取 env
    支援多個 filename，返回第一個存在的。
    """
    root = Path(project_root)

    for fname in env_filenames:
        candidate = root / fname
        if candidate.exists():
            return candidate.resolve()

    warnings.warn(f"{project_root} 下找不到 {env_filenames}，返回第一個作為路徑")
    return (root / env_filenames[0]).resolve()


# =========================
# 3️⃣ 往上找 env 檔，自動找 project-root
# =========================
def get_env_path_search_up(env_filenames: List[str] = ["ccc.env"]) -> Path:
    """
    從 helpers.py 開始往上找，直到找到 env_filenames 中任意一個。
    """
    current = Path(__file__).resolve().parent

    while True:
        for fname in env_filenames:
            candidate = current / fname
            if candidate.exists():
                return candidate.resolve()
        if current.parent == current:
            warnings.warn(f"找不到 {env_filenames}，返回 {env_filenames[0]} 路徑")
            return (current / env_filenames[0]).resolve()
        current = current.parent


# =========================
# 4️⃣ 往上找專案根 (有 .git 或 env)
# =========================
def get_env_path_autoroot(env_filenames: List[str] = ["ccc.env"]) -> Path:
    """
    往上找專案根 (包含 .git 或 env_filenames)，返回第一個存在的 env。
    """
    current = Path(__file__).resolve().parent

    while True:
        # 找到專案根
        if (current / ".git").exists():
            for fname in env_filenames:
                candidate = current / fname
                if candidate.exists():
                    return candidate.resolve()
            warnings.warn(f"{current} 下找不到 {env_filenames}，返回第一個路徑")
            return (current / env_filenames[0]).resolve()
        else:
            for fname in env_filenames:
                candidate = current / fname
                if candidate.exists():
                    return candidate.resolve()

        if current.parent == current:
            warnings.warn(f"找不到專案根或 {env_filenames}，返回 {env_filenames[0]} 路徑")
            return (current / env_filenames[0]).resolve()

        current = current.parent


# =========================
# 5️⃣ 大包大覽統一接口
# =========================
def get_env_path(strategy: Literal["relative", "fixed_root", "search_up", "auto_root"] = "auto_root",
                 env_filenames: List[str] = ["ccc.env"],
                 project_root: str = None) -> Path:
    """
    統一接口，根據策略返回 env 路徑。
    strategy:
        - relative: helpers.py 相對位置
        - fixed_root: 指定 project_root
        - search_up: 往上找 env_filenames
        - auto_root: 往上找專案根 (.git 或 env)
    """
    if strategy == "relative":
        return get_env_path_relative(env_filenames)
    elif strategy == "fixed_root":
        if project_root is None:
            raise ValueError("fixed_root 需要指定 project_root")
        return get_env_path_from_root(project_root, env_filenames)
    elif strategy == "search_up":
        return get_env_path_search_up(env_filenames)
    elif strategy == "auto_root":
        return get_env_path_autoroot(env_filenames)
    else:
        raise ValueError(f"未知 strategy: {strategy}")
