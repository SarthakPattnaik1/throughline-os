@echo off
rem Windows. Double-click this and Throughline sets itself up, then starts.
rem
rem Unsigned, so SmartScreen shows "Windows protected your PC" on the first run -
rem More info, then Run anyway, once. An invited tester can be told to expect it.
rem The `curl ^| sh` line carries no warning at all, because the mark-of-the-web
rem is written by the downloading browser and not by the operating system.
rem
rem `%~dp0` is this file's own directory with a trailing backslash, computed by
rem cmd before anything runs. It is the Windows answer to the same hazard the
rem shell launchers guard against: Explorer starts a double-clicked file from
rem whatever working directory it pleases, which is frequently not this one.
setlocal
cd /d "%~dp0.."

rem `py` is the Python launcher that ships with python.org installs and picks a
rem version; `python` is the bare name, which on a machine with no Python at all
rem is the Microsoft Store stub that prints an advert and exits 9009. Trying `py`
rem first is what keeps the stub from being mistaken for an interpreter.
set "PYTHON="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)" >nul 2>&1 && set "PYTHON=py -3"
if not defined PYTHON (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)" >nul 2>&1 && set "PYTHON=python"
)

if not defined PYTHON (
  echo Throughline needs any Python 3.8 or newer to start.
  echo It fetches the exact version it runs on by itself.
  echo.
  echo   Install it from https://www.python.org/downloads/ or the Microsoft Store,
  echo   then double-click this file again.
  echo.
  pause
  exit /b 1
)

%PYTHON% scripts\manage.py start
set "STATUS=%ERRORLEVEL%"

if not "%STATUS%"=="0" (
  echo.
  echo Throughline stopped with an error ^(exit %STATUS%^).
  echo The lines above say why. "python scripts\manage.py doctor" checks the install.
  pause
)
exit /b %STATUS%
