@echo off
cd /d "%~dp0"
if not exist "MarryTool.exe" (
  echo MarryTool.exe is missing. Extract the full ZIP before running START.bat.
  pause
  exit /b 1
)
"%~dp0MarryTool.exe" menu
pause
