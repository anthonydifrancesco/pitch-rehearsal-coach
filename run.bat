@echo off
setlocal
cd /d "%~dp0"
title Pitch Rehearsal Coach
echo.
echo  Pitch Rehearsal Coach
echo  ---------------------

if not exist "requirements.txt" goto :notextracted

set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto :nopython

if not exist ".venv\Scripts\python.exe" (
  echo  First run: setting up. This takes a minute or two...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)
if not exist ".venv\installed2.txt" (
  echo  Installing packages...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :fail
  echo done> ".venv\installed2.txt"
)
if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo  Starting. Your browser will open in a few seconds.
echo  Leave this window open while you use the tool. Close it to stop.
echo.
start "" cmd /c "timeout /t 8 >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
echo  The server stopped. Any error is shown above.
pause
exit /b 0

:notextracted
echo  Could not find the app files next to run.bat.
echo  If you opened this from inside the zip, close it, right-click the zip,
echo  choose Extract All, then run run.bat from the extracted folder.
pause
exit /b 1

:nopython
echo  Python was not found on this computer.
echo  1. Install Python 3.11 or later from https://www.python.org/downloads/
echo  2. On the first installer screen, tick "Add python.exe to PATH"
echo  3. Run this file again.
pause
exit /b 1

:fail
echo.
echo  Setup failed. Copy the message above and send it to Claude.
pause
exit /b 1
