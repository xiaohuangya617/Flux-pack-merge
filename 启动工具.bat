@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto run_venv

where py >nul 2>&1
if not errorlevel 1 goto run_py

where python >nul 2>&1
if not errorlevel 1 goto run_python

echo [提示] 未找到 Python 3.10 或更高版本。
echo 请安装 Python，并执行：python -m pip install -r requirements.txt
pause
exit /b 1

:run_venv
"%PY%" main.py
goto finish

:run_py
py -3 main.py
goto finish

:run_python
python main.py

:finish
if errorlevel 1 (
    echo.
    echo [提示] 程序异常退出（错误码 %errorlevel%）。
    pause
)
