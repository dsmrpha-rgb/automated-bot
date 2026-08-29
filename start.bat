@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat

:loop
echo Starting bot...
python bot.py
echo.
echo Bot stopped. Restarting in 3 seconds... (Press Ctrl+C to exit)
timeout /t 3 /nobreak >nul
goto loop
