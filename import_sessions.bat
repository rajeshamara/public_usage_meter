@echo off
rem Import selected local Codex telemetry; dates interpreted in America/New_York.
setlocal
cd /d "%~dp0"
if defined USAGE_METER_PYTHON (
  "%USAGE_METER_PYTHON%" import_sessions.py %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" import_sessions.py %*
) else (
  python import_sessions.py %*
)
exit /b %errorlevel%
