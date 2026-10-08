@echo off
rem Сборка DGCloakAdmin.exe (запускать на Windows, нужен Python с pip)
rem Onedir: exe вместе со scripts\ (нужны для деплоя на серверы).
rem Права администратора НЕ нужны (SSH/API только).
rem
rem ВАЖНО: закрой DGCloakAdmin.exe и окна Проводника на dist\ перед сборкой —
rem иначе PyInstaller снесёт папку наполовину и упадёт на залоченных файлах.
rem Сборка идёт в build-out\ и копируется поверх dist\ только при успехе.
setlocal
set "OUT=build-out\DGCloakAdmin"
set "DST=dist\DGCloakAdmin"

tasklist /FI "IMAGENAME eq DGCloakAdmin.exe" | find /I "DGCloakAdmin.exe" >nul && (
    echo ОШИБКА: DGCloakAdmin.exe запущен — закрой приложение и повтори.
    exit /b 1
)

python -m pip install --upgrade pyinstaller pillow || exit /b 1
python cloak_icon.py admin.ico --admin || exit /b 1

python -m PyInstaller --noconsole --icon admin.ico --version-file version_info.txt ^
    --add-data "scripts;scripts" --distpath build-out --workpath build ^
    --hidden-import PIL.Image --hidden-import PIL.ImageDraw ^
    --name DGCloakAdmin cloak_admin.py || exit /b 1

if not exist "%OUT%\DGCloakAdmin.exe" (echo Сборка не выдала exe & exit /b 1)

rem ck-client.exe рядом с exe — нужен admin-API Cloak (создание/удаление юзеров)
set "CKSRC=%APPDATA%\DGCloak\bin\ck-client.exe"
if not exist "%CKSRC%" set "CKSRC=%APPDATA%\DGCloakVPN\ck-client.exe"
if exist "%CKSRC%" (
    copy /y "%CKSRC%" "%OUT%\ck-client.exe" >nul
) else (
    echo ВНИМАНИЕ: ck-client.exe не найден в %APPDATA%\DGCloak\bin —
    echo управление юзерами потребует путь в data.json: ck_client
)

rem Перенос готовой сборки поверх dist\ (при локе целевой папки staging остаётся целым)
if exist "%DST%" rmdir /s /q "%DST%" 2>nul
if exist "%DST%" (
    echo dist\ занят ^(открыт в Проводнике или запущен exe?^) —
    echo собранная копия лежит в %OUT% — закрой окна и запусти батник ещё раз.
    exit /b 1
)
move "%OUT%" "%DST%" >nul || (echo не удалось перенести в dist\ — сборка в %OUT% & exit /b 1)

echo.
echo Готово: %DST%\DGCloakAdmin.exe
endlocal
pause
