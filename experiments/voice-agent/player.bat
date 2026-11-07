@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d %~dp0
echo Лика: команды вводятся здесь. «помощь» — список, «выход» — закрыть.
.venv\Scripts\python -u player.py
echo.
echo Лика закрылась, код %errorlevel%. Журнал в lika.log
pause
