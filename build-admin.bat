@echo off
rem Сборка DGCloakAdmin.exe (запускать на Windows, нужен Python с pip)
rem Onedir: exe вместе со scripts\ (нужны для деплоя на серверы).
rem Права администратора НЕ нужны (SSH/API только).
rem
rem ВАЖНО: закрой DGCloakAdmin.exe и окна Проводника на dist\ перед сборкой —
rem иначе PyInstaller снесёт папку наполовину и упадёт на залоченных файлах.
rem Сборка идёт сразу в dist\ (без стейджинга).
setlocal
set "DST=dist\DGCloakAdmin"

tasklist /FI "IMAGENAME eq DGCloakAdmin.exe" | find /I "DGCloakAdmin.exe" >nul && (
    echo ОШИБКА: DGCloakAdmin.exe запущен — закрой приложение и повтори.
    exit /b 1
)

python -m pip install --upgrade pyinstaller pillow || exit /b 1
python cloak_icon.py admin.ico --admin || exit /b 1

python -m PyInstaller --noconsole --icon admin.ico --version-file version_info.txt ^
    --add-data "scripts;scripts" --distpath dist --workpath build -y ^
    --hidden-import PIL.Image --hidden-import PIL.ImageDraw ^
    --name DGCloakAdmin cloak_admin.py || exit /b 1

if not exist "%DST%\DGCloakAdmin.exe" (echo Сборка не выдала exe & exit /b 1)

rem ck-client.exe рядом с exe — нужен admin-API Cloak (создание/удаление юзеров)
set "CKSRC=%APPDATA%\DGCloak\bin\ck-client.exe"
if not exist "%CKSRC%" set "CKSRC=%APPDATA%\DGCloakVPN\ck-client.exe"
if exist "%CKSRC%" (
    copy /y "%CKSRC%" "%DST%\ck-client.exe" >nul
) else (
    echo ВНИМАНИЕ: ck-client.exe не найден в %APPDATA%\DGCloak\bin —
    echo управление юзерами потребует путь в data.json: ck_client
)

echo.
echo Готово: %DST%\DGCloakAdmin.exe
endlocal
pause
