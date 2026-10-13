@echo off
setlocal
set "PYTHONPATH=%~dp0src"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"

rem Select an installed Python 3.13 without installing runtimes automatically.
py -3.13 -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 13))" >nul 2>&1
if not errorlevel 1 goto run_py
python -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 13))" >nul 2>&1
if not errorlevel 1 goto run_python

echo Kshetrajna needs Python 3.13 on this Windows computer.
echo Install Python 3.13 from the official release page:
echo https://www.python.org/downloads/release/python-31316/
echo Choose Windows 64-bit for Intel/AMD, or ARM64 for Snapdragon.
echo Then reopen this launcher. No model or runtime was downloaded.
pause
exit /b 1

:run_py
py -3.13 -m kshetrajna.chat_launcher %*
goto finished

:run_python
python -m kshetrajna.chat_launcher %*

:finished
set "KSHETRAJNA_EXIT_CODE=%ERRORLEVEL%"
if not "%KSHETRAJNA_EXIT_CODE%"=="0" pause
exit /b %KSHETRAJNA_EXIT_CODE%
