@echo off
python -u -B "%~dp0run_all.py" %*
set "run_exit_code=%errorlevel%"
echo.
if not "%run_exit_code%"=="0" echo Pipeline failed. Check the stage log printed above.
pause
exit /b %run_exit_code%
