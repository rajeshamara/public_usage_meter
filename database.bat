@echo off
rem Usage Meter database operations; verified target, stop on SQL errors.
setlocal
cd /d "%~dp0"
if defined USAGE_METER_PYTHON (
  "%USAGE_METER_PYTHON%" db_admin.py %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" db_admin.py %*
) else (
  python db_admin.py %*
)
exit /b %errorlevel%
