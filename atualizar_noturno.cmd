@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===== %date% %time% ===== >> dados\atualizar.log
".venv\Scripts\python.exe" -X utf8 -u atualizar.py >> dados\atualizar.log 2>&1
echo saida=%errorlevel% >> dados\atualizar.log
