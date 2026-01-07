"""
讓使用者安裝後可以：
ntu_easy_llm -V
ntu_easy_llm --version
"""
from ._version import __version__

def main():
    import argparse

    parser = argparse.ArgumentParser(description="ntu_easy_llm CLI")
    parser.add_argument("-V", "--version", action="store_true", help="Show version")
    args = parser.parse_args()

    if args.version:
        print(__version__)
        return

    print("ntu_easy_llm CLI running ...")
