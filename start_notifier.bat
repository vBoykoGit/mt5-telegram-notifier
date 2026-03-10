@echo off
python -m ensurepip --upgrade 2>nul
python -m pip install -r "%~dp0requirements.txt" --quiet
python -m notifier.app
pause
