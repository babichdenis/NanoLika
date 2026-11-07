@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d %~dp0
.venv\Scripts\python -u player.py --keys --music --read идиот
echo.
echo Лика закрылась, код %errorlevel%. Журнал в lika.log
pause
