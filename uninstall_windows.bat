@echo off
rem Removes FiberView. Your recordings are not touched.
if exist "%LOCALAPPDATA%\FiberView\uninstall_windows.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\FiberView\uninstall_windows.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\uninstall_windows.ps1"
)
pause
