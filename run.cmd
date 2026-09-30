@echo off
setlocal
set "PYTHONPATH=%~dp0src"
python -m kshetrajna %*
if errorlevel 1 pause
