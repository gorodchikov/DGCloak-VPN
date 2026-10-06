@echo off
rem Сборка DGCloakAdmin.exe (запускать на Windows, нужен Python с pip)
rem Onedir: exe вместе со scripts\ (нужны для деплоя на серверы).
rem Права администратора НЕ нужны (SSH/API только).
python -m pip install --upgrade pyinstaller
python cloak_icon.py app.ico
python -m PyInstaller --noconsole --icon app.ico --version-file version_info.txt --add-data "scripts;scripts" --name DGCloakAdmin cloak_admin.py
echo.
echo Готово: dist\DGCloakAdmin\DGCloakAdmin.exe
pause
