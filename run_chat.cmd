@echo off
setlocal
set "PYTHONPATH=%~dp0src"
set "PYTHONDONTWRITEBYTECODE=1"
python -m kshetrajna.chat_launcher %*
if errorlevel 1 pause
