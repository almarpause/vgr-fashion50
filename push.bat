@echo off
REM One-click push to GitHub (handles the cd /d + remote correctly).
cd /d "%~dp0"
echo Repo: %CD%
git remote remove origin 2>nul
git remote add origin https://github.com/almarpause/vgr-fashion50.git
git push -u origin main
echo.
echo Done. If a GitHub sign-in window appeared, approve it and re-run if needed.
pause
