@echo off
echo === MT5 Trade Notifier: Build ===
echo.
echo Installing PyInstaller...
pip install pyinstaller --quiet
echo.
echo Building...
pyinstaller build.spec --noconfirm
echo.
echo Done! Output in dist\MT5 Trade Notifier\
pause
