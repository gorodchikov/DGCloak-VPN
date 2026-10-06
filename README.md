# DGCloak VPN

Windows-клиент **OpenVPN over Cloak** + серверная админка.

## Клиент (`DGCloakVPN.exe`)

`cloak_ovpn.py` — tkinter-приложение с треем: запускает `ck-client.exe`, затем
консольный `openvpn.exe` через локальный прокси Cloak. Профили, статус из
management-интерфейса, авто-обход маршрутов (`bypass_ip` до сервера Cloak),
восстановление loopback.

Сборка: `build.bat` → `dist\DGCloakVPN.exe`.

## Админка (`DGCloakAdmin.exe`)

`cloak_admin.py` — управление Linux-серверами по SSH: аудит, автоматическое
развёртывание OpenVPN+Cloak (скрипты `scripts/`), чистка наследия Amnezia/Docker,
управление юзерами (сертификат easyrsa + UID Cloak через admin-API), отзыв,
мгновенное отключение, экспорт клиентских бандлов (.ovpn + ckclient-*.json).

Требования на машине админа: **PuTTY** (plink/pscp), `ck-client.exe`
(автоматически находится копия из клиента). На сервере: Ubuntu 24.04,
sudo NOPASSWD или root, SSH по ppk-ключу или паролю.

Сборка: `build-admin.bat` → `dist\DGCloakAdmin\DGCloakAdmin.exe`
(onedir, `scripts/` идут в комплекте).

Спецификация развёртывания и все грабли: `docs/server-setup.md`.
