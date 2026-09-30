@echo off
setlocal
set "PYTHONPATH=%~dp0src"
set "PYTHONDONTWRITEBYTECODE=1"
python -m unittest discover -s "%~dp0tests" -v
exit /b %errorlevel%
