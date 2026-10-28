@echo off
rem Always-on engine on this PC (no cloud account needed): scans every minute while NSE / MCX are open and runs the
rem live trigger watcher (alerts within seconds when a "BUY CALL above X" level trades). Uses the shared database and
rem alert settings in .streamlit\secrets.toml. While it runs, the GitHub backup job stands down by itself.
rem Close this window (or press Ctrl+C) to stop.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
start "SRK live trigger watcher" /min .venv\Scripts\python stream.py
.venv\Scripts\python runner.py --vm
pause
