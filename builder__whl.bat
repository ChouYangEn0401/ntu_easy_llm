@echo off
chcp 65001 >nul
SETLOCAL

echo =========================================
echo 確認你正在 venv 環境下執行此腳本
echo =========================================
set /p CONFIRM="你確定是在虛擬環境下嗎？ (y/n): "

if /I "%CONFIRM%"=="y" (
    echo 正在升級 build, setuptools, wheel...
    pip install --upgrade build setuptools wheel

    echo 正在建立套件...
    python -m build

    echo 完成！
) else (
    echo 已取消執行。請先啟動你的 venv。
)

ENDLOCAL
pause
