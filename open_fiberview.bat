@echo off
rem Opens FiberView after it has been installed with install_windows.bat.
if not exist "%LOCALAPPDATA%\FiberView\venv\Scripts\pythonw.exe" (
  echo FiberView is not installed yet. Double-click install_windows.bat first.
  pause
  exit /b 1
)
start "" "%LOCALAPPDATA%\FiberView\venv\Scripts\pythonw.exe" "%LOCALAPPDATA%\FiberView\app\fiberview.py" %*
