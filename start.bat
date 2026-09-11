@echo off
cd /d "%~dp0"
title TG Subscriptions [49188]
color 1E
echo.
echo  TG Subscriptions server
echo  http://localhost:49188/
echo  Data file: data.json
echo  Close this window to stop.
echo.
start "" http://localhost:49188/
python server.py
pause