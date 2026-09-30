@echo off
setlocal
set "PYTHONPATH=%~dp0src"
python -m kshetrajna --demo %*
if errorlevel 1 pause
