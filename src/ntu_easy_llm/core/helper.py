import os.path
from pathlib import Path
from dotenv import load_dotenv, dotenv_values
from typing import List, Tuple, Literal


def load_api_key_from_env(env_path: str = None, TAG: Literal["chatgpt", "gemini"] = "") -> str:
    """
    讀取指定 env 檔並解密 ENC_API_KEY。
    - env_path: 可以是絕對路徑，也可以是相對於 helpers.py 的相對路徑
                若為 None，預設讀取同資料夾下 chatgpt_local.env
    - password: 解密用密碼，如未提供會要求輸入
    """
    base_dir = Path(__file__).resolve().parent  # helpers.py 所在資料夾

    # 如果沒有提供路徑，預設使用 chatgpt_local.env
    if env_path is None:
        env_path = base_dir / ".env"
    elif not os.path.exists(env_path):
        env_path = Path('.env')
        if not env_path.is_absolute():
            env_path = base_dir / env_path  # 轉成絕對路徑

    env_path = Path(env_path).resolve()

    if not env_path.exists():
        raise RuntimeError(f"{env_path} 不存在，無法讀取 {TAG}")

    # 讀 env
    env_vars = dotenv_values(env_path)
    enc_key = env_vars.get(TAG)
    return enc_key


if __name__ == "__main__":
    print(load_api_key_from_env(TAG="chatgpt"))
    print(load_api_key_from_env(TAG="gemini"))

