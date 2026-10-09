@echo off
rem Сборка DGCloakAdmin.exe (запускать на Windows, нужен Python с pip)
rem Onefile: один exe удобнее распространять; scripts\ зашиты внутрь
rem (на старте распаковываются в _MEIPASS, код это понимает).
rem Права администратора НЕ нужны (SSH/API только).
rem
rem ВАЖНО: закрой DGCloakAdmin.exe и окна Проводника на dist\ перед сборкой —
rem иначе PyInstaller упадёт на залоченном файле.
setlocal
set "DST=dist\DGCloakAdmin.exe"

tasklist /FI "IMAGENAME eq DGCloakAdmin.exe" | find /I "DGCloakAdmin.exe" >nul && (
    echo ОШИБКА: DGCloakAdmin.exe запущен — закрой приложение и повтори.
    exit /b 1
)

python -m pip install --upgrade pyinstaller pillow || exit /b 1
python cloak_icon.py admin.ico --admin || exit /b 1

rem хвост старой onedir-сборки — чтобы не путались две версии
if exist "dist\DGCloakAdmin" rmdir /s /q "dist\DGCloakAdmin" 2>nul

python -m PyInstaller --noconsole --onefile --icon admin.ico ^
    --version-file version_info_admin.txt ^
    --add-data "scripts;scripts" --distpath dist --workpath build -y ^
    --hidden-import PIL.Image --hidden-import PIL.ImageDraw ^
    --name DGCloakAdmin cloak_admin.py || exit /b 1

if not exist "%DST%" (echo Сборка не выдала exe & exit /b 1)

echo.
echo Готово: %DST%
echo ck-client.exe внутрь НЕ зашит (размер) — админка найдёт его в
echo %APPDATA%\DGCloak\bin или скачает при первой юзер-операции.
endlocal
pause
