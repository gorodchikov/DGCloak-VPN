@echo off
rem Сборка DGCloakVPN.exe (запускать на Windows, нужен Python с pip)
rem Файлы cloak_ovpn.py и cloak_icon.py должны лежать рядом с этим bat.
rem --uac-admin: программа сама запросит права администратора (нужны для OpenVPN)
python -m pip install --upgrade pyinstaller pystray pillow
python cloak_icon.py app.ico
python -m PyInstaller --noconsole --onefile --uac-admin --icon app.ico --hidden-import pystray._win32 --name DGCloakVPN cloak_ovpn.py
echo.
echo Готово: dist\DGCloakVPN.exe
pause
