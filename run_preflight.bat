@echo off
REM Launcher for the Fashion50 pre-flight early-warning scan (Task Scheduler).
cd /d "%~dp0"
if not exist logs mkdir logs
"C:\Users\aresi\AppData\Local\Programs\Python\Python313\python.exe" preflight.py >> logs\preflight.log 2>&1
