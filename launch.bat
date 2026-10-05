@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
  if errorlevel 1 goto python_error
  py -3 -m creativity_lab serve --open
) else (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
  if errorlevel 1 goto python_error
  python -m creativity_lab serve --open
)
if errorlevel 1 goto python_error
exit /b 0
:python_error
echo.
echo Launch failed. Install Python 3.10+ and try again.
pause
exit /b 1
