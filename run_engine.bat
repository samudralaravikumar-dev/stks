@echo off
rem Background engine: paper-trades signals and sends Telegram alerts while markets are open.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python runner.py
pause
