@echo off
setlocal
set "PYTHONPATH=%~dp0src"
set "PYTHONDONTWRITEBYTECODE=1"
python -m kshetrajna.diagnostics --live --output "%~dp0reports\device-check.json" %*
set "task_result=%errorlevel%"
echo Report: %~dp0reports\device-check.json
if not "%task_result%"=="0" echo Device check needs attention. Review the report above.
pause
exit /b %task_result%
