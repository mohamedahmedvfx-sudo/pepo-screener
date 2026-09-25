@echo off
cd /d "%~dp0"
echo Starting server at %date% %time% > server.log
py server.py >> server.log 2>&1
