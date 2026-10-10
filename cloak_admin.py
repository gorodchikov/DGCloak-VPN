#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DGCloak Admin — управление серверами OpenVPN over Cloak.

Возможности:
  - реестр серверов (host, SSH-порт, ppk/пароль);
  - аудит и автоматическое развёртывание (scripts/*.sh через plink/pscp);
  - чистка наследия Amnezia/Docker;
  - управление юзерами: сертификат OpenVPN (easyrsa) + UID Cloak (admin-API);
  - отзыв, удаление, мгновенное отключение (kill через management OpenVPN);
  - экспорт клиентских конфигов (.dgcloak).

SSH-слой — внешние plink/pscp (ppk нативно). Юзеры Cloak — через локальный
`ck-client.exe -a` (admin-API), без SSH.
"""

import atexit
import base64
import io
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_NAME = "DGCloak Admin"
DGCLOAK_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloak")
OLD_APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakAdmin")
OLD_VPN_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakVPN")
APP_DIR = os.path.join(DGCLOAK_DIR, "Admin")
DATA_FILE = os.path.join(APP_DIR, "data.json")
BUNDLES_DIR = os.path.join(APP_DIR, "user_bundles")  # <сервер>\<юзер>.dgcloak, плоско
BIN_DIR = os.path.join(DGCLOAK_DIR, "bin")  # общие зависимости: plink/pscp/ck-client
VPN_DIR = os.path.join(DGCLOAK_DIR, "VPN")  # данные соседнего VPN-клиента
PIDS_FILE = os.path.join(APP_DIR, "pids.json")  # наши дочерние plink/pscp/ssh
GUIDE_URL = ("https://gorodchikov.github.io/DGCloak-VPN-releases/"
             "user-guide-%s.html")

# Скрипты лежат рядом с исходником/exe (для onefile — внутри _MEIPASS)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
if not os.path.isdir(SCRIPTS_DIR) and getattr(sys, "_MEIPASS", None):
    SCRIPTS_DIR = os.path.join(sys._MEIPASS, "scripts")

# ---- реестр дочерних процессов (plink/pscp/ssh/ck-client) ----
# Сирота-plink на мёртвой SSH-сессии жил после закрытия админки и лочил dist\.
# Поэтому: каждый Popen регистрируется в pids.json (PID → имя exe), на выходе
# оставшихся добиваем taskkill'ом, а при старте чистим сирот от аварийного
# завершения. Проверка имени exe защищает от переиспользования PID.
_child_procs = {}          # pid -> basename(exe)
_child_lock = threading.Lock()


def _save_child_pids():
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(PIDS_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in _child_procs.items()}, f)
    except OSError:
        pass


def track_proc(p):
    """Зарегистрировать Popen — его добьют при выходе приложения."""
    try:
        argv = p.args if isinstance(p.args, (list, tuple)) else [p.args]
        name = os.path.basename(str(argv[0] or "?"))
    except (AttributeError, IndexError):
        name = "?"
    with _child_lock:
        _child_procs[p.pid] = name
        _save_child_pids()
    return p


def untrack_proc(p):
    """Снять с учёта завершившийся процесс (живой остаётся до выхода)."""
    if p.poll() is None:
        return
    with _child_lock:
        if _child_procs.pop(p.pid, None) is not None:
            _save_child_pids()


def _tasklist_out(pid):
    try:
        return subprocess.run(
            ["tasklist", "/FI", "PID eq %s" % int(pid), "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def _pid_exists(pid):
    """CSV-строки начинаются с кавычки; INFO-строки — нет (locale-proof)."""
    return any(l.startswith('"') for l in _tasklist_out(pid).splitlines())


def _pid_running(pid, name):
    """Жив ли процесс с этим PID и ожидаемым именем exe
    (в реестре имя может быть без .exe — принимаем оба варианта)."""
    want = {name.lower(), name.lower() + ".exe"}
    for l in _tasklist_out(pid).splitlines():
        parts = [x.strip('"') for x in l.split('","')]
        if len(parts) > 1 and parts[0].lower() in want:
            return True
    return False


def _kill_pid(pid):
    """taskkill /F /T — процесс вместе с его дочерними."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                       capture_output=True, timeout=10,
                       stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def kill_child_procs():
    """atexit: завершить всех отслеживаемых детей, оставшихся живыми."""
    with _child_lock:
        items = list(_child_procs.items())
        _child_procs.clear()
        _save_child_pids()
    for pid, name in items:
        if _pid_running(pid, name):
            _kill_pid(pid)


atexit.register(kill_child_procs)


def cleanup_stale_procs(log=print):
    """При старте: добить детей, оставшихся от аварийного прошлого запуска."""
    try:
        with open(PIDS_FILE, encoding="utf-8") as f:
            stale = json.load(f)
    except (OSError, ValueError):
        return
    killed = []
    for pid, name in stale.items():
        if _pid_running(pid, name):
            _kill_pid(pid)
            killed.append("%s (PID %s)" % (name, pid))
    if killed:
        log(T("Завершил процессы от прошлого запуска: ") + ", ".join(killed))
    with _child_lock:
        if not _child_procs:
            _save_child_pids()   # файл устарел — обнуляем


def vpn_active_host():
    """Хост сервера активного подключения VPN-клиента (DGCloakVPN) или None:
    читает pids.json (жив ли клиент) и data.json/ck-конфиг профиля (куда
    подключён). Смотрим и новую папку %APPDATA%\\DGCloak\\VPN, и старую
    %APPDATA%\\DGCloakVPN — старые сборки клиента пишут туда."""
    for d in (VPN_DIR, OLD_VPN_DIR):
        host = _vpn_active_host_in(d)
        if host:
            return host
    return None


def _vpn_active_host_in(vdir):
    try:
        with open(os.path.join(vdir, "pids.json"), encoding="utf-8") as f:
            pids = json.load(f)
        if not any(_pid_exists(p) for p in pids.values() if p):
            return None
        with open(os.path.join(vdir, "data.json"), encoding="utf-8") as f:
            vdata = json.load(f)
        prof = next((p for p in vdata.get("profiles", [])
                     if p.get("name") == vdata.get("last_profile")), None)
        if not prof:
            return None
        host = prof.get("server") or prof.get("bypass_ip")
        if not host:
            ckp = prof.get("ck_config")
            if ckp and os.path.isfile(ckp):
                with open(ckp, encoding="utf-8") as f:
                    host = json.load(f).get("RemoteHost")
        return host or None
    except (OSError, ValueError):
        return None


CK_VERSION = "2.12.0"
INT64_MAX = 9223372036854775807
# Cloak хранит «безлимит» как MaxInt64 и уменьшает credit по мере расхода
# трафика — точное сравнение с INT64_MAX срабатывает только у нетронутого
# юзера. Порог 2**62 (~4.6 ЭБ): реальная квота туда не дотянется никогда.
UNLIMITED = 1 << 62


def _is_unlim(v):
    return v is None or v >= UNLIMITED
FAR_FUTURE = 2000000000  # ~2033 — «бессрочный» юзер Cloak


def _fmt_rate(bps):
    """байт/с → Мбит/с компактно; отсутствие/UNLIMITED = безлимит (∞)."""
    if _is_unlim(bps):
        return "∞"
    mb = bps * 8 / 1e6
    return T("%gМ") % round(mb, 1) if mb >= 1 else T("%dк") % round(bps * 8 / 1e3)


def _fmt_bytes(v):
    """байт → объём (МБ/ГБ); отсутствие/UNLIMITED = безлимит (∞)."""
    if _is_unlim(v):
        return "∞"
    return ("%gG" % round(v / 1073741824, 1)) if v >= 1073741824 \
        else "%dM" % round(v / 1048576)


def _fmt_limits(up, down):
    u, d = _fmt_rate(up), _fmt_rate(down)
    return "∞" if u == "∞" and d == "∞" else "↑%s ↓%s" % (u, d)


def _fmt_quota(up, down):
    u, d = _fmt_bytes(up), _fmt_bytes(down)
    return "∞" if u == "∞" and d == "∞" else "↑%s ↓%s" % (u, d)

# ------------------------------------------------------------ localization
# Ключ — русская исходная строка. Пустых значений нет: фолбэк — сам ключ.
# Строки лога переводятся в момент записи: уже записанные записи журнала
# и заметки шагов остаются на языке, на котором были созданы.
_LANG = "ru"


def T(ru):
    """Перевод для модульных функций без self (текущий язык — _LANG)."""
    return STRINGS_EN.get(ru, ru) if _LANG == "en" else ru


def detect_lang():
    """Стартовый язык по локали Windows: ru* → ru, остальные → en."""
    try:
        import ctypes
        # GetUserDefaultUILanguage → LANGID; primary lang 0x19 = Russian
        return "ru" if (ctypes.windll.kernel32.
                        GetUserDefaultUILanguage() & 0xFF) == 0x19 else "en"
    except Exception:
        return "en"


STRINGS_EN = {
    # --- общие / заголовки / кнопки ---
    "Серверы": "Servers",
    "Сервер": "Server",
    "Юзер": "User",
    "Название": "Name",
    "Доменное имя/IP": "Hostname/IP",
    "SSH порт": "SSH port",
    "SSH логин": "SSH login",
    "Ключ .ppk (PuTTY)": ".ppk key (PuTTY)",
    "Ключ OpenSSH": "OpenSSH key",
    "Пароль (если нет ключей)": "Password (if no keys)",
    "Комментарий": "Comment",
    "Добавить": "Add",
    "Изменить": "Edit",
    "Удалить": "Delete",
    "Обновить": "Refresh",
    "Открыть": "Open",
    "Закрыть": "Close",
    "Создать…": "Create…",
    "Изменить…": "Edit…",
    "Новый юзер": "New user",
    "Изменить «%s»": "Edit \"%s\"",
    "Развёртывание": "Deployment",
    "Пользователи": "Users",
    "Лог": "Log",
    "Лог — %s": "Log — %s",
    "Копировать лог": "Copy log",
    "Очистить лог": "Clear log",
    "Лог скопирован в буфер (%d символов)": "Log copied to clipboard (%d chars)",
    "Лог очищен.": "Log cleared.",
    "Язык:": "Language:",
    "Руководство пользователя": "User guide",
    "Подробный вывод": "Verbose output",
    "Стоп операцию": "Abort operation",
    "с": "s",
    "Убить зависшую SSH-команду\n(провайдер рвёт связь, сервер молчит)":
        "Kill the hung SSH command\n(ISP dropped the link, server went silent)",
    "  прерываю операцию…": "  aborting the operation…",
    "(прервано пользователем)": "(aborted by user)",
    "…операция идёт уже %d с": "…operation running for %ds already",
    "операция на «%s», можно прервать кнопкой «Стоп»":
        "operation on «%s», press «Abort operation» to stop it",
    "SSH tcp/%s на %s недоступен (проверь связь/VPN)":
        "SSH tcp/%s on %s is unreachable (check connectivity/VPN)",
    "Шаг": "Step",
    "Статус": "Status",
    "— выбери сервер": "— select a server",
    "Выбери сервер слева": "Select a server on the left",
    "Выбери шаг в таблице": "Select a step in the table",
    "Выбери юзера в таблице": "Select a user in the table",
    "Идёт операция на «%s» — подожди":
        "Operation in progress on \"%s\" — please wait",
    "▶  Развернуть всё": "▶  Deploy all",
    "Импорт ключей": "Import keys",
    "Только выбранный шаг": "Selected step only",
    "Проверить статусы": "Check statuses",
    "Управление фаерволом": "Firewall management",
    "Вернуть сервер": "Revert server",
    "Перезагрузить сервер": "Reboot server",
    "Отключить сейчас": "Disconnect now",
    "Отозвать и удалить": "Revoke and delete",
    "Экспорт конфига…": "Export config…",
    "✓ готово   ⚠ предупреждение   ✗ ошибка   – пропущен   … не выполнялся":
        "✓ done   ⚠ warning   ✗ error   – skipped   … not run",
    # --- шаги деплоя ---
    "1. SSH-подключение (auth + права root/sudo)":
        "1. SSH connection (auth + root/sudo rights)",
    "2. Ключевая авторизация (генерация, если пароль)":
        "2. Key authorization (generate if password)",
    "3. Аудит ОС и окружения": "3. OS and environment audit",
    "4. Фаервол (аудит → установка/настройка)":
        "4. Firewall (audit → install/configure)",
    "5. Обновление системы (apt full-upgrade)":
        "5. System update (apt full-upgrade)",
    "6. Пакеты (OpenVPN, Easy-RSA, nftables…)":
        "6. Packages (OpenVPN, Easy-RSA, nftables…)",
    "7. OpenVPN + PKI (Easy-RSA, server.conf, mgmt)":
        "7. OpenVPN + PKI (Easy-RSA, server.conf, mgmt)",
    "8. Маршрутизация и NAT": "8. Routing and NAT",
    "9. Cloak server (маскировка, ключи)":
        "9. Cloak server (masquerade, keys)",
    "Остановился на шаге «%s». Исправь и продолжай — завершённые шаги не повторятся.":
        "Stopped at step \"%s\". Fix it and continue — completed steps won't repeat.",
    "Сверяю статусы с сервером…": "Checking statuses against the server…",
    # --- тултипы ---
    "✓ развёрнут, все шаги ok\n✓- развёрнут, но есть пропущенные шаги\n"
    "⚠ есть предупреждение/ошибка в шагах\n"
    "пусто — не развёрнут или не прошёл аудит\n"
    "SSH/Cloak: ✓ порт доступен · ✗ недоступен · "
    "… проверяется · — н/д":
        "✓ deployed, all steps ok\n✓- deployed, some steps skipped\n"
        "⚠ warning/error in steps\nempty — not deployed or audit failed\n"
        "SSH/Cloak: ✓ reachable · ✗ down · … probing · — n/a",
    "Шаги идут сверху вниз, готовые шаги — пропускаются":
        "Steps run top to bottom; completed steps are skipped",
    "Выполнить один выбранный в таблице шаг —\nточечный повтор после исправления ошибки":
        "Run one selected step from the table —\ntargeted retry after fixing an error",
    "Read-only аудит сервера (probe): сверить состояние\nи обновить значки шагов. Если сервер не отвечает —\nметка «развёрнут» снимается до успешной проверки":
        "Read-only server audit (probe): verify state\nand update step icons. If the server doesn't respond —\nthe \"deployed\" mark is cleared until a successful check",
    "Открытые порты сервера: список, открыть/закрыть\nсвой порт (nftables/ufw/firewalld/iptables)":
        "Server's open ports: list, open/close\na custom port (nftables/ufw/firewalld/iptables)",
    "Вернуть сервер к состоянию до деплоя: снести\nOpenVPN+Cloak, наши правила фаервола, юзеров\nи локальные конфиги; вернуть прежнего владельца\nпорта Cloak, docker-автозапуск и фаервол":
        "Revert the server to its pre-deploy state: removes\nOpenVPN+Cloak, our firewall rules, users and local\nconfigs; restores the previous Cloak-port owner,\ndocker autostart and firewall state",
    "  predeploy-снимок не записан: %s":
        "  pre-deploy snapshot not written: %s",
    "sudo reboot — нужно, если шаг «Обновление системы»\nпомечен ⚠ «нужна перезагрузка»":
        "sudo reboot — needed if the \"System update\" step\nis marked ⚠ \"reboot required\"",
    "Весь лог вкладки в буфер обмена": "Copy the whole tab log to clipboard",
    "Стереть лог этого сервера на этой вкладке —\nканал «Пользователи» и другие серверы не трогает":
        "Erase this server's log on this tab —\nthe Users channel and other servers are untouched",
    "Если сервер уже настроен (вручную или через DGCloak Admin)\n— эта кнопка забирает с него ключи/юзеры Cloak,\nне переустанавливая ничего. После этого сервером можно\nуправлять: юзеры, конфиги, статусы.":
        "If the server is already configured (manually or via DGCloak Admin)\n— this button pulls Cloak keys/users from it\nwithout reinstalling anything. Then the server can be\nmanaged: users, configs, statuses.",
    "Опросить сервер: UID из admin-API Cloak,\nлимиты из users.json, кто онлайн — из OpenVPN":
        "Query the server: UIDs from Cloak admin-API,\nlimits from users.json, who's online — from OpenVPN",
    "Сертификат OpenVPN + UID в Cloak;\nконфиг сохраняется в %APPDATA%\\DGCloak\\Admin\\bundles":
        "OpenVPN certificate + Cloak UID;\nconfig saved to %APPDATA%\\DGCloak\\Admin\\bundles",
    "Лимиты/срок/домен маскировки выбранного юзера;\nпри смене параметров конфиг перевыпускается":
        "Limits/expiry/mask domain of the selected user;\nconfig is reissued when parameters change",
    "Разорвать живую сессию (mgmt OpenVPN).\nЮзер остаётся и может переподключиться":
        "Break the live session (OpenVPN mgmt).\nThe user remains and can reconnect",
    "Отозвать сертификат + удалить UID из Cloak\n+ сбросить сессию. Необратимо":
        "Revoke certificate + delete UID from Cloak\n+ reset session. Irreversible",
    "Сохранить комплект подключения\n(ovpn + ключи + ck-конфиг) для выбранного юзера":
        "Save the connection bundle\n(ovpn + keys + ck config) for the selected user",
    "Служебные строки юзер-операций (ck-client, user-cert).\nСостояние запоминается для каждого сервера.":
        "Verbose lines of user operations (ck-client, user-cert).\nThe state is remembered per server.",
    "На развёрнутом сервере поле заблокировано:\nсмена порта требует пересборки конфигов юзеров":
        "Field is locked on a deployed server:\nchanging the port requires rebuilding user configs",
    "На развёрнутом сервере поле заблокировано:\nсмена протокола требует пересборки конфигов юзеров":
        "Field is locked on a deployed server:\nchanging the protocol requires rebuilding user configs",
    # --- таблица юзеров / диалоги ---
    "Имя (CN)": "Name (CN)",
    "Макс. сессий": "Max sessions",
    "Лимит ↑/↓": "Limit ↑/↓",
    "Квота ↑/↓": "Quota ↑/↓",
    "Истекает": "Expires",
    "Домен маскировки": "Mask domain",
    "Онлайн": "Online",
    "Имя (CN, [a-z0-9_-])": "Name (CN, [a-z0-9_-])",
    "Срок жизни, дней (0 = бессрочно)": "Lifetime, days (0 = forever)",
    "Макс. одновременных подключений": "Max simultaneous connections",
    "Лимит скорости ↑, Мбит/с (0 = безлимит)": "Speed limit ↑, Mbit/s (0 = unlimited)",
    "Лимит скорости ↓, Мбит/с (0 = безлимит)": "Speed limit ↓, Mbit/s (0 = unlimited)",
    "Квота трафика ↑, МБ (0 = безлимит)": "Traffic quota ↑, MB (0 = unlimited)",
    "Квота трафика ↓, МБ (0 = безлимит)": "Traffic quota ↓, MB (0 = unlimited)",
    "Домен для маскировки": "Mask domain",
    "Домен для маскировки:": "Mask domain:",
    "Срок жизни, дней": "Lifetime, days",
    "Квота трафика ↑, МБ": "Traffic quota ↑, MB",
    "Квота трафика ↓, МБ": "Traffic quota ↓, MB",
    "Лимит скорости ↑, Мбит/с": "Speed limit ↑, Mbit/s",
    "Лимит скорости ↓, Мбит/с": "Speed limit ↓, Mbit/s",
    "Приоритет: ключ OpenSSH → .ppk → пароль":
        "Priority: OpenSSH key → .ppk → password",
    "Cloak порт": "Cloak port",
    "Протокол OpenVPN:": "OpenVPN protocol:",
    "Порт:": "Port:",
    "(TCP медленнее)": "(TCP is slower)",
    # --- диалоговые сообщения ---
    "Нужны имя и хост": "Name and host are required",
    "SSH порт: число 1-65535": "SSH port: number 1-65535",
    "Порт: число 1-65535": "Port: number 1-65535",
    "Нужен ключ (.ppk/OpenSSH) или пароль": "A key (.ppk/OpenSSH) or password is required",
    "Это публичный ключ (.pub) — нужен приватный,\nобычно тот же файл без расширения .pub":
        "This is a public key (.pub) — a private one is needed,\nusually the same file without the .pub extension",
    "Имя: только латиница, цифры, _ и -": "Name: only Latin letters, digits, _ and -",
    "Поле «%s» — только целое число": "Field \"%s\" — integer only",
    "Поле «%s» — только число": "Field \"%s\" — number only",
    "Удалить сервер «%s» из списка?\n\nЛокальные конфиги юзеров удалятся.\nSSH-ключ остаётся в %s —\nим можно зайти на сервер и потом.\nСам сервер не трогаем.":
        "Remove server \"%s\" from the list?\n\nLocal user configs will be deleted.\nThe SSH key stays in %s —\nyou can still log in with it later.\nThe server itself is untouched.",
    "Перезагрузить «%s»?\n\nСервер будет недоступен ~1 минуту.":
        "Reboot \"%s\"?\n\nThe server will be unavailable for ~1 minute.",
    "Вернуть «%s» в состояние до деплоя?\n\nУдалит Cloak, OpenVPN, PKI, юзеров, наши\nправила фаервола и локальные конфиги юзеров;\nвернёт прежнего владельца порта, docker-\nавтозапуск и исходный фаервол (по снимку).\nSSH-доступ и твой юзер НЕ затрагиваются.\n\nПродолжить?":
        "Revert \"%s\" to its pre-deploy state?\n\nRemoves Cloak, OpenVPN, PKI, users, our\nfirewall rules and local user configs; restores\nthe previous port owner, docker autostart and\nthe original firewall (from the snapshot).\nSSH access and your user are NOT affected.\n\nContinue?",
    "Отозвать «%s»?\n\nСертификат отзовётся (CRL), UID удалится,\nживая сессия будет сброшена.":
        "Revoke \"%s\"?\n\nThe certificate will be revoked (CRL), UID removed,\nthe live session will be dropped.",
    "UID %s…\nне из реестра админки — CN неизвестен, сертификат и живую сессию трогать не можем.\n\nУдалить UID из Cloak? (новые подключения закроются)":
        "UID %s…\nnot in the admin registry — CN unknown, we can't touch\nthe certificate or live session.\n\nDelete UID from Cloak? (new connections will be blocked)",
    "Порт %s/%s помечен «%s».\nЗакрытие может отрезать доступ к серверу или VPN.\n\nВсё равно закрыть?":
        "Port %s/%s is marked \"%s\".\nClosing it may cut off access to the server or VPN.\n\nClose anyway?",
    "На «%s» остановленные Amnezia-контейнеры с автозапуском (%s):\nпосле перезагрузки сервера они воскреснут и могут занять порт %s.\n\nОтключить их автозапуск? (контейнеры не удаляются)":
        "\"%s\" has stopped Amnezia containers with autostart (%s):\nafter a reboot they'll come back and may grab port %s.\n\nDisable their autostart? (containers are not removed)",
    "  автозапуск amnezia-контейнеров отключён: %s":
        "  autostart disabled for amnezia containers: %s",
    "  порт %s держит «%s» — это SSH, гасить нельзя, выбираем другой порт":
        "  port %s is held by \"%s\" — that's SSH, can't quench; pick another port",
    "Порт %s занят контейнером «%s».\nЗагасить его? (остановка + отключение автозапуска,\nконтейнер НЕ удаляется)":
        "Port %s is held by container \"%s\".\nQuench it? (stop + autostart off,\ncontainer is NOT removed)",
    "  гашу контейнер «%s»…": "  quenching container \"%s\"…",
    "  контейнер «%s» загашен (stop + restart=no)":
        "  container \"%s\" quenched (stop + restart=no)",
    "Порт %s занят сервисом «%s».\nЗагасить его? (systemctl stop + отключение автозапуска)":
        "Port %s is held by service \"%s\".\nQuench it? (systemctl stop + autostart off)",
    "  гашу сервис «%s»…": "  quenching service \"%s\"…",
    "  сервис «%s» загашен (disable --now)":
        "  service \"%s\" quenched (disable --now)",
    "  порт %s держит процесс «%s» — автоматически не освободить":
        "  port %s is held by process \"%s\" — can't free it automatically",
    "Порт %s занят (%s), Cloak на него не встанет.\nУкажи другой порт:":
        "Port %s is busy (%s), Cloak can't use it.\nEnter another port:",
    "%s занят (%s)": "%s busy (%s)",
    "не удалось подобрать свободный порт":
        "couldn't find a free port",
    "  фаервол: открыт tcp/%s": "  firewall: tcp/%s opened",
    "  !! не смог открыть tcp/%s в фаерволе: %s":
        "  !! couldn't open tcp/%s in firewall: %s",
    "Полное обновление системы на «%s» (apt update + full-upgrade + autoremove)?\n\nНа свежеустановленной системе это может занять\n10–30 минут — прогресс виден в логе.\nЕсли обновление потребует перезагрузку,\nприложение предложит её в конце.":
        "Full system update on \"%s\" (apt update + full-upgrade + autoremove)?\n\nOn a fresh install this may take\n10–30 minutes — progress is shown in the log.\nIf the update requires a reboot,\nthe app will offer it at the end.",
    "Обновление системы на «%s» требует перезагрузки.\nПерезагрузить сервер сейчас?\n\n(поднимется через ~1 минуту; завершённые шаги деплоя повторять не нужно)":
        "System update on \"%s\" requires a reboot.\nReboot the server now?\n\n(it'll come up in ~1 minute; completed deploy steps don't need repeating)",
    "На «%s» свободно %d МБ на диске.\nОбновлению может не хватить места (нужно ~1 ГБ).\n\nПродолжить?":
        "\"%s\" has %d MB free on disk.\nThe update may run out of space (~1 GB needed).\n\nContinue?",
    "«%s»: %s %s не из поддерживаемых\n(Ubuntu 20.04/22.04/24.04/26.04, Debian 11/12/13).\n\nПродолжить на свой страх и риск?":
        "\"%s\": %s %s is not supported\n(Ubuntu 20.04/22.04/24.04/26.04, Debian 11/12/13).\n\nContinue at your own risk?",
    "Сервер «%s» не развёрнут (по реестру) — сделай «Развернуть всё» или «Проверить статусы»":
        "Server \"%s\" is not deployed (per registry) — run \"Deploy all\" or \"Check statuses\"",
    "Сервер «%s» не развёрнут — ключей нет.\nСначала «Развернуть всё».":
        "Server \"%s\" is not deployed — no keys.\nRun \"Deploy all\" first.",
    "Нет admin_uid/pubkey — сделай «Импорт ключей» или деплой":
        "No admin_uid/pubkey — run \"Import keys\" or deploy",
    "«%s» не из нашего реестра — править можем только своих":
        "\"%s\" is not in our registry — we can only edit our own",
    "«%s» заведён вне этой админки — экспорт конфига недоступен":
        "\"%s\" was created outside this admin — config export unavailable",
    "«%s» не онлайн — сбрасывать нечего": "\"%s\" is not online — nothing to reset",
    "CN этого юзера неизвестен — mgmt kill работает только по CN.":
        "This user's CN is unknown — mgmt kill works by CN only.",
    "Куда сохранить конфиг «%s»": "Where to save the \"%s\" config",
    "Все файлы": "All files",
    "DGCloak Admin уже запущен.": "DGCloak Admin is already running.",
    "Управление фаерволом — %s": "Firewall management — %s",
    # --- исключения / SSH-слой ---
    "Не найдены ssh/scp (Windows OpenSSH).": "ssh/scp not found (Windows OpenSSH).",
    "Не найдены plink/pscp (PuTTY). Установи PuTTY или укажи OpenSSH-ключ в настройках сервера.":
        "plink/pscp not found (PuTTY). Install PuTTY or set an OpenSSH key in server settings.",
    "Не найден ck-client.exe и не скачался (%s) — укажи путь в data.json или положи рядом":
        "ck-client.exe not found and download failed (%s) — set a path in data.json or place it alongside",
    "SSH: команда завершилась rc=%s: %s": "SSH: command exited rc=%s: %s",
    "(пустой вывод)": "(empty output)",
    "на сервере не установлен sudo.\nВ консоли VM под %s: su - (пароль root), затем\n  apt install -y sudo && /usr/sbin/usermod -aG sudo %s\nили разреши вход root по SSH и логинься как root.":
        "sudo is not installed on the server.\nIn the VM console as %s: su - (root password), then\n  apt install -y sudo && /usr/sbin/usermod -aG sudo %s\nor allow root SSH login and sign in as root.",
    "sudo отверг пароль или юзер не в sudoers.": "sudo rejected the password or the user is not in sudoers.",
    "sudo требует пароль, а пароль не задан.\nВарианты: укажи пароль юзера в настройках сервера,\nдай NOPASSWD (visudo: user ALL=(ALL) NOPASSWD:ALL)\nили логинься как root.":
        "sudo requires a password, but none is set.\nOptions: set the user password in server settings,\ngrant NOPASSWD (visudo: user ALL=(ALL) NOPASSWD:ALL)\nor sign in as root.",
    "Таймаут %s с: %s": "Timeout %s s: %s",
    "Таймаут: %s": "Timeout: %s",
    "plink: соединение прервано (255)": "plink: connection dropped (255)",
    "загрузка файла не удалась: %s": "file upload failed: %s",
    "Нет скрипта: %s": "No script: %s",
    "не найден ssh-keygen (Windows OpenSSH)": "ssh-keygen not found (Windows OpenSSH)",
    "ssh-keygen не сработал: %s": "ssh-keygen failed: %s",
    "<юзер>": "<user>",
    # --- Cloak API ---
    "ck-client -a завершился, rc=%s": "ck-client -a exited, rc=%s",
    "ck-client -a не поднял API base за %s с": "ck-client -a didn't bring up API base in %s s",
    "admin-API не запущен": "admin-API not running",
    "admin-API недоступен: %s": "admin-API unavailable: %s",
    "Cloak API %s %s → %s: %s": "Cloak API %s %s → %s: %s",
    "user-cert.sh: неполный вывод (нет CA/CERT/KEY/TA).\nХвост вывода: %s":
        "user-cert.sh: incomplete output (no CA/CERT/KEY/TA).\nOutput tail: %s",
    # --- лог: деплой ---
    "=== Проверка зависимостей ===": "=== Dependency check ===",
    "=== Шаг: %s ===": "=== Step: %s ===",
    "=== Аудит статусов на «%s» ===": "=== Status audit on \"%s\" ===",
    "=== Перезагрузка «%s» ===": "=== Rebooting \"%s\" ===",
    "=== Возврат «%s» в исходное состояние ===":
        "=== Reverting \"%s\" to pre-deploy state ===",
    "=== Проход завершён ===": "=== Pass complete ===",
    "=== Возврат завершён (rc=%s) ===": "=== Revert done (rc=%s) ===",
    "  OpenSSH-клиент: на месте": "  OpenSSH client: present",
    "  OpenSSH-клиент: установлен": "  OpenSSH client: installed",
    "  OpenSSH-клиент: НЕТ — нужен для входа по OpenSSH-ключу. Установка: Параметры → Приложения → Дополнительные компоненты → «Клиент OpenSSH», либо используй пароль/.ppk (для них хватит PuTTY)":
        "  OpenSSH client: MISSING — needed for OpenSSH-key login. Install via Settings → Apps → Optional features → \"OpenSSH Client\", or use password/.ppk (PuTTY covers those)",
    "  PuTTY (plink/pscp): на месте": "  PuTTY (plink/pscp): present",
    "  PuTTY (plink/pscp): нет — скачиваю с официального сайта…":
        "  PuTTY (plink/pscp): missing — downloading from the official site…",
    "  !! plink/pscp не скачались (%s) — вход по паролю и по .ppk не будет работать. Поставь PuTTY или используй OpenSSH-ключ":
        "  !! plink/pscp download failed (%s) — password and .ppk login won't work. Install PuTTY or use an OpenSSH key",
    "  ck-client: на месте": "  ck-client: present",
    "  ck-client: на месте (%s)": "  ck-client: present (%s)",
    "  ck-client: нашёл %s → скопировал в bin\\": "  ck-client: found %s → copied to bin\\",
    "  !! ck-client не скачался (%s) — вкладка «Пользователи» не заработает; деплой — будет":
        "  !! ck-client download failed (%s) — the Users tab won't work; deployment will",
    "  ставлю компонент «Клиент OpenSSH» (dism)…": "  installing \"OpenSSH Client\" component (dism)…",
    "  dism не сработал: %s": "  dism failed: %s",
    "  скачиваю ck-client с GitHub: %s": "  downloading ck-client from GitHub: %s",
    "  …%.1f / %.1f МБ": "  …%.1f / %.1f MB",
    "  %s → %s (%.1f МБ)": "  %s → %s (%.1f MB)",
    "%s: скачалось не-exe (%d байт)": "%s: downloaded non-exe (%d bytes)",
    "в последнем релизе Cloak нет ck-client-windows-amd64*.exe":
        "the latest Cloak release has no ck-client-windows-amd64*.exe",
    "  ключ отвергнут сервером — работаю по паролю":
        "  key rejected by the server — falling back to password",
    "  ключ отвергнут, пароль тоже не подошёл":
        "  key rejected, password didn't work either",
    "  sudo с паролем — ок": "  sudo with password — ok",
    "  генерирую ключ ed25519…": "  generating ed25519 key…",
    "  ставлю публичный ключ на сервер…": "  installing the public key on the server…",
    "  сохранённый ключ отвергнут — ставлю новый": "  saved key rejected — installing a new one",
    "вход по ключу настроен": "key login configured",
    "нет ни пароля, ни ключа": "no password and no key",
    "ключ установлен: %s": "key installed: %s",
    "не apt-дистрибутив (pkg=%s) — не поддерживается": "non-apt distro (pkg=%s) — not supported",
    "%s %s не поддерживается": "%s %s is not supported",
    "мало места на диске: %d МБ": "low disk space: %d MB",
    "мало места на диске (%d МБ)": "low disk space (%d MB)",
    "мало RAM: %d МБ свободно": "low RAM: %d MB free",
    "--- правила ---\n": "--- rules ---\n",
    "\n--- слушают снаружи (tcp) ---\n": "\n--- listening externally (tcp) ---\n",
    "чужие правила iptables — открой «Управление фаерволом»":
        "foreign iptables rules — open \"Firewall management\"",
    "фаервола нет — сначала выполни шаг «Фаервол»":
        "no firewall — run the \"Firewall\" step first",
    "неизвестный фаервол: %s": "unknown firewall: %s",
    "  фаервола нет — ставлю nftables (INPUT DROP + ssh %s + cloak tcp/%s; откат через 120 с при потере SSH)":
        "  no firewall — installing nftables (INPUT DROP + ssh %s + cloak tcp/%s; 120 s rollback if SSH is lost)",
    "  firewall подтверждён (rollback отменён)": "  firewall confirmed (rollback cancelled)",
    "обновлено ранее, нужна перезагрузка": "updated earlier, reboot required",
    "отменено пользователем": "cancelled by user",
    "обновлено, нужна перезагрузка": "updated, reboot required",
    "обновлено": "updated",
    "обновлено, перезагружен": "updated, rebooted",
    "не встали: %s": "failed to install: %s",
    "все пакеты есть; cloak latest: %s": "all packages present; cloak latest: %s",
    "  ставлю недостающие пакеты: %s": "  installing missing packages: %s",
    "NAT через %s, Cloak tcp/%s открыт": "NAT via %s, Cloak tcp/%s open",
    "NAT ok (%s), но порт Cloak %s/tcp не открылся: %s": "NAT ok (%s), but Cloak port %s/tcp didn't open: %s",
    "%s занят, чистка отменена": "%s busy, cleanup cancelled",
    "%s занят чужим сервисом: %s": "%s busy with a foreign service: %s",
    "ключи получены, маскировка %s": "keys received, masquerade %s",
    "нет PUB/ADMIN_UID в выводе deploy-cloak": "no PUB/ADMIN_UID in deploy-cloak output",
    "  probe не прошёл (%s) — иду с первого шага": "  probe failed (%s) — starting from the first step",
    "  метку «развёрнут» снял — сервер не отвечает, состояние не проверено":
        "  cleared the \"deployed\" mark — server unresponsive, state unverified",
    "  отправляю reboot…": "  sending reboot…",
    "  сервер поднялся после перезагрузки": "  server is back up after reboot",
    "  !! сервер не перезагрузился за 3 минуты (или флаг reboot-required остался) — проверь консоль VM":
        "  !! server didn't reboot within 3 minutes (or reboot-required flag remains) — check the VM console",
    "  …выполняется уже %d мин %d с — процесс жив, ждём ответа сервера":
        "  …running for %d min %d s — process alive, waiting for server reply",
    "  вывод %d байт": "  %d bytes of output",
    "  ОШИБКА: %s": "  ERROR: %s",
    "ОШИБКА: %s": "ERROR: %s",
    # --- лог: юзеры ---
    "  admin-API: рестарт ck-client (%s)": "  admin-API: restarting ck-client (%s)",
    "  реестр юзеров с сервера не прочитан: %s": "  couldn't read user registry from server: %s",
    "  реестр юзеров синхронизирован на сервер": "  user registry synced to server",
    "  !! реестр на сервер не записался: %s": "  !! registry write to server failed: %s",
    "  конфиг «%s» → user_bundles\\%s": "  config \"%s\" → user_bundles\\%s",
    "  конфиг передан клиенту: «%s»": "  config handed to client: \"%s\"",
    "  профиль «%s» удалён и у клиента":
        "  profile \"%s\" removed from client too",
    "  !! конфиг «%s» не пересобран: %s": "  !! config \"%s\" not rebuilt: %s",
    "  сертификат отозван": "  certificate revoked",
    "  сертификат уже отозван": "  certificate already revoked",
    "  сертификата на сервере нет — пропускаю отзыв": "  no certificate on server — skipping revocation",
    "  сессия сброшена": "  session dropped",
    "  сессии не было (юзер офлайн)": "  no session (user offline)",
    "  UID удалён": "  UID deleted",
    "  UID в Cloak уже нет": "  UID already gone from Cloak",
    "  mgmt включён": "  mgmt enabled",
    "  mgmt не отвечает (%s) — включаю enable-mgmt…": "  mgmt not responding (%s) — enabling enable-mgmt…",
    "  mgmt недоступен: %s": "  mgmt unavailable: %s",
    "  mgmt так и недоступен: %s": "  mgmt still unavailable: %s",
    "  старый ключ %s удалён": "  old key %s deleted",
    "сброс сессии %s: %s": "session reset %s: %s",
    "…идёт операция на «%s» — её вывод пишется в журнал этого сервера…\n\n":
        "…operation in progress on \"%s\" — its output goes to that server's log…\n\n",
    "%s — кэш админки, «Обновить» покажет данные с сервера":
        "%s — admin cache, \"Refresh\" will pull server data",
    "%s — актуальные данные с сервера": "%s — live data from server",
    "Юзеров: %s, онлайн: %s": "Users: %s, online: %s",
    "Юзер «%s» создан, выдай конфиг юзеру: %s": "User \"%s\" created, hand over the config: %s",
    "Юзер «%s» обновлён (лимиты/срок — на сервере, конфиг тот же)":
        "User \"%s\" updated (limits/expiry on server, same config)",
    "Юзер «%s» обновлён, конфиг перевыпущен, выдай его юзеру: %s":
        "User \"%s\" updated, config reissued, hand it to the user: %s",
    "Юзер «%s» без изменений": "User \"%s\" unchanged",
    "Юзер «%s» отозван и удалён.": "User \"%s\" revoked and deleted.",
    "Юзер «%s» уже существует на «%s»": "User \"%s\" already exists on \"%s\"",
    "Сессия «%s» сброшена": "Session of \"%s\" dropped",
    "UID %s… удалён из Cloak": "UID %s… deleted from Cloak",
    "Конфиг «%s» → %s": "Config \"%s\" → %s",
    "Импорт: ключи подтянуты с «%s»%s": "Import: keys pulled from \"%s\"%s",
    ", юзеров восстановлено: %d": ", users recovered: %d",
    "!! probe.sh не вернул данных": "!! probe.sh returned no data",
    "!! Не нашёл /etc/ck-server/* — сервер развёрнут?": "!! /etc/ck-server/* not found — is the server deployed?",
    # --- фаервол-диалог ---
    "Фаервол: %s\n\nПорты, открытые снаружи:\n\n": "Firewall: %s\n\nExternally open ports:\n\n",
    "   (нет открытых портов)": "   (no open ports)",
    "\n\nОстальные входящие соединения закрыты.": "\n\nAll other inbound connections are closed.",
    "SSH — не удалять": "SSH — do not remove",
    "Cloak VPN — не удалять": "Cloak VPN — do not remove",
    "DHCP-клиент — не удалять": "DHCP client — do not remove",
    "DHCPv6-клиент — не удалять": "DHCPv6 client — do not remove",
    "пользовательский порт": "custom port",
    "открыт": "opened",
    "закрыт": "closed",
    "fw: порт %s/%s %s, бэкенд %s%s": "fw: port %s/%s %s, backend %s%s",
    # --- формат-юниты ---
    "%dк": "%dk",
    "%gМ": "%gM",
    # --- заметки probe.sh (токены → текст) ---
    "ключей в authorized_keys: %s": "keys in authorized_keys: %s",
    "authorized_keys пуст/отсутствует": "authorized_keys is empty/missing",
    "фаервола нет": "no firewall",
    "%s; открыто: %s": "%s; open: %s",
    "нужна перезагрузка": "reboot required",
    "apt update не выполнялся": "apt update never ran",
    "не установлено обновлений: %s": "pending updates: %s",
    "система обновлена, ребут не требуется": "system up to date, no reboot needed",
    "не хватает: %s": "missing: %s",
    "все пакеты есть": "all packages present",
    "proto=%s mgmt:%s": "proto=%s mgmt:%s",
    "openvpn-server@server не активен": "openvpn-server@server is not active",
    "forward+masquerade на месте": "forward+masquerade in place",
    "forward=1, masquerade 10.8.0.0/24 не найден":
        "forward=1, no masquerade for 10.8.0.0/24",
    "masquerade есть, нет forward-правил tun0":
        "masquerade present, but no tun0 forwarding rules",
    "ip_forward=0": "ip_forward=0",
    "NAT/форвардинг не подтвердился (masq=%s, fwd_rules=%s)":
        "NAT/forwarding not confirmed (masq=%s, fwd_rules=%s)",
    "active, tcp/%s слушает, маскировка %s": "active, tcp/%s listening, mask %s",
    "active, но порт %s не слушает/нет конфига": "active, but port %s not listening/no config",
    "ck-server не активен": "ck-server is not active",
    "ck-server не активен, tcp/%s занят %s:%s":
        "ck-server is not active, tcp/%s held by %s:%s",
    "Язык интерфейса: %s": "Interface language: %s",
    "Завершил процессы от прошлого запуска: ":
        "Terminated leftover processes from previous run: ",
    "Имя": "Name",
    "SSH": "SSH",
    "Cloak": "Cloak",
    "Поднять в списке": "Move up",
    "Опустить в списке": "Move down",
    "Уже написанное остаётся на прежнем языке:\n"
    "записи в логе и статусы/заметки шагов деплоя.\n\n"
    "Чтобы обновить статусы шагов — нажми\n"
    "«Проверить статусы».":
        "Already-written content stays in the previous language:\n"
        "log entries and deployment step statuses/notes.\n\n"
        "To refresh step statuses, click\n"
        "\"Check statuses\".",
    "«%s» — сейчас это сервер твоего АКТИВНОГО VPN.\n\n"
    "SSH-сессия идёт через этот же туннель: при его разрыве\n"
    "(перезапуск OpenVPN/Cloak, смена фаервола, ребут, сброс)\n"
    "связь оборвётся и операция зависнет или не завершится.\n\n"
    "Рекомендуется отключить VPN. Продолжить?":
        "\"%s\" is the server of your ACTIVE VPN connection.\n\n"
        "The SSH session goes through this same tunnel: if it drops\n"
        "(OpenVPN/Cloak restart, firewall change, reboot, purge),\n"
        "the connection will break and the operation may hang.\n\n"
        "Disconnecting the VPN first is recommended. Continue?",
}

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

OVPN_TEMPLATE = """client
dev tun
proto {proto}
remote 127.0.0.1 1984
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
auth SHA256
verb 3
mute-replay-warnings
<ca>
{ca}
</ca>
<cert>
{cert}
</cert>
<key>
{key}
</key>
<tls-crypt>
{ta}
</tls-crypt>
"""


# ---------------------------------------------------------------- utilities

def _move_file(src, dst):
    """Переложить файл в общий bin; если там уже есть — удалить дубликат
    (дедупликация deps — цель и есть одна копия на обе программы)."""
    try:
        if not os.path.isfile(src):
            return
        if os.path.isfile(dst):
            os.remove(src)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
    except OSError:
        pass


def _rewrite_paths(obj, old, new):
    """Префикс старой папки → новой внутри data.json (ключи, бандлы)."""
    if isinstance(obj, str):
        return new + obj[len(old):] if obj.startswith(old) else obj
    if isinstance(obj, list):
        return [_rewrite_paths(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {k: _rewrite_paths(v, old, new) for k, v in obj.items()}
    return obj


def migrate_dirs():
    """Переезд %APPDATA%\\DGCloakAdmin → DGCloak\\Admin\\, бинарники —
    в общий DGCloak\\bin\\ (клиент мигрирует свою папку сам при старте).
    В тестах (APP_DIR пропатчен) — no-op."""
    if APP_DIR != os.path.join(DGCLOAK_DIR, "Admin"):
        return
    if os.path.isdir(OLD_APP_DIR) and not os.path.isdir(APP_DIR):
        try:
            os.makedirs(DGCLOAK_DIR, exist_ok=True)
            shutil.move(OLD_APP_DIR, APP_DIR)
        except OSError:
            return
        # пути в реестре: старый префикс → новый
        try:
            d = load_data()
            d = _rewrite_paths(d, OLD_APP_DIR + os.sep, APP_DIR + os.sep)
            save_data(d)
        except Exception:
            pass
    # Admin\bin → общий bin (и при миграции, и для чистки хвостов,
    # если общий bin оказался занят раньше — дубликаты удаляются)
    old_bin = os.path.join(APP_DIR, "bin")
    if os.path.isdir(old_bin):
        for f in os.listdir(old_bin):
            _move_file(os.path.join(old_bin, f), os.path.join(BIN_DIR, f))
        try:
            os.rmdir(old_bin)
        except OSError:
            pass
    _move_file(os.path.join(APP_DIR, "ck-client.exe"),
               os.path.join(BIN_DIR, "ck-client.exe"))
    # ck-client старого клиента → в общий bin (копия — старый клиент
    # не ломаем; новый при своей миграции приберёт свою папку)
    try:
        src = os.path.join(OLD_VPN_DIR, "ck-client.exe")
        dst = os.path.join(BIN_DIR, "ck-client.exe")
        if os.path.isfile(src) and not os.path.isfile(dst):
            os.makedirs(BIN_DIR, exist_ok=True)
            shutil.copy2(src, dst)
    except OSError:
        pass
    _migrate_layout()


def _key_remap():
    """{старый путь: новый} для переезда keys\\<srv>_ed25519 → keys\\<srv>\\key."""
    kd = os.path.join(APP_DIR, "keys")
    out = {}
    if os.path.isdir(kd):
        for f in os.listdir(kd):
            if f.endswith("_ed25519"):
                out[os.path.join(kd, f)] = \
                    os.path.join(kd, f[:-len("_ed25519")], "key")
    return out


def _migrate_layout():
    """Плоская раскладка: keys\\<srv>\\key(.pub) и
    user_bundles\\<srv>\\<юзер>.dgcloak (бывш. bundles\\<srv>\\<юзер>\\)."""
    # ключи
    remap = _key_remap()
    for old, new in remap.items():
        os.makedirs(os.path.dirname(new), exist_ok=True)
        _move_file(old, new)
        _move_file(old + ".pub", new + ".pub")
    if remap:
        try:  # пути ключей в реестре следом за файлами
            d = load_data()
            for s in d.get("servers", []):
                if s.get("key") in remap:
                    s["key"] = remap[s["key"]]
            save_data(d)
        except Exception:
            pass
    # бандлы: bundles → user_bundles, внутри — сплющить <cn>\<cn>.dgcloak
    old_bd = os.path.join(APP_DIR, "bundles")
    if os.path.isdir(old_bd) and not os.path.isdir(BUNDLES_DIR):
        try:
            shutil.move(old_bd, BUNDLES_DIR)
        except OSError:
            pass
    if os.path.isdir(BUNDLES_DIR):
        for srv in os.listdir(BUNDLES_DIR):
            sd = os.path.join(BUNDLES_DIR, srv)
            if not os.path.isdir(sd):
                continue
            for sub in list(os.listdir(sd)):
                p = os.path.join(sd, sub)
                if os.path.isdir(p):      # старая схема: папка на юзера
                    for f in os.listdir(p):
                        _move_file(os.path.join(p, f), os.path.join(sd, f))
                    try:
                        os.rmdir(p)
                    except OSError:
                        pass


def load_data():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
            d.setdefault("servers", [])
            return d
    except Exception:
        return {"servers": []}


def save_data(data):
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def find_exe(names, extra_dirs=None):
    """Ищет exe в extra_dirs и PATH."""
    import shutil
    dirs = list(extra_dirs or []) + os.environ.get("PATH", "").split(os.pathsep)
    for d in dirs:
        if not d:
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    for n in names:
        w = shutil.which(n)
        if w:
            return w
    return None


def find_putty():
    dirs = [BIN_DIR, os.path.join(OLD_APP_DIR, "bin"),  # до миграции
            r"C:\Program Files\PuTTY", r"C:\Program Files (x86)\PuTTY"]
    plink = find_exe(["plink.exe", "plink"], dirs)
    pscp = find_exe(["pscp.exe", "pscp"], dirs)
    return plink, pscp


PUTTY_DL = "https://the.earth.li/~sgtatham/putty/latest/w64/%s"


def download_putty(say):
    """plink/pscp — одиночные exe с официального сайта PuTTY → BIN_DIR."""
    os.makedirs(BIN_DIR, exist_ok=True)
    for name in ("plink.exe", "pscp.exe"):
        req = urllib.request.Request(PUTTY_DL % name,
                                     headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
        if not blob.startswith(b"MZ"):
            raise RuntimeError(T("%s: скачалось не-exe (%d байт)")
                               % (name, len(blob)))
        p = os.path.join(BIN_DIR, name)
        with open(p, "wb") as f:
            f.write(blob)
        say(T("  %s → %s (%.1f МБ)") % (name, p, len(blob) / 1e6))


def install_openssh(say):
    """OpenSSH-клиент — это Windows-компонент; ставится только из-под
    админа. Возвращает True, если ssh.exe появился."""
    try:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return False
        say(T("  ставлю компонент «Клиент OpenSSH» (dism)…"))
        p = subprocess.run(
            ["dism", "/Online", "/Add-Capability",
             "/CapabilityName:OpenSSH.Client~~~~0.0.1.0"],
            capture_output=True, text=True, timeout=600,
            creationflags=CREATE_NO_WINDOW)
        say("  dism: %s" % (p.stdout or p.stderr).strip().splitlines()[-1][:120])
    except Exception as e:
        say(T("  dism не сработал: %s") % e)
    return bool(find_exe(["ssh.exe", "ssh"],
                         [r"C:\Windows\System32\OpenSSH"]))


def find_ck_client(data):
    cands = [data.get("ck_client") or "",
             os.path.join(BIN_DIR, "ck-client.exe"),
             os.path.join(APP_DIR, "ck-client.exe"),
             os.path.join(OLD_APP_DIR, "bin", "ck-client.exe"),
             os.path.join(DGCLOAK_DIR, "VPN", "ck-client.exe"),
             os.path.join(OLD_VPN_DIR, "ck-client.exe")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return find_exe(["ck-client.exe", "ck-client-windows-amd64.exe", "ck-client"],
                    [BASE_DIR])


CLOAK_API = "https://api.github.com/repos/cbeuw/Cloak/releases/latest"


def cloak_release_url():
    """Прямая ссылка на свежий ck-client-windows-amd64*.exe."""
    req = urllib.request.Request(CLOAK_API, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=20) as r:
        meta = json.loads(r.read().decode("utf-8"))
    for a in meta.get("assets", []):
        if re.match(r"ck-client-windows-amd64.*\.exe$", a.get("name", "")):
            return a["browser_download_url"], int(a.get("size") or 0)
    raise RuntimeError(T("в последнем релизе Cloak нет ck-client-windows-amd64*.exe"))


def download_ck_client(say):
    """Скачать ck-client.exe в BIN_DIR (как plink/pscp — все внешние
    инструменты в одном месте)."""
    url, size = cloak_release_url()
    dst = os.path.join(BIN_DIR, "ck-client.exe")
    os.makedirs(BIN_DIR, exist_ok=True)
    say(T("  скачиваю ck-client с GitHub: %s") % url.split("/")[-1])
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    done = 0
    next_mark = 1024 * 1024
    with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if done >= next_mark:
                say(T("  …%.1f / %.1f МБ") % (done / 1e6, (size or done) / 1e6))
                next_mark = done + 1024 * 1024
    return dst


def valid_cn(name):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", name or ""))


def uid_to_b64url(uid):
    return uid.replace("+", "-").replace("/", "_")


class Tooltip:
    """Простой всплывающий текст при наведении на виджет."""

    def __init__(self, widget, text):
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _e=None):
        if self.tip:
            return
        wgt = _e.widget if _e else None
        if wgt is None:
            return
        x = wgt.winfo_rootx() + 16
        y = wgt.winfo_rooty() + wgt.winfo_height() + 4
        tw = tk.Toplevel(wgt)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self.text, bg="#ffffd8", fg="#222",
                 relief="solid", bd=1, padx=6, pady=4,
                 font=("", 9), justify="left").pack()
        tw.update_idletasks()
        # у нижнего края экрана показываем подсказку над виджетом
        if y + tw.winfo_reqheight() > wgt.winfo_screenheight():
            y = wgt.winfo_rooty() - tw.winfo_reqheight() - 4
        tw.geometry("+%d+%d" % (x, y))
        self.tip = tw

    def _hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ---------------------------------------------------------------- SSH layer

class SSHErr(Exception):
    pass


class SSH:
    """SSH-обёртка для одного сервера.

    Бэкенды:
      - srv["ppk"]      → plink/pscp (PuTTY)
      - srv["key"]      → ssh.exe/scp.exe (Windows OpenSSH, ключ OpenSSH-формата)
      - srv["password"] → plink -pw
    """

    # Общий флаг отмены на текущую операцию: кнопка «Прервать» выставляет его,
    # а цикл ожидания процесса в _run_proc убивает запущенный plink/ssh.
    op_cancel = threading.Event()

    # host:port, для которых сервер в этом процессе уже отверг сохранённый
    # ключ (VM откачена/пересоздана). Каждый шаг создаёт новый SSH-объект —
    # без метки каждый заново тычет мёртвым ключом; на OpenSSH ≥9.8
    # (PerSourcePenalties) серия authfail роняет следующие соединения
    # с нашего IP — plink получает «Connection reset by peer» посреди деплоя.
    _auth_pw_hosts = set()

    def _hp(self):
        return "%s:%s" % (self.srv.get("host"), self.srv.get("ssh_port") or 22)

    def _pw_forced(self):
        """Сервер уже отверг ключ — сразу парольный бэкенд."""
        return self._hp() in SSH._auth_pw_hosts

    def __init__(self, srv, log):
        self.t = T
        self.srv = srv
        self.log = log
        self._probed = False  # первый _spawn делает быстрый TCP-чек порта
        self.plink = self.pscp = self.ssh_exe = self.scp_exe = None
        # root не имеет sudo на Debian-minimal; для него префикс не нужен.
        # -n: без пароля → сразу ошибка, а не подвисший промпт.
        # Режим sudo с паролем (sudo -S) определяет preflight() → self.sudo_pw.
        self.sudo = "" if srv.get("user") == "root" else "sudo -n "
        self.sudo_pw = None  # пароль sudo, если юзер с sudo по паролю
        # режим sudo, найденный preflight, кэшируется в srv["sudo_mode"] —
        # каждый шаг создаёт новый SSH-объект, без кэша pw-режим терялся бы
        if srv.get("sudo_mode") == "pw" and srv.get("password"):
            self.sudo = "sudo -S -p '' "
            self.sudo_pw = srv["password"]
        # ключ отвергнут сервером (VM пересоздана) → с этого момента
        # вся работа по паролю через plink, пока объект жив
        self._auth_pw = False
        if srv.get("key"):
            self.backend = "openssh"
            # Системный OpenSSH приоритетнее Git-овского из PATH: MSYS2-ssh
            # закрывает канал по EOF stdin → вывод скриптов с easyrsa
            # обрезается после последнего чтения stdin.
            win_ssh = [r"C:\Windows\System32\OpenSSH"]
            self.ssh_exe = find_exe(["ssh.exe", "ssh"], win_ssh)
            self.scp_exe = find_exe(["scp.exe", "scp"], win_ssh)
            if not self.ssh_exe or not self.scp_exe:
                raise SSHErr(self.t("Не найдены ssh/scp (Windows OpenSSH)."))
        else:
            self.backend = "putty"
            self.plink, self.pscp = find_putty()
            if not self.plink or not self.pscp:
                raise SSHErr(self.t("Не найдены plink/pscp (PuTTY). "
                             "Установи PuTTY или укажи OpenSSH-ключ в настройках сервера."))

    def _target(self):
        return "%s@%s" % (self.srv.get("user", "ubuntu"), self.srv["host"])

    def _find_putty(self):
        if not self.plink:
            self.plink, self.pscp = find_putty()
        return self.plink, self.pscp

    def _argv(self, cmd):
        if (self.backend == "openssh" and not self._auth_pw
                and self.srv.get("key") and not self._pw_forced()):
            # -n (stdin=/dev/null) ломает sudo -S: пароль не доедет.
            # Отключаем, когда есть пароль юзера (потенциально нужен sudo -S).
            no_stdin = [] if self.srv.get("password") else ["-n"]
            return [self.ssh_exe] + no_stdin + ["-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=15",
                    # провайдер может тихо резать сессию — keepalive ловит
                    # это за ~30с вместо ожидания полного таймаута команды
                    "-o", "ServerAliveInterval=15",
                    "-o", "ServerAliveCountMax=2",
                    "-i", self.srv["key"], "-p", str(self.srv.get("ssh_port", 22)),
                    self._target(), cmd]
        plink, _ = self._find_putty()
        a = [plink, "-batch"]
        if self.srv.get("ppk") and not self._auth_pw:
            a += ["-i", self.srv["ppk"]]
        elif self.srv.get("password"):
            a += ["-pw", self.srv["password"]]
        a += ["-P", str(self.srv.get("ssh_port", 22)), self._target(), cmd]
        return a

    def _argv_upload(self, local_path, remote_path):
        if (self.backend == "openssh" and not self._auth_pw
                and self.srv.get("key") and not self._pw_forced()):
            return [self.scp_exe, "-B", "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ServerAliveInterval=15",
                    "-o", "ServerAliveCountMax=2",
                    "-i", self.srv["key"], "-P", str(self.srv.get("ssh_port", 22)),
                    local_path, "%s:%s" % (self._target(), remote_path)]
        _, pscp = self._find_putty()
        a = [pscp, "-batch"]
        if self.srv.get("ppk") and not self._auth_pw:
            a += ["-i", self.srv["ppk"]]
        elif self.srv.get("password"):
            a += ["-pw", self.srv["password"]]
        a += ["-P", str(self.srv.get("ssh_port", 22)),
              local_path, "%s:%s" % (self._target(), remote_path)]
        return a

    def _run_proc(self, args, input_text, timeout):
        """Popen с полл-циклом: честный таймаут И мгновенная отмена
        (SSH.op_cancel → убиваем процесс, не ждём весь timeout)."""
        p = track_proc(subprocess.Popen(
            args, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW))
        t0 = time.time()
        first = True
        try:
            while True:
                try:
                    # input только в первом communicate — после TimeoutExpired
                    # повторяем без него, иначе RuntimeError
                    out, _ = p.communicate(
                        input=(input_text or "") if first else None,
                        timeout=0.25)
                    return p.returncode, out or ""
                except subprocess.TimeoutExpired:
                    first = False
                    if SSH.op_cancel.is_set():
                        p.kill()
                        out, _ = p.communicate()
                        return 130, (out or "") + self.t("(прервано пользователем)")
                    if time.time() - t0 > timeout:
                        p.kill()
                        out, _ = p.communicate()
                        return 124, (out or "") + "TIMEOUT after %ss" % timeout
        finally:
            untrack_proc(p)

    def _try(self, args, input_text, timeout):
        """Один запуск + TOFU retry по fingerprint для plink-семейства."""
        # stdin=PIPE обязателен: в --noconsole exe нет консольного stdin,
        # наследование битого хэндла роняет ssh/scp молча с пустым выводом
        rc, out = self._run_proc(args, input_text, timeout)
        base = os.path.basename(args[0]).lower()
        if (base.startswith(("plink", "pscp")) and rc != 0
                and "host key" in out and "-hostkey" not in args):
            # plink в нон-консоли не читает 'y' со stdin — достаём fingerprint
            # из текста промпта и повторяем с -hostkey (TOFU, без интерактива)
            fp = re.search(r"fingerprint is:\s*\S+\s+\d+\s+(SHA256:\S+)", out)
            if fp:
                # -hostkey — опция, должна стоять ДО host (иначе уедет в remote-команду)
                rc, out = self._run_proc(args[:-2] + ["-hostkey", fp.group(1)]
                                         + args[-2:], input_text, timeout)
        return rc, out

    def _spawn(self, args, input_text=None, timeout=180):
        """Запуск с фолбэком: ключ openssh отвергнут сервером
        (VM пересоздана/снапшот откачен) → повтор по паролю через plink."""
        # Быстрый TCP-чек порта перед первым запуском: при мёртвом хосте
        # отвечаем за секунды вместо серии plink-таймаутов по 180с
        if not self._probed:
            self._probed = True
            port = int(self.srv.get("ssh_port") or 22)
            try:
                socket.create_connection((self.srv["host"], port), 4).close()
            except OSError:
                return 1, self.t("SSH tcp/%s на %s недоступен "
                                 "(проверь связь/VPN)") % (port,
                                                          self.srv["host"])
        rc, out = self._try(args, input_text, timeout)
        if (rc != 0 and self.backend == "openssh"
                and self.srv.get("password")
                and "Permission denied" in out):
            plink, pscp = self._find_putty()
            if plink and pscp:
                exe = pscp if os.path.basename(args[0]).lower().startswith("scp") \
                      else plink
                alt = [exe, "-batch", "-pw", self.srv["password"],
                       "-P", str(self.srv.get("ssh_port", 22))] + args[-2:]
                rc2, out2 = self._try(alt, input_text, timeout)
                if re.search(r"(?i)unable to authenticate|no supported "
                             r"authentication|access denied|fatal error",
                             out2 or ""):
                    # и пароль не пустили — показываем ошибку plink
                    self.log(self.t("  ключ отвергнут, пароль тоже не подошёл"))
                    return rc2, out2
                # подключились: ключ мёртв → весь объект дальше по паролю.
                # rc2 != 0 здесь — ошибка КОМАНДЫ на сервере, её и возвращаем.
                # Метка на host:port — чтобы следующие SSH-объекты (другие
                # шаги) не тыкали мёртвый ключ (PerSourcePenalties).
                self._auth_pw = True
                SSH._auth_pw_hosts.add(self._hp())
                self.log(self.t("  ключ отвергнут сервером — работаю по паролю"))
                return rc2, out2
        return rc, out

    def run(self, cmd, timeout=120):
        args = self._argv(cmd)
        # в режиме sudo-по-паролю шлём пароль в stdin — его прочитает sudo -S
        inp = (self.sudo_pw + "\n") if self.sudo_pw else ""
        rc, out = self._spawn(args, input_text=inp, timeout=timeout)
        if rc != 0:
            raise SSHErr(self.t("SSH: команда завершилась rc=%s: %s")
                         % (rc, out.strip()[:400] or self.t("(пустой вывод)")))
        return out

    def preflight(self):
        """Быстрый гейт перед деплоем: SSH жив + root или sudo.
        sudo бывает трёх видов: NOPASSWD, по паролю, недоступен.
        По паролю работаем через sudo -S + пароль в stdin (pw не в argv —
        не светится в ps на сервере)."""
        out = self.run("echo PF:$(id -u):$("
                       "command -v sudo >/dev/null 2>&1 && "
                       "{ sudo -n true 2>/dev/null && echo np || echo nop; } || "
                       "echo missing)", timeout=30)
        if "PF:0:" in out:
            self.sudo = ""
            self.srv["sudo_mode"] = "root"
            return
        if ":missing" in out:
            raise SSHErr(
                self.t("на сервере не установлен sudo.\n"
                "В консоли VM под %s: su - (пароль root), затем\n"
                "  apt install -y sudo && /usr/sbin/usermod -aG sudo %s\n"
                "или разреши вход root по SSH и логинься как root.")
                % (self.srv.get("user", self.t("<юзер>")),
                   self.srv.get("user", self.t("<юзер>"))))
        if ":np" in out:
            self.sudo = "sudo -n "
            self.srv["sudo_mode"] = "np"
            return
        pw = self.srv.get("password")
        if pw:
            # -kS: принудительный промпт → пароль со stdin; проверяем один раз
            rc, _o = self._spawn(self._argv("sudo -kS -p '' true"),
                                 input_text=pw + "\n", timeout=30)
            if rc == 0:
                self.sudo = "sudo -S -p '' "
                self.sudo_pw = pw
                self.srv["sudo_mode"] = "pw"
                self.log(self.t("  sudo с паролем — ок"))
                return
            raise SSHErr(self.t("sudo отверг пароль или юзер не в sudoers."))
        raise SSHErr(self.t("sudo требует пароль, а пароль не задан.\n"
                     "Варианты: укажи пароль юзера в настройках сервера,\n"
                     "дай NOPASSWD (visudo: user ALL=(ALL) NOPASSWD:ALL)\n"
                     "или логинься как root."))

    def run_stream(self, cmd, on_line, timeout=None):
        """Стриминг stdout+stderr построчно (для долгих деплой-скриптов)."""
        # тот же быстрый TCP-чек, что в _spawn: мёртвый хост → отказ за секунды
        if not self._probed:
            self._probed = True
            port = int(self.srv.get("ssh_port") or 22)
            try:
                socket.create_connection((self.srv["host"], port), 4).close()
            except OSError:
                raise SSHErr(self.t("SSH tcp/%s на %s недоступен "
                                    "(проверь связь/VPN)")
                             % (port, self.srv["host"]))
        args = self._argv(cmd)
        p = track_proc(subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.PIPE if self.sudo_pw else subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW))
        if self.sudo_pw:
            try:
                p.stdin.write(self.sudo_pw + "\n")
                p.stdin.close()
            except (OSError, ValueError):
                pass
        t0 = time.time()
        assert p.stdout is not None
        seen = []
        try:
            for line in p.stdout:
                if SSH.op_cancel.is_set():
                    p.kill()
                    raise SSHErr(self.t("(прервано пользователем)"))
                seen.append(line)
                on_line(line.rstrip("\n"))
                if timeout and time.time() - t0 > timeout:
                    p.kill()
                    raise SSHErr(self.t("Таймаут %s с: %s") % (timeout, cmd[:80]))
            p.wait(timeout=10)
        finally:
            untrack_proc(p)
        out_tail = ""
        is_plink = os.path.basename(args[0]).lower().startswith("plink")
        if is_plink and p.returncode == 255:
            # ищем fingerprint в уже увиденном выводе → retry с -hostkey
            mfp = re.search(r"fingerprint is:\s*\S+\s+\d+\s+(SHA256:\S+)",
                            "".join(seen))
            fp = mfp.group(1) if mfp else None
            if fp:
                args2 = args[:-2] + ["-hostkey", fp] + args[-2:]
            else:
                args2 = [a for a in args if a != "-batch"]
            # stdin: пароль sudo (pw-режим) > 'y' для host-key промпта
            if self.sudo_pw:
                inp2, need_pipe = self.sudo_pw + "\n", True
            elif fp:
                inp2, need_pipe = None, False
            else:
                inp2, need_pipe = "y\n", True
            p2 = track_proc(subprocess.Popen(
                args2, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
                stdin=subprocess.PIPE if need_pipe else subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW))
            try:
                out, _ = p2.communicate(input=inp2,
                                        timeout=timeout or 300)
            except subprocess.TimeoutExpired:
                p2.kill()
                raise SSHErr(self.t("Таймаут: %s") % cmd[:80])
            finally:
                untrack_proc(p2)
            for line in (out or "").splitlines():
                on_line(line)
            out_tail = out or ""
        if is_plink and p.returncode == 255 and not out_tail:
            raise SSHErr(self.t("plink: соединение прервано (255)"))
        return p.returncode

    def install_pubkey(self, pubkey):
        """Поставить публичный ключ в ~/.ssh/authorized_keys текущей
        авторизацией (обычно пароль через plink)."""
        safe = pubkey.strip().replace("'", "")
        cmd = ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
               "touch ~/.ssh/authorized_keys && "
               "grep -qxF '%s' ~/.ssh/authorized_keys || "
               "echo '%s' >> ~/.ssh/authorized_keys; "
               "chmod 600 ~/.ssh/authorized_keys") % (safe, safe)
        self.run(cmd, timeout=30)

    def upload(self, local_path, remote_path):
        args = self._argv_upload(local_path, remote_path)
        rc, out = self._spawn(args, timeout=60)
        if rc != 0:
            raise SSHErr(self.t("загрузка файла не удалась: %s")
                         % out.strip()[:300])
        return out

    def run_script(self, filename, args="", timeout=600):
        """Залить скрипт из scripts/ в /tmp и выполнить под sudo bash."""
        local = os.path.join(SCRIPTS_DIR, filename)
        if not os.path.isfile(local):
            raise SSHErr(self.t("Нет скрипта: %s") % local)
        remote = "/tmp/dgadm-%s" % filename
        self.upload(local, remote)
        # CRLF-страховка: скрипты редактируются на Windows.
        # DG_LANG — язык вывода скрипта (env через sudo, env_keep не нужен).
        return self.run("sed -i 's/\\r$//' %s && %senv DG_LANG=%s bash %s %s"
                        % (remote, self.sudo, _LANG, remote, args),
                        timeout=timeout)

    def run_script_stream(self, filename, args, on_line, timeout=900):
        local = os.path.join(SCRIPTS_DIR, filename)
        remote = "/tmp/dgadm-%s" % filename
        self.upload(local, remote)
        return self.run_stream(
            "sed -i 's/\\r$//' %s && %senv DG_LANG=%s bash %s %s"
            % (remote, self.sudo, _LANG, remote, args),
            on_line, timeout=timeout)


# ---------------------------------------------------------- Cloak admin API

class CloakAPIErr(Exception):
    pass


class CloakAPI:
    """Локальный `ck-client -a` → HTTP admin-API на 127.0.0.1.

    Жизненный цикл: запустить на пачку операций, убить. Клиент засыпает по
    idle (~минута) — поэтому на каждую пачку поднимаем заново и умеем
    рестартовать при отказе соединения.
    """

    def __init__(self, ck_exe, srv, log, vlog=None):
        self.t = T
        self.ck = ck_exe
        self.srv = srv
        self.log = log
        self.vlog = vlog  # лог служебных строк (скрываются галочкой)
        self.proc = None
        self.base = None          # http://127.0.0.1:PORT
        self.cfg_path = os.path.join(APP_DIR, "tmp-admin-ckclient.json")
        self._outq = queue.Queue()
        self._ready = threading.Event()

    # --- lifecycle ---
    def _cfg(self):
        s = self.srv
        # LocalPort "0" НЕ работает (API реально слушает :0 → урл невалиден) —
        # выделяем свободный порт сами и пишем его в конфиг.
        import socket as _sock
        with _sock.socket() as tmp:
            tmp.bind(("127.0.0.1", 0))
            port = tmp.getsockname()[1]
        cfg = {
            "Transport": "direct",
            "ProxyMethod": "openvpn",
            "EncryptionMethod": "plain",
            "UID": s["admin_uid"],
            "PublicKey": s["pubkey"],
            "ServerName": s.get("mask_domain", "www.bing.com"),
            "NumConn": 4,
            "BrowserSig": "firefox",
            "StreamTimeout": 300,
            "RemoteHost": s["host"],
            "RemotePort": str(s.get("ck_port") or "443"),
            "LocalHost": "127.0.0.1",
            "LocalPort": str(port),
            "UDP": False,          # admin-API — TCP, UDP:true ломает подъём
        }
        os.makedirs(APP_DIR, exist_ok=True)
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    def _pump(self, stream):
        for line in stream:
            self._outq.put(line.rstrip("\n"))

    def _log_line(self, line):
        """Служебный вывод ck-client: info/debug — в подробный лог,
        warn/error — всегда в основной."""
        if "level=info" in line or "level=debug" in line:
            (self.vlog or self.log)("  ck-client: %s" % line)
        else:
            self.log("  ck-client: %s" % line)

    def start(self, timeout=20):
        self._cfg()
        self._ready.clear()
        self.proc = track_proc(subprocess.Popen(
            [self.ck, "-a", self.srv["admin_uid"], "-c", self.cfg_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW))
        threading.Thread(target=self._pump, args=(self.proc.stdout,), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                line = self._outq.get(timeout=0.5)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise CloakAPIErr(self.t("ck-client -a завершился, rc=%s") % self.proc.returncode)
                continue
            self._log_line(line)
            m = re.search(r"API base is (?:https?://)?([\d.]+:\d+)", line)
            if m:
                self.base = "http://%s" % m.group(1)
                return
        raise CloakAPIErr(self.t("ck-client -a не поднял API base за %s с") % timeout)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc is not None:
            untrack_proc(self.proc)
        self.proc = None
        self.base = None

    def _req(self, method, path, body=None, _retry=True):
        if not self.base:
            raise CloakAPIErr(self.t("admin-API не запущен"))
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode(errors="replace")
                return r.status, (json.loads(raw) if raw.strip().startswith(("[", "{")) else raw)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")
        except Exception as e:
            if _retry:
                # клиент заснул — перезапускаем и повторяем один раз
                self.log(self.t("  admin-API: рестарт ck-client (%s)") % e)
                self.stop()
                self.start()
                return self._req(method, path, body, _retry=False)
            raise CloakAPIErr(self.t("admin-API недоступен: %s") % e)

    # --- операции ---
    def list_users(self):
        code, r = self._req("GET", "/admin/users")
        if code != 200:
            raise CloakAPIErr("Cloak API GET /admin/users → %s: %s"
                              % (code, r))
        return r if isinstance(r, list) else []

    def create_user(self, sessions_cap=16, expiry=FAR_FUTURE,
                    up_rate=INT64_MAX, down_rate=INT64_MAX,
                    up_credit=INT64_MAX, down_credit=INT64_MAX,
                    uid=None):
        uid = uid or base64.b64encode(secrets.token_bytes(16)).decode()
        body = {"UID": uid, "SessionsCap": sessions_cap,
                "UpRate": up_rate, "DownRate": down_rate,
                "UpCredit": up_credit, "DownCredit": down_credit,
                "ExpiryTime": expiry}
        code, r = self._req("POST", "/admin/users/" + uid_to_b64url(uid), body)
        if code not in (200, 201):
            raise CloakAPIErr("Cloak API POST /admin/users → %s: %s"
                              % (code, r))
        return uid

    def update_user(self, uid, sessions_cap=None, expiry=None,
                    up_rate=INT64_MAX, down_rate=INT64_MAX,
                    up_credit=INT64_MAX, down_credit=INT64_MAX):
        """Правка существующего UID: PUT полным телом; если старый API без
        PUT — пересоздаём тем же UID через DELETE+POST."""
        body = {"UID": uid,
                "SessionsCap": sessions_cap if sessions_cap is not None else 16,
                "UpRate": up_rate, "DownRate": down_rate,
                "UpCredit": up_credit, "DownCredit": down_credit,
                "ExpiryTime": expiry if expiry is not None else FAR_FUTURE}
        code, r = self._req("PUT", "/admin/users/" + uid_to_b64url(uid), body)
        if code in (404, 405):
            self.delete_user(uid)
            self.create_user(sessions_cap=body["SessionsCap"],
                             expiry=body["ExpiryTime"],
                             up_rate=up_rate, down_rate=down_rate,
                             up_credit=up_credit, down_credit=down_credit,
                             uid=uid)
            return True
        if code not in (200, 201, 204):
            raise CloakAPIErr("Cloak API PUT user → %s: %s"
                              % (code, r))
        return True

    def delete_user(self, uid):
        code, r = self._req("DELETE", "/admin/users/" + uid_to_b64url(uid))
        if code not in (200, 204):
            raise CloakAPIErr("Cloak API DELETE user → %s: %s"
                              % (code, r))
        return True


# ------------------------------------------------------------- server ops

def parse_section(out, marker):
    """Вырезает блок между ===MARKER=== и следующей ===-строкой."""
    m = re.search(r"===\s*%s\s*===\s*\n(.*?)(?=\n===|\Z)" % re.escape(marker),
                  out, re.S)
    return m.group(1).strip() if m else ""


def make_ovpn(proto, ca, cert, key, ta):
    return OVPN_TEMPLATE.format(proto=proto, ca=ca, cert=cert, key=key, ta=ta)


def make_ckclient(srv, uid, mask=None):
    return {
        "Transport": "direct",
        "ProxyMethod": "openvpn",
        "EncryptionMethod": "plain",
        "UID": uid,
        "PublicKey": srv["pubkey"],
        # ServerName — SNI, который видит цензор: можно разный на юзера;
        # серверу без разницы (его RedirAddr один на всех)
        "ServerName": mask or srv.get("mask_domain", "www.bing.com"),
        "NumConn": 16,
        "BrowserSig": "firefox",
        "StreamTimeout": 300,
        "RemoteHost": srv["host"],
        "RemotePort": str(srv.get("ck_port") or "443"),
        "LocalHost": "127.0.0.1",
        "LocalPort": "1984",
        "UDP": srv.get("proto", "udp") == "udp",
    }


def make_dgcloak(srv, name, uid, mats, mask=None):
    """Единый файл профиля для клиента: ck-конфиг + .ovpn в одном JSON."""
    return {"type": "dgcloak-profile", "version": 1, "name": name,
            "cloak": make_ckclient(srv, uid, mask),
            "ovpn": make_ovpn(srv.get("proto", "udp"),
                              mats["ca"], mats["cert"], mats["key"],
                              mats["ta"])}


def write_user_bundle(s, name, uid, mats, dst, mask=None):
    """Конфиг юзера — один файл <name>.dgcloak (cloak+ovpn в одном JSON).
    Сырые .ovpn/ckclient при желании вытаскиваются из него же."""
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, "%s.dgcloak" % name), "w",
              encoding="utf-8") as f:
        json.dump(make_dgcloak(s, name, uid, mats, mask), f,
                  ensure_ascii=False, indent=2)


def provision_client(srv_name, cn, dgcloak_path=None, delete=False):
    """Если на этом ПК стоит клиент DGCloakVPN — передать профиль через
    его inbox\\<cn>@<сервер>.dgcloak / .del. Клиент определяем по data-dir
    %APPDATA%\\DGCloak\\VPN — exe может лежать где угодно, папка одна.
    Маркеры подхватывает сам клиент (свой data.json пишет только он —
    гонок нет, мьютекс не нужен). Атомарно через tmp+os.replace.
    True = клиент найден и файл брошен."""
    vdir = os.path.join(os.environ.get("APPDATA", ""), "DGCloak", "VPN")
    if not os.path.isdir(vdir):
        return False
    inbox = os.path.join(vdir, "inbox")
    pname = "%s@%s" % (cn, srv_name)
    try:
        os.makedirs(inbox, exist_ok=True)
        if delete:
            mark = os.path.join(inbox, pname + ".del")
            tmp = mark + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(pname)
            os.replace(tmp, mark)
            dg = os.path.join(inbox, pname + ".dgcloak")
            if os.path.isfile(dg):
                os.remove(dg)
        else:
            tmp = os.path.join(inbox, pname + ".tmp")
            shutil.copyfile(dgcloak_path, tmp)
            os.replace(tmp, os.path.join(inbox, pname + ".dgcloak"))
            dl = os.path.join(inbox, pname + ".del")
            if os.path.isfile(dl):
                os.remove(dl)
        return True
    except OSError:
        return False


def parse_cert_bundle(out):
    return {
        "ca":   parse_section(out, "CA"),
        "cert": parse_section(out, "CERT"),
        "key":  parse_section(out, "KEY"),
        "ta":   parse_section(out, "TA"),
    }


# -------------------------------------------------------------------- GUI

class ServerDialog(simpledialog.Dialog):
    """Диалог добавления/редактирования сервера."""

    def __init__(self, parent, srv=None, title="Сервер"):
        self.t = parent.t
        self.srv = srv or {}
        super().__init__(parent, self.t(title))

    def body(self, f):
        self.vars = {}
        fields = [
            ("name", self.t("Название"), self.srv.get("name", "")),
            ("host", self.t("Доменное имя/IP"), self.srv.get("host", "")),
            ("ssh_port", self.t("SSH порт"), str(self.srv.get("ssh_port", 22))),
            ("user", self.t("SSH логин"), self.srv.get("user", "ubuntu")),
            ("ppk", self.t("Ключ .ppk (PuTTY)"), self.srv.get("ppk", "")),
            ("key", self.t("Ключ OpenSSH"), self.srv.get("key", "")),
            ("password", self.t("Пароль (если нет ключей)"), self.srv.get("password", "")),
        ]
        for i, (k, label, val) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=3)
            v = tk.StringVar(value=val)
            self.vars[k] = v
            e = ttk.Entry(f, textvariable=v, width=42)
            e.grid(row=i, column=1, padx=4, pady=3)
            if k in ("ppk", "key"):
                ft = ([("PuTTY key", "*.ppk")] if k == "ppk"
                      else [("OpenSSH key", "id_*")])
                ft.append((self.t("Все файлы"), "*.*"))
                ttk.Button(f, text="…", width=2,
                           command=lambda v=v, ft=ft: v.set(
                               filedialog.askopenfilename(filetypes=ft)
                               or v.get())
                           ).grid(row=i, column=2)
            if k == "password":
                e.config(show="*")
        ttk.Label(f, text=self.t("Приоритет: ключ OpenSSH → .ppk → пароль"),
                  foreground="#666").grid(row=len(fields), column=0,
                                          columnspan=3, sticky="w", padx=4)
        return f

    def validate(self):
        if not self.vars["name"].get().strip() or not self.vars["host"].get().strip():
            messagebox.showerror(self.t("Сервер"), self.t("Нужны имя и хост"), parent=self)
            return False
        if not (self.vars["ppk"].get().strip() or self.vars["key"].get().strip()
                or self.vars["password"].get()):
            messagebox.showerror(self.t("Сервер"), self.t("Нужен ключ (.ppk/OpenSSH) или пароль"),
                                 parent=self)
            return False
        try:
            port = int(self.vars["ssh_port"].get() or 22)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror(self.t("Сервер"), self.t("SSH порт: число 1-65535"), parent=self)
            return False
        # частая ошибка — выбрать .pub вместо приватного ключа
        key = self.vars["key"].get().strip()
        if key.lower().endswith(".pub"):
            priv = key[:-4]
            if os.path.isfile(priv):
                self.vars["key"].set(priv)
            else:
                messagebox.showerror(
                    self.t("Сервер"), self.t("Это публичный ключ (.pub) — нужен приватный,\n"
                    "обычно тот же файл без расширения .pub"), parent=self)
                return False
        return True

    def apply(self):
        self.result = {
            "name": self.vars["name"].get().strip(),
            "host": self.vars["host"].get().strip(),
            "ssh_port": int(self.vars["ssh_port"].get() or 22),
            "user": self.vars["user"].get().strip() or "ubuntu",
            "ppk": self.vars["ppk"].get().strip(),
            "key": self.vars["key"].get().strip(),
            "password": self.vars["password"].get(),
        }
        for k in ("admin_uid", "pubkey", "mask_domain", "proto", "deployed",
                  "ck_ver", "public_ip", "ext_if", "users", "deploy_stage"):
            if k in self.srv:
                self.result[k] = self.srv[k]


class UserDialog(simpledialog.Dialog):
    """Диалог создания/правки юзера. rec — запись реестра для правки."""

    def __init__(self, parent, srv_mask="", rec=None, title="Новый юзер"):
        self.t = parent.t
        self.srv_mask = srv_mask
        self.rec = rec
        super().__init__(parent, self.t(title))

    def body(self, f):
        rec = self.rec or {}
        exp_days = "0"
        if rec.get("expiry") and rec["expiry"] < FAR_FUTURE:
            exp_days = str(max(1, round((rec["expiry"] - time.time()) / 86400)))
        fields = [("name", self.t("Имя (CN, [a-z0-9_-])"), rec.get("cn", "")),
                  ("expiry_days", self.t("Срок жизни, дней (0 = бессрочно)"), exp_days),
                  ("sessions", self.t("Макс. одновременных подключений"),
                   str(rec.get("sessions") or 16)),
                  ("up_mbits", self.t("Лимит скорости ↑, Мбит/с (0 = безлимит)"),
                   "%g" % (rec["up_rate"] * 8 / 1e6)
                   if rec.get("up_rate") and not _is_unlim(rec["up_rate"]) else "0"),
                  ("down_mbits", self.t("Лимит скорости ↓, Мбит/с (0 = безлимит)"),
                   "%g" % (rec["down_rate"] * 8 / 1e6)
                   if rec.get("down_rate") and not _is_unlim(rec["down_rate"]) else "0"),
                  ("up_mb", self.t("Квота трафика ↑, МБ (0 = безлимит)"),
                   str(rec["up_credit"] // 1048576)
                   if rec.get("up_credit") and not _is_unlim(rec["up_credit"]) else "0"),
                  ("down_mb", self.t("Квота трафика ↓, МБ (0 = безлимит)"),
                   str(rec["down_credit"] // 1048576)
                   if rec.get("down_credit") and not _is_unlim(rec["down_credit"]) else "0"),
                  ("mask", self.t("Домен для маскировки"),
                   rec.get("mask") or self.srv_mask)]
        self.vars = {}
        for i, (k, label, val) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=3)
            v = tk.StringVar(value=val)
            self.vars[k] = v
            e = ttk.Entry(f, textvariable=v, width=30)
            e.grid(row=i, column=1, padx=4, pady=3)
            if rec and k == "name":
                e.config(state="readonly")  # CN = сертификат, не меняем
        return f

    def validate(self):
        if not valid_cn(self.vars["name"].get().strip()):
            messagebox.showerror(self.t("Юзер"), self.t("Имя: только латиница, цифры, _ и -"),
                                 parent=self)
            return False
        lbl = {"expiry_days": self.t("Срок жизни, дней"),
               "sessions": self.t("Макс. одновременных подключений"),
               "up_mb": self.t("Квота трафика ↑, МБ"), "down_mb": self.t("Квота трафика ↓, МБ"),
               "up_mbits": self.t("Лимит скорости ↑, Мбит/с"),
               "down_mbits": self.t("Лимит скорости ↓, Мбит/с")}
        for k in ("expiry_days", "sessions", "up_mb", "down_mb"):
            try:
                int(self.vars[k].get() or 0)
            except ValueError:
                messagebox.showerror(self.t("Юзер"), self.t("Поле «%s» — только целое число")
                                     % lbl[k], parent=self)
                return False
        for k in ("up_mbits", "down_mbits"):
            try:
                float(self.vars[k].get() or 0)
            except ValueError:
                messagebox.showerror(self.t("Юзер"), self.t("Поле «%s» — только число")
                                     % lbl[k], parent=self)
                return False
        return True

    def apply(self):
        days = int(self.vars["expiry_days"].get() or 0)
        self.result = {
            "name": self.vars["name"].get().strip(),
            "expiry": FAR_FUTURE if days <= 0 else int(time.time()) + days * 86400,
            "sessions": max(1, int(self.vars["sessions"].get() or 16)),
            # 0/пусто → INT64_MAX: в Cloak rate=0 = полный запрет трафика,
            # безлимит выражается только большим числом
            "up_rate": INT64_MAX if float(self.vars["up_mbits"].get() or 0) <= 0
                       else int(float(self.vars["up_mbits"].get()) * 125000),
            "down_rate": INT64_MAX if float(self.vars["down_mbits"].get() or 0) <= 0
                         else int(float(self.vars["down_mbits"].get()) * 125000),
            "up_credit": INT64_MAX if int(self.vars["up_mb"].get() or 0) <= 0
                        else int(self.vars["up_mb"].get()) * 1048576,
            "down_credit": INT64_MAX if int(self.vars["down_mb"].get() or 0) <= 0
                          else int(self.vars["down_mb"].get()) * 1048576,
            "mask": self.vars["mask"].get().strip(),
        }


class PortsDialog(tk.Toplevel):
    """Управление портами фаервола: список правил + открыть/закрыть порт."""

    def __init__(self, app, srv, rules_text):
        super().__init__(app)
        self.app = app
        self.t = app.t
        self.srv = srv
        app._ports_dlg = self
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.title(self.t("Управление фаерволом — %s") % srv["name"])
        self.txt = tk.Text(self, width=72, height=16, font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True, padx=6, pady=6)
        self._fill(rules_text)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=6)
        ttk.Label(bar, text=self.t("Порт:")).pack(side="left")
        self.v_port = tk.StringVar()
        ttk.Entry(bar, textvariable=self.v_port, width=7).pack(side="left", padx=4)
        self.v_proto = tk.StringVar(value="tcp")
        ttk.Combobox(bar, textvariable=self.v_proto, width=5,
                     values=["tcp", "udp"], state="readonly").pack(side="left")
        ttk.Button(bar, text=self.t("Открыть"),
                   command=lambda: self._act("allow")).pack(side="left", padx=4)
        ttk.Button(bar, text=self.t("Закрыть"),
                   command=lambda: self._act("deny")).pack(side="left")
        ttk.Button(bar, text=self.t("Обновить"),
                   command=self._reload).pack(side="left", padx=10)

    def _port_label(self, proto, port, cm):
        """Человеческий комментарий к открытому порту (зависит от деплоя)."""
        s = self.srv
        ssh_ports = {str(p) for p in
                     [s.get("ssh_port", 22)] + s.get("sshd_ports", [])}
        ck_port = str(s.get("ck_port")
                      or self.app.v_ckport.get().strip() or "443")
        if proto == "tcp" and port in ssh_ports:
            return self.t("SSH — не удалять")
        if proto == "tcp" and port == ck_port:
            return self.t("Cloak VPN — не удалять")
        if proto == "udp" and port in ("68", "546"):
            return self.t("DHCP-клиент — не удалять")
        return self.t(self.COMMENT_RU.get(cm) or "пользовательский порт")

    def _port_protected(self, proto, port):
        ssh_ports = {str(p) for p in
                     [self.srv.get("ssh_port", 22)] + self.srv.get("sshd_ports", [])}
        ck_port = str(self.srv.get("ck_port")
                      or self.app.v_ckport.get().strip() or "443")
        return (proto == "tcp" and port in ssh_ports) \
            or (proto == "tcp" and port == ck_port) \
            or (proto == "udp" and port in ("68", "546"))

    COMMENT_RU = {"ssh": "SSH — не удалять", "cloak": "Cloak VPN — не удалять",
                  "dhcp": "DHCP-клиент — не удалять",
                  "dhcpv6-client": "DHCPv6-клиент — не удалять"}

    def _fill(self, out):
        fw = ""
        rows = []
        seen = set()
        notes = []
        for line in out.splitlines():
            line = line.strip()
            m = re.match(r"OPEN\s+(\w+)\s+(\d+)\s*(.*)", line)
            if m:
                proto, port, cm = m.groups()
                if (port, proto) in seen:  # ufw дублирует правила для v6
                    continue
                seen.add((port, proto))
                rows.append((port, proto,
                             self._port_label(proto, port, cm)))
            elif line.startswith("FW="):
                fw = line[3:]
            elif line and not line.startswith("==="):
                notes.append(line)
        t = self.t("Фаервол: %s\n\nПорты, открытые снаружи:\n\n") % (fw or "?")
        if rows:
            t += "\n".join("   %-9s %s" % ("%s/%s" % (p, pr), cm)
                           for p, pr, cm in sorted(rows, key=lambda r: int(r[0])))
        else:
            t += self.t("   (нет открытых портов)")
        t += self.t("\n\nОстальные входящие соединения закрыты.")
        if notes:
            t += "\n\n" + "\n".join(notes)
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("end", t)
        self.txt.config(state="disabled")

    def _close(self):
        self.app._ports_dlg = None
        self.destroy()

    def _reload(self):
        def work():
            ssh = SSH(self.srv, self.app.say)
            out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
            self.app.ui(lambda: self._fill(out))
        self.app._worker(work, ctx=self.srv["name"])

    def _act(self, action):
        port = self.v_port.get().strip()
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            messagebox.showerror(APP_NAME, self.t("Порт: число 1-65535"), parent=self)
            return
        proto = self.v_proto.get()
        if action == "deny":
            lbl = self._port_label(proto, port, "")
            if self._port_protected(proto, port):
                if not messagebox.askyesno(
                        APP_NAME,
                        self.t("Порт %s/%s помечен «%s».\nЗакрытие может отрезать "
                        "доступ к серверу или VPN.\n\nВсё равно закрыть?")
                        % (port, proto, lbl), parent=self):
                    return

        def work():
            ssh = SSH(self.srv, self.app.say)
            out = ssh.run_script("fw-manage.sh",
                                 "%s %s %s" % (action, proto, port),
                                 timeout=60)
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            fw_ = next((l.split("=", 1)[1] for l in lines
                        if l.startswith("FW=")), "?")
            notes = "; ".join(l for l in lines if not
                              l.startswith(("FW=", "=== ", "ok:")))[:140]
            verb = self.t("открыт") if action == "allow" else self.t("закрыт")
            self.app.say(self.t("fw: порт %s/%s %s, бэкенд %s%s")
                         % (proto, port, verb, fw_,
                            " (%s)" % notes if notes else ""))
            out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
            self.app.ui(lambda: self._fill(out))
        self.app._worker(work, ctx=self.srv["name"])


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1040x680")
        self.resizable(False, False)
        try:  # иконка окна: плащ + шестерёнка (без Pillow — пропускаем)
            import cloak_icon
            buf = io.BytesIO()
            cloak_icon.draw_admin_icon(cloak_icon.BRAND, 64).save(buf, "PNG")
            self._win_icon = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
            self.iconphoto(True, self._win_icon)
        except Exception:
            pass
        migrate_dirs()
        self.data = load_data()
        # язык: из настроек, при первом запуске — по локали Windows
        self.lang = self.data.get("language") or detect_lang()
        global _LANG
        _LANG = self.lang
        self._i18n = []       # [(widget, ru, fmt_args)] — тексты для re-apply
        self._tips = []       # [(Tooltip, ru)] — тултипы для re-apply
        self._nb_tabs = []    # [(frame, ru)] — подписи вкладок
        self.uiq = queue.Queue()
        self.busy = False
        self._ports_dlg = None
        self._spin_key = None     # ключ шага, который сейчас крутится
        self._spin_srv = None     # сервер, на котором идёт операция
        self._spin_i = 0
        self._spin_t0 = 0.0
        self._spin_hb = 0.0
        self._logs = {}           # name -> [строки лога]
        self._log_name = None     # чей лог показан
        self._log_ctx = None      # на каком сервере идёт операция
        self._op_tab = None       # канал лога операции: "deploy"|"users"
        self._online = {}         # (name, ssh|cloak) -> bool: порт доступен
        self._probing = set()     # (name, which) в полёте — без дублей
        self._no_sel_ev = False   # глушит <<TreeviewSelect>> при rebuild
        self.verbose = tk.BooleanVar(value=False)
        self._verbose_on = False  # потокобезопасный дубль для _say
        self._fix_ctrl_bindings()
        self._build()
        self.after_idle(self._fit_width)
        for var, key in ((self.v_mask, "mask_domain"),
                         (self.v_proto, "proto"),
                         (self.v_ckport, "ck_port")):
            var.trace_add("write",
                          lambda *a, k=key, v=var: self._opt_changed(k, v))
        self._refresh_servers()
        self.after(100, self._drain)
        cleanup_stale_procs(log=self.say)
        threading.Thread(target=self._online_loop, daemon=True).start()
        self._ensure_deps()

    # ---- локализация ----
    def t(self, ru):
        return STRINGS_EN.get(ru, ru) if self.lang == "en" else ru

    def _tw(self, w, ru, *a):
        """Виджет с переводимым текстом: регистрирует для смены языка."""
        w.config(text=self.t(ru) % a if a else self.t(ru))
        self._i18n.append((w, ru, a))
        return w

    def _tip(self, w, ru):
        tp = Tooltip(w, self.t(ru))
        tp.ru = ru
        self._tips.append(tp)
        return tp

    def _nb_tab(self, nb, f, ru):
        nb.add(f, text=self.t(ru))
        self._nb_tabs.append((f, ru))

    def _guide(self):
        webbrowser.open(GUIDE_URL % self.lang)

    def _on_lang(self, _e=None):
        lang = "en" if self.v_lang.get() == "English" else "ru"
        if lang == self.lang:
            return
        self.lang = lang
        global _LANG
        _LANG = lang
        self.data["language"] = lang
        save_data(self.data)
        self._apply_lang()
        # на новом языке: RU→EN пишет по-английски, EN→RU по-русски
        self.say(self.t("Язык интерфейса: %s") %
                 self.v_lang.get())

    def _apply_lang(self):
        """Живое переключение: виджеты/тултипы/вкладки/заголовки.
        Журнал и заметки шагов не переводятся задним числом."""
        for w, ru, a in self._i18n:
            try:
                w.config(text=self.t(ru) % a if a else self.t(ru))
            except tk.TclError:
                pass
        for tp in self._tips:
            tp.text = self.t(tp.ru)
        for f, ru in self._nb_tabs:
            self.nb.tab(f, text=self.t(ru))
        for c, ru in self._users_heads.items():
            ctr = c in ("sessions", "online")
            self.users_tv.heading(c, text=self.t(ru),
                                  anchor="center" if ctr else "w")
        for c, ru in self._srv_heads.items():
            self.srv_tv.heading(c, text=self.t(ru))
        self._fill_steps()
        if self._log_name:
            self._log_load(self._log_name)
        self.v_tcpwarn.set(self.t("(TCP медленнее)")
                           if self.v_proto.get() == "tcp" else "")
        if getattr(self, "_usr_note", None):
            ru, a = self._usr_note
            self.v_users_srv.set(self.t(ru) % a)
        self.after_idle(self._fit_width)

    def _fit_width(self):
        """Подогнать окно под текущий язык: RU-подписи кнопок длиннее EN,
        при фиксированной геометрии они обрезались. Окно растёт до ширины,
        реально запрошенной виджетами (не меньше стартовой)."""
        self.update_idletasks()
        w = max(1040, self.winfo_reqwidth())
        h = max(680, self.winfo_reqheight())
        if (w, h) != (self.winfo_width(), self.winfo_height()):
            self.geometry("%dx%d" % (w, h))

    def _ensure_deps(self):
        """Первый запуск на чистой машине: SSH-слой и ck-client.
        plink/pscp качаем сами (одиночные exe); OpenSSH — это Windows-
        компонент, ставим через dism только из-под админа, иначе совет."""
        def work():
            self.say(self.t("=== Проверка зависимостей ==="))
            openssh = [r"C:\Windows\System32\OpenSSH"]
            if find_exe(["ssh.exe", "ssh"], openssh) and \
                    find_exe(["scp.exe", "scp"], openssh):
                self.say(self.t("  OpenSSH-клиент: на месте"))
            elif install_openssh(self.say):
                self.say(self.t("  OpenSSH-клиент: установлен"))
            else:
                self.say(self.t("  OpenSSH-клиент: НЕТ — нужен для входа по "
                         "OpenSSH-ключу. Установка: Параметры → Приложения "
                         "→ Дополнительные компоненты → «Клиент OpenSSH», "
                         "либо используй пароль/.ppk (для них хватит PuTTY)"))
            plink, pscp = find_putty()
            if plink and pscp:
                self.say(self.t("  PuTTY (plink/pscp): на месте"))
            else:
                try:
                    self.say(self.t("  PuTTY (plink/pscp): нет — скачиваю с "
                             "официального сайта…"))
                    download_putty(self.say)
                except Exception as e:
                    self.say(self.t("  !! plink/pscp не скачались (%s) — вход по "
                             "паролю и по .ppk не будет работать. Поставь "
                             "PuTTY или используй OpenSSH-ключ") % e)
            # старые установки держали ck-client в корне APP_DIR —
            # перекладываем в bin\ для единообразия
            old_ck = os.path.join(APP_DIR, "ck-client.exe")
            new_ck = os.path.join(BIN_DIR, "ck-client.exe")
            if os.path.isfile(old_ck) and not os.path.isfile(new_ck):
                try:
                    os.makedirs(BIN_DIR, exist_ok=True)
                    shutil.move(old_ck, new_ck)
                except OSError:
                    pass  # найдём и в старом месте
            found = find_ck_client(self.data)
            if found:
                if os.path.normpath(found) != os.path.normpath(new_ck):
                    # чужая копия (например VPN-клиента) — заберём себе:
                    # админка не должна ломаться, если клиент снесут
                    try:
                        os.makedirs(BIN_DIR, exist_ok=True)
                        shutil.copy2(found, new_ck)
                        self.say(self.t("  ck-client: нашёл %s → скопировал в "
                                 "bin\\") % found)
                    except OSError:
                        self.say(self.t("  ck-client: на месте (%s)") % found)
                else:
                    self.say(self.t("  ck-client: на месте"))
            else:
                try:
                    ck = download_ck_client(self.say)
                    self.say("  ck-client → %s" % ck)
                except Exception as e:
                    self.say(self.t("  !! ck-client не скачался (%s) — вкладка "
                             "«Пользователи» не заработает; деплой — будет")
                             % e)
        self._worker(work)

    def _opt_changed(self, key, var):
        """Поля маскировка/протокол/порт — per-server: правка сразу пишется
        в выбранный сервер, иначе терялась при переключении."""
        s = self._sel_srv_silent()
        if not s:
            return
        v = var.get().strip()
        if v:
            s[key] = v
        else:
            s.pop(key, None)
        save_data(self.data)

    # ---- UI plumbing (как в клиенте) ----
    def _fix_ctrl_bindings(self):
        """На русской раскладке Ctrl+V приходит с кириллическим keysym —
        стандартный биндинг <Control-v> молча не срабатывает. Ловим
        <Control-KeyPress> и смотрим keycode (он раскладке не зависит)."""
        kc = {86: "<<Paste>>", 67: "<<Copy>>",  # V, C
              88: "<<Cut>>", 65: "<<SelectAll>>"}  # X, A
        latin = "vcxaVCXA"

        def _ctrl(e, _kc=kc, _l=latin):
            v = _kc.get(e.keycode)
            # keysym латинский — отработает штатный <Control-v> и т.п.
            if v and e.keysym not in _l:
                e.widget.event_generate(v)
                return "break"

        for cls in ("Entry", "TEntry", "Text", "TCombobox",
                    "Spinbox", "TSpinbox"):
            self.bind_class(cls, "<Control-KeyPress>", _ctrl)

    def ui(self, fn, *a):
        self.uiq.put((fn, a))

    def _drain(self):
        try:
            while True:
                fn, a = self.uiq.get_nowait()
                try:
                    fn(*a)
                except Exception as e:
                    print("ui: %r" % e)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _say(self, msg, verbose=False):
        """verbose=True — служебная строка: хранится в журнале, но на экране
        видна только при включённом «Подробном выводе» (фильтр при показе —
        галочка раскрывает и старые строки)."""
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        # активная операция пишет в журнал СВОЕГО сервера и в свой канал
        # (deploy/users — по вкладке, с которой запущена), даже если юзер
        # переключил выбор; без операции — в показанный журнал/вкладку
        name = self._log_ctx or self._log_name
        tab = self._op_tab or self._cur_tab()
        if name is not None:
            self._logs.setdefault(name, []).append((line, verbose, tab))
        # self._verbose_on — обычный bool-дубль tk-переменной: _say зовут
        # из рабочих потоков, а tk-переменные читать оттуда нельзя
        if (name == self._log_name or name is None) and \
                (not verbose or self._verbose_on):
            w_ = self.logw_usr if tab == "users" else self.logw_dep
            def w(w_=w_):
                w_.insert("end", line + "\n")
                w_.see("end")
            self.ui(w)

    def say(self, msg):
        self._say(msg)

    def vsay(self, msg):
        self._say(msg, verbose=True)

    def _cur_tab(self):
        """Канал лога активной вкладки: deploy или users."""
        try:
            return "users" if self.nb.index(self.nb.select()) == 1 else "deploy"
        except Exception:
            return "deploy"

    def _srv_by_name(self, name):
        return next((s for s in self.data["servers"] if s["name"] == name),
                    None)

    def _log_load(self, name):
        """Показать журнал сервера — в каждой вкладке свой канал.
        «Подробный вывод» — флаг сервера (log_verbose в реестре):
        у каждого своё состояние, переживает перезапуск."""
        s = self._srv_by_name(name)
        v = bool(s and s.get("log_verbose"))
        self.verbose.set(v)
        self._verbose_on = v
        for tab, w_ in (("deploy", self.logw_dep), ("users", self.logw_usr)):
            w_.delete("1.0", "end")
            if self._log_ctx and self._log_ctx != name and self._op_tab == tab:
                w_.insert("end", self.t("…идёт операция на «%s» — её вывод пишется "
                                 "в журнал этого сервера…\n\n") % self._log_ctx)
            entries = [l for l, vb, t in self._logs.get(name, [])
                       if t == tab and (not vb or v)]
            if entries:
                w_.insert("end", "\n".join(entries) + "\n")
            w_.see("end")
        self.logframe_dep.config(text=self.t("Лог — %s") % name)
        self.logframe_usr.config(text=self.t("Лог — %s") % name)

    def _on_verbose_toggle(self):
        s = self._srv_by_name(self._log_name) if self._log_name else None
        self._verbose_on = self.verbose.get()
        if s is not None:
            s["log_verbose"] = self._verbose_on
            save_data(self.data)
        if self._log_name:
            self._log_load(self._log_name)

    def _mark_log(self, tab, msg):
        """Отметка о локальном действии с журналом (копирование/очистка)
        — пишется в журнал ПОКАЗАННОГО сервера в видимый канал,
        мимо контекста чужой операции (_log_ctx/_op_tab)."""
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        if self._log_name is not None:
            self._logs.setdefault(self._log_name, []).append(
                (line, False, tab))
        w_ = self.logw_usr if tab == "users" else self.logw_dep
        w_.insert("end", line + "\n")
        w_.see("end")

    def _copy_log(self, tab):
        w_ = self.logw_usr if tab == "users" else self.logw_dep
        txt = w_.get("1.0", "end").rstrip()
        self.clipboard_clear()
        self.clipboard_append(txt)
        self._mark_log(tab, self.t("Лог скопирован в буфер (%d символов)")
                       % len(txt))

    def _copy_log_dep(self):
        self._copy_log("deploy")

    def _clear_log(self, tab):
        """Чистит журнал ТЕКУЩЕГО сервера только в своём канале —
        записи другой вкладки и других серверов не трогаем."""
        name = self._log_name
        if name is not None:
            self._logs[name] = [e for e in self._logs.get(name, [])
                                if e[2] != tab]
        w_ = self.logw_usr if tab == "users" else self.logw_dep
        w_.delete("1.0", "end")
        self._mark_log(tab, self.t("Лог очищен."))

    def _clear_log_dep(self):
        self._clear_log("deploy")

    def _set_busy(self, b):
        self.busy = b
        state = "disabled" if b else "normal"
        for btn in self._all_buttons:
            btn.config(state=state)
        if hasattr(self, "btn_stop"):
            self.btn_stop.config(state="normal" if b else "disabled")
        if b:
            self._beat_last = 0
            self._op_tick()
        else:
            self.title(APP_NAME)

    def _worker(self, fn, ctx=None):
        """ctx — имя сервера, чей журнал получает лог (если операция
        привязана к серверу не по текущему выбору, а по диалогу)."""
        if self.busy:
            messagebox.showinfo(APP_NAME, self.t("Идёт операция на «%s» — подожди")
                                % (self._log_ctx or "?"))
            return
        self.busy = True
        SSH.op_cancel.clear()
        self._op_started = time.time()
        tab = self._cur_tab()  # вкладка-источник — читаем в главном потоке
        self.ui(lambda: self._set_busy(True))

        def run():
            self._log_ctx = ctx or self._log_name  # журнал сервера операции
            self._op_tab = tab                     # и канал её вкладки
            try:
                fn()
            except Exception as e:
                self.say(self.t("ОШИБКА: %s") % e)
            finally:
                self._log_ctx = None
                self._op_tab = None
                self.busy = False
                self._op_started = None
                self.ui(lambda: self._set_busy(False))
        threading.Thread(target=run, daemon=True).start()

    def _op_tick(self):
        """Пульс операции в заголовке окна + heartbeat в журнал —
        длинный SSH-таймаут не должен выглядеть как зависшее окно."""
        if not self.busy:
            self.title(APP_NAME)
            return
        el = int(time.time() - (self._op_started or time.time()))
        self.title("%s — %s (%d %s)" % (
            APP_NAME,
            self.t("операция на «%s», можно прервать кнопкой «Стоп»")
            % (self._log_ctx or "?"),
            el, self.t("с")))
        if el and el % 15 == 0 and el != getattr(self, "_beat_last", 0):
            self._beat_last = el
            self.vsay("…операция идёт уже %d с" % el)
        self.after(1000, self._op_tick)

    def _op_stop(self):
        """Кнопка «Стоп»: не ждём таймаут plink — убиваем текущий процесс,
        операция завершится ошибкой и разблокирует интерфейс."""
        if not self.busy:
            return
        SSH.op_cancel.set()
        self.say(self.t("  прерываю операцию…"))

    # ---- layout ----
    def _build(self):
        self._all_buttons = []
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=6, pady=6)

        # --- левая колонка: серверы (таблица: имя+метка шагов / онлайн) ---
        left = self._tw(ttk.LabelFrame(top), "Серверы")
        left.pack(side="left", fill="y", padx=(0, 6))
        self._srv_heads = {"name": "Имя", "ssh": "SSH", "cloak": "Cloak"}
        self.srv_tv = ttk.Treeview(left, columns=("name", "ssh", "cloak"),
                                   show="headings", height=18,
                                   selectmode="browse")
        for c, w in (("name", 170), ("ssh", 45), ("cloak", 55)):
            self.srv_tv.heading(c, text=self.t(self._srv_heads[c]))
            self.srv_tv.column(c, width=w, stretch=(c == "name"),
                               anchor="w" if c == "name" else "center")
        self.srv_tv.pack(fill="both", expand=True, padx=4, pady=4)
        self.srv_tv.bind("<<TreeviewSelect>>", lambda e: self._on_srv_select())
        self._tip(self.srv_tv,
                  "✓ развёрнут, все шаги ok\n"
                  "✓- развёрнут, но есть пропущенные шаги\n"
                  "⚠ есть предупреждение/ошибка в шагах\n"
                  "пусто — не развёрнут или не прошёл аудит\n"
                  "SSH/Cloak: ✓ порт доступен · ✗ недоступен · "
                  "… проверяется · — н/д")
        btns = ttk.Frame(left)
        btns.pack(fill="x", padx=4, pady=4)
        for t, c in (("Добавить", self._srv_add),
                     ("Изменить", self._srv_edit),
                     ("Удалить", self._srv_del)):
            self._mk_btn(btns, t, c).pack(side="left", padx=2)
        dn = ttk.Button(btns, text="▼", width=3,
                        command=lambda: self._srv_move(1))
        self._tip(dn, "Опустить в списке")
        dn.pack(side="right", padx=2)
        up = ttk.Button(btns, text="▲", width=3,
                        command=lambda: self._srv_move(-1))
        self._tip(up, "Поднять в списке")
        up.pack(side="right", padx=2)
        # «Стоп» живёт вне _all_buttons: именно он должен оставаться живым,
        # пока остальные кнопки заблокированы висящей операцией
        self.btn_stop = ttk.Button(left, state="disabled",
                                   command=self._op_stop)
        self._i18n.append((self.btn_stop, "Стоп операцию", ()))
        self.btn_stop.config(text=self.t("Стоп операцию"))
        self._tip(self.btn_stop, "Убить зависшую SSH-команду\n"
                  "(провайдер рвёт связь, сервер молчит)")
        self.btn_stop.pack(fill="x", padx=4, pady=(0, 4))

        # --- правая колонка: вкладки ---
        nb = self.nb = ttk.Notebook(top)
        nb.pack(side="left", fill="both", expand=True)
        self._tab_deploy(nb)
        self._tab_users(nb)

    def _mk_btn(self, parent, text, cmd):
        b = ttk.Button(parent, text=self.t(text), command=cmd)
        self._i18n.append((b, text, ()))
        self._all_buttons.append(b)
        return b

    # ---- вкладка «Развёртывание» ----
    STEPS = [
        ("ssh",    "1. SSH-подключение (auth + права root/sudo)"),
        ("key",    "2. Ключевая авторизация (генерация, если пароль)"),
        ("audit",  "3. Аудит ОС и окружения"),
        ("fw",     "4. Фаервол (аудит → установка/настройка)"),
        ("sysupd", "5. Обновление системы (apt full-upgrade)"),
        ("pkgs",   "6. Пакеты (OpenVPN, Easy-RSA, nftables…)"),
        ("ovpn",   "7. OpenVPN + PKI (Easy-RSA, server.conf, mgmt)"),
        ("nat",    "8. Маршрутизация и NAT"),
        ("cloak",  "9. Cloak server (маскировка, ключи)"),
    ]
    # статусы шага в s["steps"][key] = {"st": ok|warn|fail|skip, "note": str}

    def _tab_deploy(self, nb):
        f = ttk.Frame(nb)
        self._nb_tab(nb, f, "Развёртывание")
        pad = {"padx": 6, "pady": 4}

        row = 0
        optf = ttk.Frame(f)
        optf.grid(row=row, column=0, sticky="ew", **pad)
        self._tw(ttk.Label(optf), "Домен для маскировки:").pack(side="left")
        self.v_mask = tk.StringVar(value="www.bing.com")
        ttk.Entry(optf, textvariable=self.v_mask, width=36).pack(side="left", padx=4)
        self.v_ckport = tk.StringVar(value="443")
        self.w_ckport = ttk.Entry(optf, textvariable=self.v_ckport, width=5)
        self.w_ckport.pack(side="right")
        lbl_p = self._tw(ttk.Label(optf), "Cloak порт")
        lbl_p.pack(side="right", padx=(10, 4))
        self._tip(lbl_p, "На развёрнутом сервере поле заблокировано:\n"
                  "смена порта требует пересборки конфигов юзеров")
        self.v_tcpwarn = tk.StringVar()
        ttk.Label(optf, textvariable=self.v_tcpwarn,
                  foreground="#a33").pack(side="right", padx=(2, 0))
        self.v_proto = tk.StringVar(value="udp")
        self.v_proto.trace_add("write", lambda *a: self.v_tcpwarn.set(
            self.t("(TCP медленнее)") if self.v_proto.get() == "tcp" else ""))
        self.w_proto = ttk.Combobox(optf, textvariable=self.v_proto, width=5,
                                    state="readonly", values=["udp", "tcp"])
        self.w_proto.pack(side="right")
        lbl_pr = self._tw(ttk.Label(optf), "Протокол OpenVPN:")
        lbl_pr.pack(side="right", padx=(10, 0))
        self._tip(lbl_pr, "На развёрнутом сервере поле заблокировано:\n"
                  "смена протокола требует пересборки конфигов юзеров")

        row += 1
        # нативная шапка treeview на Windows ~24px и не сжимается —
        # рисуем свою тонкую строку заголовков поверх колонок
        hdr = tk.Frame(f, bg="#e8e8e8")
        hdr.grid(row=row, column=0, sticky="ew", padx=6, pady=(4, 0))
        for w, t in ((307, "Шаг"), (46, "Статус"), (403, "Комментарий")):
            c = tk.Frame(hdr, width=w, height=16, bg="#e8e8e8")
            c.pack_propagate(False)
            c.pack(side="left")
            self._tw(tk.Label(c, bg="#e8e8e8", font=("Segoe UI", 9),
                             anchor="w", padx=6), t).pack(fill="both")

        row += 1
        self.steps_tv = ttk.Treeview(f, columns=("st", "note"),
                                     show="tree", height=9)
        self.steps_tv.column("#0", width=305, stretch=False)
        self.steps_tv.column("st", width=46, anchor="center", stretch=False)
        self.steps_tv.column("note", width=401)
        # спиннер — canvas-дуга поверх ячейки «Статус» активного шага
        self.spin_cv = tk.Canvas(self.steps_tv, bd=0, highlightthickness=0,
                                 bg="white")
        self._spin_arc = self.spin_cv.create_arc(
            0, 0, 0, 0, start=0, extent=270, style="arc",
            width=2.5, outline="#357")
        self.steps_tv.grid(row=row, column=0, sticky="ew", **pad)
        f.columnconfigure(0, weight=1)

        row += 1
        bf = ttk.Frame(f)
        bf.grid(row=row, column=0, sticky="ew", **pad)
        big = self._tw(tk.Button(bf, command=self._step_run_all,
                                font=("", 10, "bold"), bg="#2d7", fg="white",
                                activebackground="#2a6", padx=10, pady=2),
                      "▶  Развернуть всё")
        big.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 10))
        self._all_buttons.append(big)
        self._tip(big, "Шаги идут сверху вниз, готовые шаги — пропускаются")
        _dep_tips = {
            "Только выбранный шаг":
                "Выполнить один выбранный в таблице шаг —\n"
                "точечный повтор после исправления ошибки",
            "Проверить статусы":
                "Read-only аудит сервера (probe): сверить состояние\n"
                "и обновить значки шагов. Если сервер не отвечает —\n"
                "метка «развёрнут» снимается до успешной проверки",
            "Управление фаерволом":
                "Открытые порты сервера: список, открыть/закрыть\n"
                "свой порт (nftables/ufw/firewalld/iptables)",
            "Вернуть сервер":
                "Вернуть сервер к состоянию до деплоя: снести\n"
                "OpenVPN+Cloak, наши правила фаервола, юзеров\n"
                "и локальные конфиги; вернуть прежнего владельца\n"
                "порта Cloak, docker-автозапуск и фаервол",
            "Перезагрузить сервер":
                "sudo reboot — нужно, если шаг «Обновление системы»\n"
                "помечен ⚠ «нужна перезагрузка»",
            "Копировать лог": "Весь лог вкладки в буфер обмена",
            "Очистить лог":
                "Стереть лог этого сервера на этой вкладке —\n"
                "канал «Пользователи» и другие серверы не трогает",
        }
        for i, (t, c) in enumerate((("Только выбранный шаг", self._step_run_sel),
                                    ("Проверить статусы", self._steps_reset),
                                    ("Управление фаерволом", self._fw_ports)),
                                   start=1):
            b = self._mk_btn(bf, t, c)
            b.grid(row=0, column=i, sticky="ew", padx=2, pady=1)
            bf.columnconfigure(i, weight=1, uniform="btn")
            self._tip(b, _dep_tips[t])
        b_imp = self._mk_btn(bf, "Импорт ключей", self._do_import)
        b_imp.grid(row=0, column=4, sticky="ew", padx=(2, 0), pady=1)
        bf.columnconfigure(4, weight=1, uniform="btn")
        self._tip(b_imp,
                  "Если сервер уже настроен (вручную или через DGCloak Admin)\n"
                  "— эта кнопка забирает с него ключи/юзеры Cloak,\n"
                  "не переустанавливая ничего. После этого сервером можно\n"
                  "управлять: юзеры, конфиги, статусы.")
        for i, (t, c) in enumerate((("Вернуть сервер", self._srv_purge),
                                    ("Перезагрузить сервер", self._srv_reboot),
                                    ("Копировать лог", self._copy_log_dep),
                                    ("Очистить лог", self._clear_log_dep)),
                                   start=1):
            b = self._mk_btn(bf, t, c)
            b.grid(row=1, column=i, sticky="ew",
                   padx=(2, 0) if i == 4 else 2, pady=1)
            self._tip(b, _dep_tips[t])

        row += 1
        leg = ttk.Frame(f)
        leg.grid(row=row, column=0, sticky="ew", **pad)
        self._tw(ttk.Label(leg, foreground="#666"),
                "✓ готово   ⚠ предупреждение   ✗ ошибка   "
                "– пропущен   … не выполнялся").pack(side="left")
        self.v_lang = tk.StringVar(
            value="English" if self.lang == "en" else "Русский")
        btn_guide = ttk.Button(leg, text="?", width=2,
                               command=self._guide)
        btn_guide.pack(side="right", padx=(4, 0))
        self._tip(btn_guide, "Руководство пользователя")
        self.cb_lang = ttk.Combobox(leg, textvariable=self.v_lang,
                                    state="readonly",
                                    values=["Русский", "English"], width=9)
        self.cb_lang.pack(side="right")
        self.cb_lang.bind("<<ComboboxSelected>>", self._on_lang)
        lbl_lang = self._tw(ttk.Label(leg), "Язык:")
        lbl_lang.pack(side="right", padx=(0, 4))
        for w in (lbl_lang, self.cb_lang):
            self._tip(w, "Уже написанное остаётся на прежнем языке:\n"
                         "записи в логе и статусы/заметки шагов деплоя.\n\n"
                         "Чтобы обновить статусы шагов — нажми\n"
                         "«Проверить статусы».")

        row += 1
        self.logframe_dep = self._tw(ttk.LabelFrame(f), "Лог")
        self.logframe_dep.grid(row=row, column=0, sticky="nsew", **pad)
        f.rowconfigure(row, weight=1)
        self.logw_dep = tk.Text(self.logframe_dep, height=10, wrap="none",
                                font=("Consolas", 9))
        sb = ttk.Scrollbar(self.logframe_dep, command=self.logw_dep.yview)
        self.logw_dep.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.logw_dep.pack(fill="both", expand=True, padx=4, pady=4)

    # ---- вкладка «Пользователи» ----
    def _tab_users(self, nb):
        f = ttk.Frame(nb)
        self._nb_tab(nb, f, "Пользователи")
        self.v_users_srv = tk.StringVar(value=self.t("— выбери сервер"))
        ttk.Label(f, textvariable=self.v_users_srv,
                  foreground="#666").pack(anchor="w", padx=6, pady=(6, 0))
        cols = ("cn", "sessions", "limit", "quota", "expiry", "mask", "online")
        self.users_tv = ttk.Treeview(f, columns=cols, show="headings", height=12)
        heads = self._users_heads = {
            "cn": "Имя (CN)", "sessions": "Макс. сессий",
            "limit": "Лимит ↑/↓", "quota": "Квота ↑/↓",
            "expiry": "Истекает", "mask": "Домен маскировки",
            "online": "Онлайн"}
        widths = {"cn": 120, "sessions": 50, "limit": 100, "quota": 100,
                  "expiry": 90, "mask": 140, "online": 55}
        for c in cols:
            ctr = c in ("sessions", "online")
            self.users_tv.heading(c, text=self.t(heads[c]),
                                  anchor="center" if ctr else "w")
            self.users_tv.column(c, width=widths[c],
                                 anchor="center" if ctr else "w")
        self.users_tv.pack(fill="both", expand=True, padx=6, pady=6)

        bf = ttk.Frame(f)
        bf.pack(fill="x", padx=6, pady=4)
        _user_tips = {
            "Обновить": "Опросить сервер: UID из admin-API Cloak,\n"
                        "лимиты из users.json, кто онлайн — из OpenVPN",
            "Создать…": "Сертификат OpenVPN + UID в Cloak;\n"
                        "конфиг сохраняется в %APPDATA%\\DGCloak\\Admin\\bundles",
            "Изменить…": "Лимиты/срок/домен маскировки выбранного юзера;\n"
                         "при смене параметров конфиг перевыпускается",
            "Отключить сейчас": "Разорвать живую сессию (mgmt OpenVPN).\n"
                                "Юзер остаётся и может переподключиться",
            "Отозвать и удалить": "Отозвать сертификат + удалить UID из Cloak\n"
                                  "+ сбросить сессию. Необратимо",
            "Экспорт конфига…": "Сохранить комплект подключения\n"
                                "(ovpn + ключи + ck-конфиг) для выбранного юзера",
        }
        for i, (t, c) in enumerate((("Обновить", self._users_refresh),
                                    ("Создать…", self._user_create),
                                    ("Изменить…", self._user_edit))):
            b = self._mk_btn(bf, t, c)
            b.grid(row=0, column=i, sticky="ew", padx=2)
            bf.columnconfigure(i, weight=1, uniform="ug1")
            self._tip(b, _user_tips[t])
        for i, (t, c) in enumerate((("Отключить сейчас", self._user_kill),
                                    ("Отозвать и удалить", self._user_revoke),
                                    ("Экспорт конфига…", self._user_export)),
                                   start=3):
            b = self._mk_btn(bf, t, c)
            b.grid(row=0, column=i, sticky="ew", padx=2)
            bf.columnconfigure(i, weight=1, uniform="ug2")
            self._tip(b, _user_tips[t])
        cb_v = self._tw(ttk.Checkbutton(bf, variable=self.verbose,
                                     command=self._on_verbose_toggle),
                       "Подробный вывод")
        cb_v.grid(row=0, column=6, sticky="e", padx=(8, 2))
        self._tip(cb_v, "Служебные строки юзер-операций (ck-client, user-cert).\n"
                  "Состояние запоминается для каждого сервера.")

        self.logframe_usr = self._tw(ttk.LabelFrame(f), "Лог")
        self.logframe_usr.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.logw_usr = tk.Text(self.logframe_usr, height=10, wrap="none",
                                font=("Consolas", 9))
        sb = ttk.Scrollbar(self.logframe_usr, command=self.logw_usr.yview)
        self.logw_usr.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.logw_usr.pack(fill="both", expand=True, padx=4, pady=4)

    # ---- серверы ----
    def _srv_mark(self, s):
        """Метка у имени. Развёрнутость решает флаг deployed (его ставит
        cloak=ok), а не формальность шагов: иначе сервер с пропущенным
        необязательным шагом (sysupd «отменено») выглядит неразвёрнутым.
          ✓ — развёрнут, все шаги ok
          ✓- — развёрнут, но есть пропущенные/не выполненные шаги
          ⚠ — где-то warn/fail (выше любого развёрнут-состояния)
          (пусто) — не развёрнут / deployed снят после провала probe"""
        steps = s.get("steps") or {}
        vals = [r.get("st") for r in steps.values()]
        if any(v in ("warn", "fail") for v in vals):
            return " ⚠"
        if s.get("deployed"):
            if all(k in steps for k, _ in self.STEPS) and \
                    all(v == "ok" for v in vals):
                return " ✓"
            return " ✓-"
        return ""

    def _srv_online_txt(self, s, which):
        # cloak на неразвёрнутом: TCP-проба не отличит наш ck-server от
        # чужого TLS на 443 — колонка неприменима, а не «недоступен»
        if which == "cloak" and not s.get("deployed"):
            return "—"
        ok = self._online.get((s["name"], which))
        if ok is None:
            return "…"   # первая проба ещё не вернулась
        return "✓" if ok else "✗"

    def _refresh_servers(self):
        # delete+insert сносит выделение — сохраняем его, иначе
        # _sel_srv_silent() вернёт None и спиннер шага спрячется
        sel = self.srv_tv.selection()
        keep = sel[0] if sel else None
        self._no_sel_ev = True
        try:
            self.srv_tv.delete(*self.srv_tv.get_children())
            for s in self.data["servers"]:
                name = s["name"]
                self.srv_tv.insert(
                    "", "end", iid=name,
                    values=(name + self._srv_mark(s),
                            self._srv_online_txt(s, "ssh"),
                            self._srv_online_txt(s, "cloak")))
            if keep and self.srv_tv.exists(keep):
                self.srv_tv.selection_set(keep)
        finally:
            self._no_sel_ev = False
        self._apply_opt_lock()

    # ---- онлайн-индикаторы: TCP-коннект на порты, без логина ----
    # ssh → ssh_port сервера. cloak → ck_port, но только у развёрнутого
    # сервера: у чужого/незадеплоенного хоста на 443 может отвечать что
    # угодно (TCP-проба не отличит наш ck-server от постороннего TLS).
    def _online_loop(self):
        while True:
            for s in list(self.data.get("servers", [])):
                host = s.get("host")
                self._spawn_probe(s.get("name"), "ssh", host,
                                  int(s.get("ssh_port") or 22))
                ck = s.get("ck_port")
                if s.get("deployed") and ck:
                    self._spawn_probe(s.get("name"), "cloak", host,
                                      int(ck))
                elif self._online.get((s.get("name"), "cloak")) is not None:
                    self._online[(s.get("name"), "cloak")] = None
                    self.ui(lambda n=s.get("name"): self._srv_online_set(
                            n, "cloak"))
            time.sleep(15)

    def _spawn_probe(self, name, which, host, port):
        # в полёте на (сервер, порт) не больше одной пробы — иначе на
        # «чёрной дыре» потоки плодились бы каждые 15 с
        key = (name, which)
        if key in self._probing:
            return
        self._probing.add(key)
        threading.Thread(target=self._probe_srv,
                         args=(name, which, host, port),
                         daemon=True).start()

    def _probe_srv(self, name, which, host, port):
        key = (name, which)
        try:
            ok = False
            if host:
                try:
                    socket.create_connection((host, port),
                                             timeout=4).close()
                    ok = True
                except OSError:
                    pass
            prev = self._online.get(key)
            self._online[key] = ok
            if prev != ok:
                self.ui(lambda: self._srv_online_set(name, which))
        finally:
            self._probing.discard(key)

    def _srv_online_set(self, name, which):
        s = next((x for x in self.data["servers"]
                  if x["name"] == name), None)
        if s and self.srv_tv.exists(name):
            self.srv_tv.set(name, which, self._srv_online_txt(s, which))

    def _apply_opt_lock(self):
        """Порт/протокол зашиты в конфиги юзеров — правка на развёрнутом
        сервере без пересборки бандлов ломает связность. Лочим до
        сброса/передеплоя. Домен маскировки остаётся — он только
        дефолт для новых юзеров и RedirAddr."""
        s = self._sel_srv_silent()
        locked = bool(s and s.get("deployed"))
        self.w_ckport.config(state="disabled" if locked else "normal")
        self.w_proto.config(state="disabled" if locked else "readonly")

    def _sel_srv(self):
        sel = self.srv_tv.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, self.t("Выбери сервер слева"))
            return None
        return self._srv_by_name(sel[0])

    def _on_srv_select(self):
        if self._no_sel_ev:
            return
        s = self._sel_srv_silent()
        if s:
            self._log_name = s["name"]
            self._log_load(s["name"])
            self.v_mask.set(s.get("mask_domain", "www.bing.com"))
            self.v_proto.set(s.get("proto", "udp"))
            self.v_ckport.set(str(s.get("ck_port") or "443"))
            self._apply_opt_lock()
            self._fill_users_local(s)
            self._fill_steps()

    def _fill_users_local(self, s):
        """Мгновенный вид по локальному реестру (без SSH). UID/expiry
        известны только для юзеров, созданных админкой; чужие UID
        появятся после «Обновить»."""
        self.users_tv.delete(*self.users_tv.get_children())
        for u in s.get("users", []):
            exp = u.get("expiry", 0)
            self.users_tv.insert("", "end", iid=u["uid"], values=(
                u["cn"], u.get("sessions", "?"),
                _fmt_limits(u.get("up_rate"), u.get("down_rate")),
                _fmt_quota(u.get("up_credit"), u.get("down_credit")),
                time.strftime("%d.%m.%Y", time.localtime(exp)) if exp else "—",
                u.get("mask") or s.get("mask_domain", ""), ""))
        self._usr_note = ("%s — кэш админки, «Обновить» покажет "
                          "данные с сервера", (s["name"],))
        self.v_users_srv.set(self.t(self._usr_note[0]) % self._usr_note[1])

    def _sel_srv_silent(self):
        sel = self.srv_tv.selection()
        return self._srv_by_name(sel[0]) if sel else None

    def _srv_move(self, d):
        """▲/▼ — порядок серверов в списке (и в data.json)."""
        s = self._sel_srv_silent()
        if not s:
            return
        srvs = self.data["servers"]
        i = srvs.index(s)
        j = i + d
        if not (0 <= j < len(srvs)):
            return
        srvs[i], srvs[j] = srvs[j], srvs[i]
        save_data(self.data)
        self._refresh_servers()  # выбор по iid=name переживает rebuild

    def _srv_add(self):
        d = ServerDialog(self)
        if d.result:
            d.result["users"] = []
            self.data["servers"].append(d.result)
            save_data(self.data)
            self._refresh_servers()

    def _srv_edit(self):
        s = self._sel_srv()
        if not s:
            return
        d = ServerDialog(self, srv=s)
        if d.result:
            s.update(d.result)
            save_data(self.data)
            self._refresh_servers()

    def _srv_del(self):
        s = self._sel_srv()
        if not s:
            return
        if messagebox.askyesno(APP_NAME, self.t("Удалить сервер «%s» из списка?\n\n"
                               "Локальные конфиги юзеров удалятся.\n"
                               "SSH-ключ остаётся в %s —\n"
                               "им можно зайти на сервер и потом.\n"
                               "Сам сервер не трогаем.")
                               % (s["name"], os.path.join(
                                   APP_DIR, "keys",
                                   re.sub(r"[^\w-]", "_", s["name"])))):
            self.data["servers"].remove(s)
            save_data(self.data)
            self._logs.pop(s["name"], None)
            # бандлы юзеров сносим; SSH-ключ НЕ трогаем — он стоит на сервере
            # в authorized_keys и может быть единственным способом зайти
            shutil.rmtree(os.path.join(BUNDLES_DIR, s["name"]), ignore_errors=True)
            self._refresh_servers()

    # ---- движок шагов ----
    def ask(self, title, text):
        """messagebox.askyesno из рабочего потока — маршалится в UI-поток."""
        ev = threading.Event()
        box = []

        def q():
            try:
                box.append(messagebox.askyesno(title, text))
            finally:
                ev.set()
        self.ui(q)
        ev.wait()
        return bool(box and box[0])

    def _warn_own_tunnel(self, s):
        """True — продолжать; False — юзер отказался.
        Предупреждает, если сервер — эндпоинт активного VPN-клиента:
        SSH к нему идёт через этот же туннель, и его разрыв
        (ребут/сброс/перезапуск OpenVPN|Cloak) убьёт операцию."""
        ep = vpn_active_host()
        mine = (s.get("host") or "").strip().lower().rstrip(".")
        if not ep or not mine:
            return True
        other = ep.strip().lower().rstrip(".")
        same = mine == other
        if not same:
            try:  # разные записи одного хоста (домен vs IP)
                same = socket.gethostbyname(mine) == socket.gethostbyname(other)
            except OSError:
                pass
        if not same:
            return True
        msg = self.t(
            "«%s» — сейчас это сервер твоего АКТИВНОГО VPN.\n\n"
            "SSH-сессия идёт через этот же туннель: при его разрыве\n"
            "(перезапуск OpenVPN/Cloak, смена фаервола, ребут, сброс)\n"
            "связь оборвётся и операция зависнет или не завершится.\n\n"
            "Рекомендуется отключить VPN. Продолжить?") % s["name"]
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno(APP_NAME, msg)
        return self.ask(APP_NAME, msg)

    def ask_port(self, title, text, default=443):
        """Ввод номера порта из рабочего потока; None — отмена."""
        ev = threading.Event()
        box = []

        def q():
            try:
                box.append(simpledialog.askinteger(
                    title, text, initialvalue=default,
                    minvalue=1, maxvalue=65535))
            finally:
                ev.set()
        self.ui(q)
        ev.wait()
        return box[0] if box else None

    def _fill_steps(self):
        s = self._sel_srv_silent()
        st = (s or {}).get("steps", {})
        self.steps_tv.delete(*self.steps_tv.get_children())
        mark = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "–"}
        for key, title in self.STEPS:
            r = st.get(key, {})
            self.steps_tv.insert("", "end", iid=key, text=self.t(title),
                                 values=(mark.get(r.get("st"), "…"),
                                         r.get("note", "")))

    def _steps_reset(self):
        """Проверить статусы: read-only аудит каждого шага на сервере."""
        s = self._sel_srv()
        if not s:
            return

        def work():
            try:
                ssh = SSH(s, self.say)
                ssh.preflight()
                self.say(self.t("=== Аудит статусов на «%s» ===") % s["name"])
                out = ssh.run_script("probe.sh", timeout=120)
            except Exception:
                # probe не отработал (SSH/сеть/sudo) — реальное
                # состояние неизвестно, старой ✓ верить нельзя
                if s.get("deployed"):
                    s["deployed"] = False
                    save_data(self.data)
                    self.say(self.t("  метку «развёрнут» снял — сервер не "
                             "отвечает, состояние не проверено"))
                    self.ui(self._refresh_servers)
                raise
            if self._apply_probe(s, out):
                self.ui(self._fill_steps)
                self.ui(self._refresh_servers)
        self._worker(work)

    # Токены probe.sh → RU-шаблоны (переводятся через self.t при разборе).
    # Заметки без токена («U=... H=...», «ubuntu 24.04 / x86_64») — данные,
    # выводятся как есть.
    PROBE_NOTES = {
        "ak_count":        "ключей в authorized_keys: %s",
        "ak_missing":      "authorized_keys пуст/отсутствует",
        "fw_none":         "фаервола нет",
        "fw_ok":           "%s; открыто: %s",
        "reboot_required": "нужна перезагрузка",
        "apt_stale":       "apt update не выполнялся",
        "pending":         "не установлено обновлений: %s",
        "clean":           "система обновлена, ребут не требуется",
        "pkgs_missing":    "не хватает: %s",
        "pkgs_ok":         "все пакеты есть",
        "ovpn_ok":         "proto=%s mgmt:%s",
        "ovpn_down":       "openvpn-server@server не активен",
        "nat_ok":          "forward+masquerade на месте",
        "nat_no_masq":     "forward=1, masquerade 10.8.0.0/24 не найден",
        "nat_no_fwd":      "masquerade есть, нет forward-правил tun0",
        "no_forward":      "ip_forward=0",
        "cloak_ok":        "active, tcp/%s слушает, маскировка %s",
        "cloak_no_listen": "active, но порт %s не слушает/нет конфига",
        "cloak_down":      "ck-server не активен",
        "cloak_busy":      "ck-server не активен, tcp/%s занят %s:%s",
    }

    def _probe_note(self, raw):
        """Сырой note из probe.sh → локализованная строка.
        Неизвестные токены/данные возвращаем как есть."""
        tok, _, rest = raw.partition(":")
        tmpl = self.PROBE_NOTES.get(tok)
        if tmpl is None:
            return raw
        if not rest:
            return self.t(tmpl)
        args = [a.strip() for a in rest.split(":")]
        if len(args) != tmpl.count("%s"):
            return self.t(tmpl)  # безопаснее, чем TypeError при лишних ':'
        return self.t(tmpl) % tuple(args)

    def _apply_probe(self, s, out):
        """Разобрать вывод probe.sh → steps/deployed/reboot_required.
        Возвращает False, если probe не выдал ни одного шага."""
        known = dict(self.STEPS)
        st = {}
        reboot = False
        for m in re.finditer(r"===STEP_(\w+)===\s*\n(\w+)\|([^\n]*)", out):
            key, stt, raw = m.group(1), m.group(2), m.group(3).strip()
            if key in known:
                if raw.split(":", 1)[0] == "reboot_required":
                    reboot = True
                note = self._probe_note(raw)
                st[key] = {"st": stt, "note": note}
                self.say("  %s → %s: %s" % (key, stt, note))
        if not st:
            self.say(self.t("!! probe.sh не вернул данных"))
            return False
        s["steps"] = st
        # Флаг reboot_required: по токену probe, плюс бэкофф на старые
        # записи с текстовой заметкой
        note_su = st.get("sysupd", {}).get("note", "")
        if reboot or "reboot" in note_su or "перезагруз" in note_su:
            s["reboot_required"] = True
        else:
            s.pop("reboot_required", None)
        s["deployed"] = st.get("cloak", {}).get("st") == "ok"
        save_data(self.data)
        return True

    def _srv_purge(self):
        """Вернуть сервер в состояние до деплоя: purge-dgcloak.sh по SSH
        (восстанавливает прежнего владельца порта/фаервол по снимку
        predeploy.env) + чистка реестра."""
        s = self._sel_srv()
        if not s:
            return
        if not self._warn_own_tunnel(s):
            return
        if not messagebox.askyesno(
                APP_NAME,
                self.t("Вернуть «%s» в состояние до деплоя?\n\n"
                "Удалит Cloak, OpenVPN, PKI, юзеров, наши\n"
                "правила фаервола и локальные конфиги юзеров;\n"
                "вернёт прежнего владельца порта, docker-\n"
                "автозапуск и исходный фаервол (по снимку).\n"
                "SSH-доступ и твой юзер НЕ затрагиваются.\n\n"
                "Продолжить?") % s["name"]):
            return

        def work():
            ssh = SSH(s, self.say)
            self.say(self.t("=== Возврат «%s» в исходное состояние ===")
                     % s["name"])
            ck = str(s.get("ck_port") or "443")
            rc = ssh.run_script_stream(
                "purge-dgcloak.sh", ck,
                lambda l: self.say("  " + l), timeout=600)
            for k in ("deployed", "pubkey", "admin_uid", "users",
                      "steps", "reboot_required", "fw_backend"):
                s.pop(k, None)
            # локальные бандлы юзеров — сертификаты мертвы вместе с PKI
            shutil.rmtree(os.path.join(BUNDLES_DIR, s["name"]),
                          ignore_errors=True)
            save_data(self.data)
            self.ui(self._fill_steps)
            self.ui(self._refresh_servers)
            self.say(self.t("=== Возврат завершён (rc=%s) ===") % rc)
        self._worker(work)

    def _srv_reboot(self):
        """Перезагрузка сервера по SSH + ожидание подъёма обратно."""
        s = self._sel_srv()
        if not s:
            return
        if not self._warn_own_tunnel(s):
            return
        if not messagebox.askyesno(
                APP_NAME,
                self.t("Перезагрузить «%s»?\n\n"
                "Сервер будет недоступен ~1 минуту.") % s["name"]):
            return

        def work():
            self.say(self.t("=== Перезагрузка «%s» ===") % s["name"])
            self._reboot_and_wait(s)
        self._worker(work)

    # ---- анимация «выполняется» + heartbeat ----
    # canvas-дуга (270°) поверх ячейки «Статус»: вращается по часовой,
    # 8 кадров × 45° × 125 мс = 1 с на оборот

    def _spin_start(self, key, srv_name=None):
        self._spin_key = key
        self._spin_srv = srv_name   # спиннер виден только в таблице СВОЕГО сервера
        self._spin_i = 0
        self._spin_t0 = self._spin_hb = time.time()
        self._spin_tick()

    def _spin_stop(self):
        self._spin_key = None
        self.spin_cv.place_forget()

    def _spin_tick(self):
        """Крутит спиннер в статусе шага + heartbeat в лог (главный поток)."""
        k = self._spin_key
        if k is None:
            return
        # iid шагов одинаковы у всех серверов — рисуем дугу только
        # если показана таблица того сервера, где идёт операция
        cur = self._sel_srv_silent()
        visible = cur is not None and cur.get("name") == self._spin_srv
        bb = None
        if visible:
            try:
                bb = self.steps_tv.bbox(k, "st")
            except tk.TclError:
                bb = None  # строки нет
        if bb:
            x, y, w, h = bb
            self.spin_cv.place(x=x, y=y, width=w, height=h)
            d = min(w, h) - 6
            cx, cy = w / 2, h / 2
            self.spin_cv.coords(self._spin_arc,
                                cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2)
            # минус — по часовой (canvas считает углы против часовой)
            self.spin_cv.itemconfigure(self._spin_arc,
                                       start=(-45 * self._spin_i) % 360)
        else:
            self.spin_cv.place_forget()
        self._spin_i += 1
        el = time.time() - self._spin_t0
        if el - self._spin_hb >= 30:
            self._spin_hb = el
            self.say(self.t("  …выполняется уже %d мин %d с — процесс жив, "
                     "ждём ответа сервера") % (el // 60, int(el) % 60))
        self.after(125, self._spin_tick)

    def _run_step(self, s, key):
        title = dict(self.STEPS)[key]
        self.say(self.t("=== Шаг: %s ===") % self.t(title))
        st = s.setdefault("steps", {})
        self.ui(self._spin_start, key, s["name"])
        try:
            ssh = SSH(s, self.say)
            stt, note = getattr(self, "_step_" + key)(ssh, s)
        except Exception as e:
            stt, note = "fail", (str(e).splitlines() or ["?"])[-1][:140]
            self.say(self.t("  ОШИБКА: %s") % e)
        st[key] = {"st": stt, "note": note}
        save_data(self.data)
        self.ui(self._spin_stop)
        self.ui(self._fill_steps)
        self.ui(self._refresh_servers)  # deployed-галочка в списке
        self.say("  → %s: %s" % (stt, note))
        return stt in ("ok", "warn", "skip")

    def _step_run_sel(self):
        s = self._sel_srv()
        if not s:
            return
        sel = self.steps_tv.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, self.t("Выбери шаг в таблице"))
            return
        key = sel[0]
        # read-only шаги не рвут туннель — предупреждать только об опасных
        if key not in ("ssh", "key", "audit") and not self._warn_own_tunnel(s):
            return
        self._worker(lambda: self._run_step(s, key))

    def _step_run_all(self):
        s = self._sel_srv()
        if not s:
            return
        if not self._warn_own_tunnel(s):
            return

        def work():
            # реестр может врать (VM откачена на снапшот, сервер переставлен):
            # перед проходом сверяем реальное состояние — probe дешёвый (~1 с),
            # а пропуск «готовых» шагов на пустом сервере — тупик
            if any(v.get("st") == "ok" for v in s.get("steps", {}).values()):
                try:
                    ssh = SSH(s, self.say)
                    ssh.preflight()
                    self.say(self.t("Сверяю статусы с сервером…"))
                    if self._apply_probe(s, ssh.run_script("probe.sh", timeout=120)):
                        self.ui(self._fill_steps)
                        self.ui(self._refresh_servers)
                except Exception as e:
                    # preflight упал (нет sudo и т.п.) — шаг 1 скажет то же
                    # самое внятно; но готовые шаги доверять нельзя
                    self.say(self.t("  probe не прошёл (%s) — иду с первого шага")
                             % str(e).splitlines()[0][:80])
                    s["steps"] = {}
            for key, _t in self.STEPS:
                if s.get("steps", {}).get(key, {}).get("st") == "ok":
                    continue
                if not self._run_step(s, key):
                    self.say(self.t("Остановился на шаге «%s». Исправь и продолжай — "
                             "завершённые шаги не повторятся.")
                             % self.t(dict(self.STEPS)[key]))
                    break
            self.say(self.t("=== Проход завершён ==="))
            if s.get("reboot_required"):
                if self.ask(APP_NAME,
                            self.t("Обновление системы на «%s» требует перезагрузки.\n"
                            "Перезагрузить сервер сейчас?\n\n"
                            "(поднимется через ~1 минуту; завершённые шаги "
                            "деплоя повторять не нужно)") % s["name"]):
                    self._reboot_and_wait(s)
        self._worker(work)

    def _reboot_and_wait(self, s):
        """Перезагрузка через sudo + ожидание подъёма. Статус «нужен reboot»
        снимается только после реального подъёма — иначе лог врёт."""
        ssh = SSH(s, self.say)
        ssh.preflight()
        boot0 = ssh.run("uptime -s", timeout=15).strip()
        self.say(self.t("  отправляю reboot…"))
        try:
            ssh.run(ssh.sudo + "systemctl reboot", timeout=15)
        except Exception:
            pass  # соединение рвётся при перезагрузке — это норма
        deadline = time.time() + 180
        up = False
        while time.time() < deadline:
            time.sleep(6)
            try:
                boot1 = SSH(s, lambda m: None).run("uptime -s",
                                                   timeout=12).strip()
                # «поднялся» = время загрузки изменилось; просто «ответил»
                # не годится — сервер мог ещё не упасть или вовсе не
                # перезагрузиться (reboot без прав)
                if boot1 and boot1 != boot0:
                    up = True
                    break
            except Exception:
                pass
        if up:
            s.pop("reboot_required", None)
            st = s.setdefault("steps", {})
            note_su = st.get("sysupd", {}).get("note", "")
            if "reboot" in note_su or "перезагруз" in note_su:
                st["sysupd"] = {"st": "ok",
                                "note": self.t("обновлено, перезагружен")}
            save_data(self.data)
            self.ui(self._fill_steps)
            self.ui(self._refresh_servers)  # снять ⚠ у имени в списке
            self.say(self.t("  сервер поднялся после перезагрузки"))
        else:
            self.say(self.t("  !! сервер не перезагрузился за 3 минуты "
                     "(или флаг reboot-required остался) — проверь консоль VM"))
        return up

    # ---- шаги ----
    def _step_ssh(self, ssh, s):
        ssh.preflight()
        out = ssh.run("echo U=$(id -un) H=$(hostname)", timeout=20)
        return "ok", out.strip().replace("\n", " ")

    def _step_key(self, ssh, s):
        old_key = s.get("key") or ""
        if s.get("key") or s.get("ppk"):
            # проверяем, что ключ реально работает — VM могли откатить
            # на снапшот без него (ключ «мёртв», пароль спасает как фолбэк)
            s2 = dict(s); s2["password"] = None
            try:
                SSH(s2, self.say).run("true", timeout=15)
                return "ok", self.t("вход по ключу настроен")
            except Exception:
                self.say(self.t("  сохранённый ключ отвергнут — ставлю новый"))
                s.pop("key", None); s.pop("ppk", None)
        if not s.get("password"):
            return "fail", self.t("нет ни пароля, ни ключа")
        # ключ ставим без вопросов: операция безопасна,
        # пароль остаётся запасным входом
        self.say(self.t("  генерирую ключ ed25519…"))
        kg = find_exe(["ssh-keygen.exe", "ssh-keygen"],
                      [r"C:\Windows\System32\OpenSSH"])
        if not kg:
            raise SSHErr(self.t("не найден ssh-keygen (Windows OpenSSH)"))
        # ключи по подпапкам: keys\<сервер>\key(.pub) — общая папка
        # на всех серверов в один уровень быстро превращается в свалку
        kd = os.path.join(APP_DIR, "keys",
                          re.sub(r"[^\w-]", "_", s["name"]))
        os.makedirs(kd, exist_ok=True)
        # наш старый сгенерированный ключ (имя от старого названия
        # сервера) — после успешной установки нового сносим
        kp = os.path.join(kd, "key")
        if not os.path.isfile(kp):
            r = subprocess.run([kg, "-t", "ed25519", "-N", "", "-f", kp],
                               capture_output=True, text=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                raise SSHErr(self.t("ssh-keygen не сработал: %s")
                             % (r.stderr or r.stdout))
        pub = open(kp + ".pub", encoding="ascii").read().strip()
        self.say(self.t("  ставлю публичный ключ на сервер…"))
        ssh.install_pubkey(pub)
        # метка «ключ отвергнут» больше не верна — сбрасываем, чтобы
        # проверка ниже действительно ходила новым ключом, а не паролем
        SSH._auth_pw_hosts.discard(
            "%s:%s" % (s.get("host"), s.get("ssh_port") or 22))
        # проверка: вход по ключу отдельным подключением
        s2 = dict(s)
        s2["key"] = kp
        SSH(s2, self.say).preflight()
        s["key"] = kp
        save_data(self.data)
        if (old_key and old_key != kp
                and os.path.basename(old_key).endswith("_ed25519")
                and os.path.normpath(old_key).startswith(
                    os.path.normpath(kd + os.sep))):
            for f in (old_key, old_key + ".pub"):
                try:
                    os.remove(f)
                except OSError:
                    pass
            self.say(self.t("  старый ключ %s удалён")
                     % os.path.basename(old_key))
        return "ok", self.t("ключ установлен: %s") % os.path.basename(kp)

    def _predeploy_note(self, ssh, kv):
        """Дописать факт в /etc/dgcloak/predeploy.env — снимок для revert
        («Вернуть сервер»). Не критично: без файла revert просто сносит наше."""
        try:
            ssh.run("%sbash -c 'mkdir -p /etc/dgcloak && echo %s >> "
                    "/etc/dgcloak/predeploy.env'"
                    % (ssh.sudo, shlex.quote(kv)), timeout=15)
        except Exception as e:  # noqa: BLE001
            self.say("  predeploy-snapshot: %r" % e)

    def _predeploy_docker_pols(self, ssh, names):
        """Записать исходный restart-policy контейнеров до того, как мы
        выставим им restart=no — для «Вернуть сервер»."""
        for c in names:
            try:
                pol = ssh.run("%sdocker inspect -f "
                              "'{{.HostConfig.RestartPolicy.Name}}' %s"
                              % (ssh.sudo, shlex.quote(c)),
                              timeout=30).strip()
            except Exception:  # noqa: BLE001
                continue
            if pol:
                self._predeploy_note(ssh, "DOCKER_POLICY_%s=%s" % (c, pol))

    def _step_audit(self, ssh, s):
        out = ssh.run_script("detect.sh", timeout=60)
        for ln in out.splitlines():
            self.say("  " + ln)
        # снимок «до деплоя» для revert — пока ещё ничего не меняли
        try:
            for ln in ssh.run_script("predeploy-save.sh",
                                     timeout=30).splitlines():
                self.say("  " + ln)
        except Exception as e:  # noqa: BLE001 — снимок не критичен
            self.say(self.t("  predeploy-снимок не записан: %s") % e)
        pkg = parse_section(out, "PKG").strip()
        ossec = parse_section(out, "OS")
        osid = re.search(r"ID=(\S+)", ossec)
        ver = re.search(r"VERSION=(\S+)", ossec)
        arch = parse_section(out, "ARCH").strip()
        # ^ обязателен: иначе первым матчится маркер ===EXT_IF=== → "=="
        m = re.search(r"^EXT_IF=(\S+)", out, re.M)
        if m:
            s["ext_if"] = m.group(1)
        pub = parse_section(out, "PUBIP")
        if pub and pub != "?":
            s["public_ip"] = pub
        sshd = [l.strip() for l in parse_section(out, "SSHD_PORTS").splitlines()
                if l.strip().isdigit()]
        if sshd:
            s["sshd_ports"] = sshd
        s["has_docker"] = ("docker" in out.lower()
                           and "no docker" not in out.lower())
        m = re.search(r"free_mb=(\d+)", parse_section(out, "DISK"))
        if m:
            s["disk_free_mb"] = int(m.group(1))
        m = re.search(r"mem_avail_mb=(\d+)", parse_section(out, "MEM"))
        if m:
            s["mem_avail_mb"] = int(m.group(1))
        save_data(self.data)
        if pkg != "apt":
            return "fail", self.t("не apt-дистрибутив (pkg=%s) — не поддерживается") % pkg
        os_id = osid.group(1) if osid else "?"
        os_ver = ver.group(1) if ver else "?"
        supported = {"ubuntu": {"20.04", "22.04", "24.04", "26.04"},
                     "debian": {"11", "12", "13"}}
        if os_id not in supported or os_ver not in supported[os_id]:
            if not self.ask(
                    APP_NAME,
                    self.t("«%s»: %s %s не из поддерживаемых\n"
                    "(Ubuntu 20.04/22.04/24.04/26.04, Debian 11/12/13).\n\n"
                    "Продолжить на свой страх и риск?")
                    % (s["name"], os_id, os_ver)):
                return "fail", self.t("%s %s не поддерживается") % (os_id, os_ver)
        res = []
        if s.get("disk_free_mb", 9999) < 400:
            res.append(self.t("мало места на диске: %d МБ") % s["disk_free_mb"])
        if s.get("mem_avail_mb", 9999) < 128:
            res.append(self.t("мало RAM: %d МБ свободно") % s["mem_avail_mb"])
        txt = "%s %s / %s" % (os_id, os_ver, arch)
        if res:
            return "warn", "%s; %s" % (txt, "; ".join(res))
        return "ok", txt

    def _step_fw(self, ssh, s):
        out = ssh.run_script("fw-detect.sh", timeout=60)
        m = re.search(r"FW=(\S+)", parse_section(out, "FW_BACKEND"))
        fw = m.group(1) if m else "none"
        s["fw_backend"] = fw
        self._predeploy_note(ssh, "FW=" + fw)  # какой фаервол был до нас
        for ln in (self.t("--- правила ---\n") + parse_section(out, "FW_RULES")
                   + self.t("\n--- слушают снаружи (tcp) ---\n")
                   + parse_section(out, "LISTEN_TCP")).splitlines():
            self.say("  " + ln)
        if fw == "none":
            ports = sorted({str(s.get("ssh_port", 22))}
                           | set(s.get("sshd_ports", [])), key=int)
            ck = self.v_ckport.get().strip() or "443"
            # ставим nftables без подтверждения: это суть деплоя,
            # anti-lockout canary откатит правила при потере SSH
            self.say(self.t("  фаервола нет — ставлю nftables "
                     "(INPUT DROP + ssh %s + cloak tcp/%s; "
                     "откат через 120 с при потере SSH)")
                     % (",".join(ports), ck))
            ssh.run_script("fw-install.sh", "%s %s" % (",".join(ports), ck),
                           timeout=300)
            # canary: на сервере 120с откат; новое ssh-подключение подтверждает
            ssh.run("touch /tmp/dgcloak-fw-ok", timeout=30)
            self.say(self.t("  firewall подтверждён (rollback отменён)"))
            s["fw_backend"] = "nftables-dg"
            s["ck_port"] = ck
            return "ok", "nftables: ssh=%s cloak=%s" % (",".join(ports), ck)
        if fw == "iptables-custom":
            return "warn", self.t("чужие правила iptables — открой «Управление фаерволом»")
        return "ok", fw

    def _step_sysupd(self, ssh, s):
        # full-upgrade качает .deb в кэш — проверяем место свежим запросом
        try:
            free = int(ssh.run("df -m / | awk 'NR==2{print $4}'",
                               timeout=15).strip())
            s["disk_free_mb"] = free
        except Exception:
            free = s.get("disk_free_mb", 0)
        if free and free < 1024 and not self.ask(
                APP_NAME,
                self.t("На «%s» свободно %d МБ на диске.\n"
                "Обновлению может не хватить места (нужно ~1 ГБ).\n\n"
                "Продолжить?") % (s["name"], free)):
            return "skip", self.t("мало места на диске (%d МБ)") % free
        if not self.ask(APP_NAME,
                        self.t("Полное обновление системы на «%s» "
                        "(apt update + full-upgrade + autoremove)?\n\n"
                        "На свежеустановленной системе это может занять\n"
                        "10–30 минут — прогресс виден в логе.\n"
                        "Если обновление потребует перезагрузку,\n"
                        "приложение предложит её в конце.") % s["name"]):
            # отказались от апгрейда — но pending reboot мог остаться
            # с прошлого прогона; тогда статус должен остаться «нужен reboot»
            if "REBOOT" in ssh.run("test -f /var/run/reboot-required "
                                   "&& echo REBOOT || true", timeout=15):
                s["reboot_required"] = True
                save_data(self.data)
                return "warn", self.t("обновлено ранее, нужна перезагрузка")
            return "skip", self.t("отменено пользователем")
        # стримим вывод apt в лог — на свежем ISO апдейтов сотни,
        # без живого вывода шаг выглядит зависшим
        lines = []
        rc = ssh.run_script_stream(
            "sysupdate.sh", "",
            lambda l: (lines.append(l), self.say("  " + l)),
            timeout=1800)
        if rc != 0:
            return "fail", "sysupdate rc=%s" % rc
        out = "\n".join(lines)
        if "===REBOOT===" in out:
            s["reboot_required"] = True
            save_data(self.data)
            return "warn", self.t("обновлено, нужна перезагрузка")
        s.pop("reboot_required", None)
        return "ok", self.t("обновлено")

    def _step_pkgs(self, ssh, s):
        out = ssh.run_script("pkgs.sh", "check", timeout=90)
        for ln in out.splitlines():
            self.say("  " + ln)
        missing = [l.split("=")[0] for l in
                   parse_section(out, "PKGS").splitlines()
                   if l.strip().endswith("=-")]
        latest = parse_section(out, "CK_LATEST").strip()
        if missing:
            self.say(self.t("  ставлю недостающие пакеты: %s") % ", ".join(missing))
            rc = ssh.run_script_stream(
                "pkgs.sh", "install",
                lambda l: self.say("  " + l), timeout=600)
            if rc != 0:
                return "fail", "pkgs install rc=%s" % rc
            out = ssh.run_script("pkgs.sh", "check", timeout=90)
            missing = [l.split("=")[0] for l in
                       parse_section(out, "PKGS").splitlines()
                       if l.strip().endswith("=-")]
            # до install на minimal-образе не было curl → latest пустой
            latest = parse_section(out, "CK_LATEST").strip() or latest
        if missing:
            return "fail", self.t("не встали: %s") % ",".join(missing)
        return "ok", self.t("все пакеты есть; cloak latest: %s") % (latest or "?")

    def _step_ovpn(self, ssh, s):
        proto = self.v_proto.get()
        rc = ssh.run_script_stream(
            "deploy-openvpn.sh", proto,
            lambda l: self.say("  " + l), timeout=900)
        if rc != 0:
            return "fail", "deploy-openvpn rc=%s" % rc
        s["proto"] = proto
        return "ok", "proto=%s, mgmt :7505" % proto

    def _step_nat(self, ssh, s):
        fw = s.get("fw_backend")
        if not fw:
            out = ssh.run_script("fw-detect.sh", timeout=60)
            m = re.search(r"FW=(\S+)", parse_section(out, "FW_BACKEND"))
            fw = m.group(1) if m else "none"
            s["fw_backend"] = fw
        ck = str(s.get("ck_port") or self.v_ckport.get().strip() or "443")
        if fw == "ufw":
            out = ssh.run_script("deploy-net-ufw.sh", timeout=300)
            for ln in out.splitlines():
                self.say("  " + ln)
        elif fw in ("nftables-dg", "nftables"):
            out = ssh.run_script("nat-enable.sh", timeout=120)
            for ln in out.splitlines():
                self.say("  " + ln)
        elif fw == "firewalld":
            # На firewalld >= 0.9 masquerade сам по себе forward не
            # открывает — нужен --add-forward на зоне. На старых версиях
            # опции нет (masq открывал forward сам) — ошибку глушим.
            ssh.run("%sbash -c 'firewall-cmd --permanent --add-masquerade "
                    "--zone=public && "
                    "firewall-cmd --permanent --add-forward --zone=public "
                    "2>/dev/null; "
                    "firewall-cmd --reload && "
                    "echo net.ipv4.ip_forward=1 "
                    "> /etc/sysctl.d/99-dgcloak-vpn.conf && "
                    "sysctl -w net.ipv4.ip_forward=1'" % ssh.sudo, timeout=60)
        elif fw in ("iptables-persistent", "iptables-custom"):
            ports = sorted({str(s.get("ssh_port", 22))}
                           | set(s.get("sshd_ports", [])), key=int)
            ssh.run_script("deploy-net-iptables.sh",
                           "%s %s" % (",".join(ports), ck),
                           timeout=300)
            ssh.run("touch /tmp/dgcloak-fw-ok", timeout=30)
            self.say(self.t("  firewall подтверждён (rollback отменён)"))
        elif fw == "none":
            return "fail", self.t("фаервола нет — сначала выполни шаг «Фаервол»")
        else:
            return "fail", self.t("неизвестный фаервол: %s") % fw
        # Запоминаем реальный внешний интерфейс — для аудита/диагностики
        ext = ssh.run("ip route show default | awk '{print $5; exit}'",
                      timeout=15).strip()
        if ext:
            s["ext_if"] = ext
        # Пост-проверка: masq именно 10.8.0.0/24 и forward-правила tun0
        # реально стоят. Иначе «успешный» деплой без интернета —
        # чужие docker/amnezia masq давали ложное ok (живой кейс AWS).
        if fw == "firewalld":
            # Своя ветка: masq в зоне public + forward (опция с 0.9;
            # на старее ответа нет вообще → F=na, пропускаем — там masq
            # включал форвардинг сам).
            chk = ssh.run(
                "%sbash -c 'M=no; firewall-cmd --query-masquerade "
                "--zone=public >/dev/null 2>&1 && M=yes;"
                "O=$(firewall-cmd --query-forward --zone=public 2>/dev/null);"
                "F=na; [ \"$O\" = yes ] && F=yes; [ \"$O\" = no ] && F=no;"
                "echo V:NAT=$M:FWD=$F'" % ssh.sudo, timeout=30)
            mv = re.search(r"V:NAT=(\w+):FWD=(\w+)", chk)
            m_v, f_v = (mv.group(1), mv.group(2)) if mv else ("?", "?")
            if m_v != "yes" or f_v == "no":
                return "fail", self.t(
                    "NAT/форвардинг не подтвердился (masq=%s, fwd_rules=%s)"
                    % (m_v, f_v))
        else:
            # NB: один sudo на всю команду — в pw-режиме пароль читается
            # из stdin один раз, второй sudo в цепочке получает EOF.
            chk = ssh.run(
                "%sbash -c 'M=$(iptables -t nat -S POSTROUTING 2>/dev/null "
                "| grep -cE \"10\\.8\\.0\\.0/24 .*MASQUERADE\");"
                "N=$(nft list chain inet dgcloak postrouting 2>/dev/null "
                "| grep -c \"10\\.8\\.0\\.0/24.*masquerade\");"
                "F=$(iptables -S 2>/dev/null | grep -c tun0);"
                "G=$(nft list chain inet dgcloak forward 2>/dev/null "
                "| grep -c tun0);"
                "echo V:NAT=$((M+N)):FWD=$((F+G))'" % ssh.sudo, timeout=30)
            mv = re.search(r"V:NAT=(\d+):FWD=(\d+)", chk)
            nat_n = int(mv.group(1)) if mv else 0
            fwd_n = int(mv.group(2)) if mv else 0
            if not nat_n or not fwd_n:
                return "fail", self.t(
                    "NAT/форвардинг не подтвердился (masq=%s, fwd_rules=%s)"
                    % (nat_n, fwd_n))
        # порт Cloak должен быть открыт снаружи на любом бэкенде
        try:
            ssh.run_script("fw-manage.sh", "allow tcp %s" % ck, timeout=60)
            s["ck_port"] = ck
        except Exception as e:
            return "warn", (self.t("NAT ok (%s), но порт Cloak %s/tcp не открылся: %s")
                            % (fw, ck, str(e)[:80]))
        return "ok", self.t("NAT через %s, Cloak tcp/%s открыт") % (fw, ck)

    def _port_owner(self, ssh, ck):
        """port-owner.sh → (owner|None, [stopped-amnezia-автозапуск…])."""
        owner, autostart = None, []
        for ln in ssh.run_script("port-owner.sh", ck, timeout=30).splitlines():
            ln = ln.strip()
            if ln.startswith("OWNER="):
                owner = ln[6:]
            elif ln.startswith("AUTOSTART="):
                autostart = [x for x in ln[10:].split(",") if x]
        return owner, autostart

    def _step_cloak(self, ssh, s):
        mask = self.v_mask.get().strip() or "www.bing.com"
        proto = self.v_proto.get()
        ck = str(s.get("ck_port") or self.v_ckport.get().strip() or "443")

        # --- префлайт: свободен ли порт, кто держит, что с этим делать ---
        for _try in range(5):
            owner, autostart = self._port_owner(ssh, ck)
            if owner is None or owner == "proc:ck-server":
                # свободен / наш же — но есть ли stopped amnezia с
                # автозапуском, которая воскреснет и отнимет порт?
                if autostart and self.ask(APP_NAME, self.t(
                        "На «%s» остановленные Amnezia-контейнеры с "
                        "автозапуском (%s):\nпосле перезагрузки сервера "
                        "они воскреснут и могут занять порт %s.\n\n"
                        "Отключить их автозапуск? (контейнеры не удаляются)")
                        % (s["name"], ", ".join(autostart), ck)):
                    self._predeploy_docker_pols(ssh, autostart)
                    ssh.run("%sdocker update --restart=no %s"
                            % (ssh.sudo, " ".join(autostart)), timeout=60)
                    self.say(self.t("  автозапуск amnezia-контейнеров "
                             "отключён: %s") % ", ".join(autostart))
                break

            typ, _, who = owner.partition(":")
            freed = False
            # SSH-сервис/процесс гасить нельзя — убьём собственную сессию
            if "ssh" in who.lower():
                self.say(self.t("  порт %s держит «%s» — это SSH, гасить "
                         "нельзя, выбираем другой порт") % (ck, who))
            elif typ == "docker":
                if self.ask(APP_NAME, self.t(
                        "Порт %s занят контейнером «%s».\n"
                        "Загасить его? (остановка + отключение автозапуска,\n"
                        "контейнер НЕ удаляется)") % (ck, who)):
                    self.say(self.t("  гашу контейнер «%s»…") % who)
                    self._predeploy_docker_pols(ssh, [who])
                    self._predeploy_note(ssh, "DOCKER_START=" + who)
                    ssh.run("%sbash -c 'docker stop %s && "
                            "docker update --restart=no %s'"
                            % (ssh.sudo, who, who), timeout=120)
                    self.say(self.t("  контейнер «%s» загашен "
                             "(stop + restart=no)") % who)
                    # заодно у остальных amnezia-* снять автозапуск
                    if who.startswith("amnezia"):
                        others = ssh.run(
                            "%sdocker ps -aq --filter name=amnezia"
                            % ssh.sudo, timeout=30).split()
                        self._predeploy_docker_pols(ssh, others)
                        ssh.run("%sbash -c 'for c in $(docker ps -aq "
                                "--filter name=amnezia); do "
                                "docker update --restart=no $c; done'"
                                % ssh.sudo, timeout=120)
                    time.sleep(1)
                    freed = self._port_owner(ssh, ck)[0] is None
            elif typ == "svc":
                if self.ask(APP_NAME, self.t(
                        "Порт %s занят сервисом «%s».\n"
                        "Загасить его? (systemctl stop + отключение "
                        "автозапуска)") % (ck, who)):
                    self.say(self.t("  гашу сервис «%s»…") % who)
                    self._predeploy_note(ssh, "STOPPED_SVC=" + who)
                    ssh.run("%ssystemctl disable --now %s"
                            % (ssh.sudo, who), timeout=60)
                    self.say(self.t("  сервис «%s» загашен "
                             "(disable --now)") % who)
                    time.sleep(1)
                    freed = self._port_owner(ssh, ck)[0] is None
            else:  # proc — безопасной автоматической остановки нет
                self.say(self.t("  порт %s держит процесс «%s» — "
                         "автоматически не освободить") % (ck, who))
            if freed:
                continue  # перепроверим (плюс AUTOSTART-ветка)
            # не освободили / отказались → другой порт
            alt = self.ask_port(APP_NAME, self.t(
                "Порт %s занят (%s), Cloak на него не встанет.\n"
                "Укажи другой порт:") % (ck, owner), 443)
            if not alt:
                return "fail", self.t("%s занят (%s)") % (ck, owner)
            ck = str(alt)
        else:
            return "fail", self.t("не удалось подобрать свободный порт")

        # шаг 8 (NAT/fw) открывал порт из s["ck_port"] — если тут выбрали
        # другой, открываем его (allow идемпотентен)
        if ck != str(s.get("ck_port") or ""):
            try:
                ssh.run_script("fw-manage.sh", "allow tcp %s" % ck,
                               timeout=60)
                self.say(self.t("  фаервол: открыт tcp/%s") % ck)
            except Exception as e:
                self.say(self.t("  !! не смог открыть tcp/%s в фаерволе: %s")
                         % (ck, str(e)[:80]))

        out = ssh.run_script("deploy-cloak.sh",
                             "%s %s %s %s" % (mask, proto, CK_VERSION, ck),
                             timeout=300)
        pub_k = parse_section(out, "PUB")
        admin_uid = parse_section(out, "ADMIN_UID")
        if pub_k and admin_uid:
            s["pubkey"] = pub_k
            s["admin_uid"] = admin_uid
            s["mask_domain"] = mask
            s["proto"] = proto
            s["ck_ver"] = CK_VERSION
            s["ck_port"] = ck
            s["deployed"] = True
            save_data(self.data)
            return "ok", self.t("ключи получены, маскировка %s") % mask
        return "fail", self.t("нет PUB/ADMIN_UID в выводе deploy-cloak")

    # ---- управление портами фаервола ----
    def _fw_ports(self):
        s = self._sel_srv()
        if not s:
            return
        dlg = getattr(self, "_ports_dlg", None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            dlg.focus_force()
            return

        def work():
            ssh = SSH(s, self.say)
            out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
            self.ui(lambda: PortsDialog(self, s, out))
        self._worker(work)

    # ---- импорт ключей с развёрнутого хоста ----
    def _do_import(self):
        """Подтянуть pubkey/admin_uid с уже развёрнутого сервера."""
        s = self._sel_srv()
        if not s:
            return

        def work():
            ssh = SSH(s, self.say)
            ssh.preflight()
            if not s.get("deployed"):
                # флага нет — проверяем реальное состояние сервера:
                # вдруг развёрнут вне админки или реестр сброшен
                self.say(self.t("=== Аудит статусов на «%s» ===") % s["name"])
                out = ssh.run_script("probe.sh", timeout=120)
                if self._apply_probe(s, out):
                    self.ui(self._fill_steps)
                    self.ui(self._refresh_servers)
                if not s.get("deployed"):
                    msg = (self.t("Сервер «%s» не развёрнут — ключей нет.\n"
                           "Сначала «Развернуть всё».") % s["name"])
                    self.say("!! %s" % msg)
                    self.ui(lambda m=msg: messagebox.showinfo(APP_NAME, m))
                    return
            # один sudo на всё: в режиме sudo-по-паролю пароль в stdin
            # получает только первый sudo — второй молча отдаёт пустоту
            out = ssh.run(
                "%sbash -c '"
                "printf PUB:; cat /etc/ck-server/publickey.txt 2>/dev/null; echo; "
                "printf AUID:; cat /etc/ck-server/adminuid.txt 2>/dev/null; echo; "
                "grep \"^proto \" /etc/openvpn/server/server.conf 2>/dev/null; "
                "grep -A1 -E \"RedirAddr|BindAddr\" /etc/ck-server/ckserver.json "
                "2>/dev/null; true'" % ssh.sudo, timeout=30)
            pub = re.search(r"PUB:(\S+)", out)
            auid = re.search(r"AUID:(\S+)", out)
            if pub and auid and pub.group(1) != "" and auid.group(1) != "":
                s["pubkey"] = pub.group(1)
                s["admin_uid"] = auid.group(1)
                s["deployed"] = True
                m = re.search(r"proto\s+(\w+)", out)
                if m:
                    s["proto"] = m.group(1)
                m = re.search(r'"RedirAddr":\s*"([^"]+)"', out)
                if m:
                    s["mask_domain"] = m.group(1)
                # порт Cloak — иначе после переезда конфиги юзеров
                # получат 443 вместо реального
                m = re.search(r'"BindAddr":\s*\[\s*"[^"]*:(\d+)"', out)
                if m:
                    s["ck_port"] = m.group(1)
                added = self._pull_users(ssh, s)
                save_data(self.data)
                self.say(self.t("Импорт: ключи подтянуты с «%s»%s")
                         % (s["name"], self.t(", юзеров восстановлено: %d")
                            % len(added) if added else ""))
                # бандлы для восстановленных: сертификаты живут на
                # сервере — пересобираем .dgcloak, чтобы user_bundles\
                # не оставался пустым после переезда
                for rec in added:
                    try:
                        mats = parse_cert_bundle(
                            ssh.run_script("user-cert.sh", rec["cn"],
                                           timeout=60))
                        write_user_bundle(
                            s, rec["cn"], rec["uid"], mats,
                            os.path.join(BUNDLES_DIR, s["name"]),
                            rec.get("mask") or "")
                        self.say(self.t("  конфиг «%s» → user_bundles\\%s")
                                 % (rec["cn"], s["name"]))
                    except Exception as e:
                        self.say(self.t("  !! конфиг «%s» не пересобран: %s")
                                 % (rec["cn"], e))
                self.ui(lambda: self._fill_users_local(s))
            else:
                self.say(self.t("!! Не нашёл /etc/ck-server/* — сервер развёрнут?"))
            self.ui(self._refresh_servers)
        self._worker(work)

    # ---- юзеры: зеркало реестра на сервере ----
    # /etc/dgcloak/users.json — связь CN↔UID + маскировка. Серверу он не
    # нужен (Cloak знает UID, PKI знает CN) — это страховка для админки:
    # после удаления сервера из списка / переезда на другой ПК «Подтянуть
    # ключи» восстанавливает юзеров полностью, а не как «?».
    REMOTE_USERS = "/etc/dgcloak/users.json"

    def _push_users(self, ssh, s):
        """Залить s["users"] на сервер. Ошибка не роняет операцию."""
        try:
            tmp = os.path.join(APP_DIR, "tmp-users.json")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(s.get("users", []), f, ensure_ascii=False, indent=1)
            ssh.upload(tmp, "/tmp/dgcloak-users.json")
            ssh.run("%sbash -c 'mkdir -p /etc/dgcloak && "
                    "install -m600 /tmp/dgcloak-users.json %s && "
                    "rm -f /tmp/dgcloak-users.json'"
                    % (ssh.sudo, self.REMOTE_USERS), timeout=20)
            self.vsay(self.t("  реестр юзеров синхронизирован на сервер"))
        except Exception as e:
            self.say(self.t("  !! реестр на сервер не записался: %s") % e)

    def _pull_users(self, ssh, s):
        """Забрать реестр с сервера и влить в локальный (по CN; локальная
        запись выигрывает — она свежее, если вдруг разошлись)."""
        try:
            out = ssh.run("%scat %s 2>/dev/null || echo '[]'"
                          % (ssh.sudo, self.REMOTE_USERS), timeout=20)
            remote = json.loads(out.strip() or "[]")
        except Exception as e:
            self.say(self.t("  реестр юзеров с сервера не прочитан: %s") % e)
            return []
        if not isinstance(remote, list):
            return []
        local = s.setdefault("users", [])
        have = {u.get("cn") for u in local}
        added = [u for u in remote
                 if isinstance(u, dict) and u.get("cn") and u.get("uid")
                 and u["cn"] not in have]
        local.extend(added)
        return added

    # ---- юзеры ----
    def _api(self, s):
        # сначала — развёрнут ли сервер вообще: иначе 30 с таймаута
        # ck-client в пустоту с устаревшими admin_uid/pubkey в реестре
        if not s.get("deployed"):
            raise CloakAPIErr(self.t("Сервер «%s» не развёрнут (по реестру) — "
                              "сделай «Развернуть всё» или «Проверить статусы»")
                              % s["name"])
        ck = find_ck_client(self.data)
        if not ck:
            try:
                ck = download_ck_client(self.say)
            except Exception as e:
                raise CloakAPIErr(
                    self.t("Не найден ck-client.exe и не скачался (%s) — "
                    "укажи путь в data.json или положи рядом") % e)
            self.say("  ck-client.exe → %s" % ck)
        if not s.get("admin_uid") or not s.get("pubkey"):
            raise CloakAPIErr(self.t("Нет admin_uid/pubkey — сделай «Импорт ключей» "
                              "или деплой"))
        return CloakAPI(ck, s, self.say, vlog=self.vsay)

    def _users_refresh(self):
        s = self._sel_srv()
        if not s:
            return

        def work():
            ssh = SSH(s, self.say)
            # список UID из admin-API
            api = self._api(s)
            api.start()
            try:
                uids = api.list_users()
            finally:
                api.stop()
            uid_map = {u.get("UID"): u for u in uids if isinstance(u, dict)}
            # кто онлайн — через mgmt; на старых деплоях порта нет — доустанавливаем
            online = set()
            ssh.upload(os.path.join(SCRIPTS_DIR, "ovpn-mgmt.py"),
                       "/tmp/ovpn-mgmt.py")
            try:
                out = ssh.run("%senv DG_LANG=%s python3 /tmp/ovpn-mgmt.py clients"
                              % (ssh.sudo, _LANG), timeout=20)
                online = {l.strip() for l in out.splitlines() if l.strip()}
            except Exception as e:
                self.say(self.t("  mgmt не отвечает (%s) — включаю enable-mgmt…")
                         % str(e).splitlines()[-1][:120])
                try:
                    ssh.run_script("enable-mgmt.sh", timeout=60)
                    out = ssh.run("%senv DG_LANG=%s python3 /tmp/ovpn-mgmt.py clients"
                                  % (ssh.sudo, _LANG), timeout=20)
                    online = {l.strip() for l in out.splitlines() if l.strip()}
                    self.say(self.t("  mgmt включён"))
                except Exception as e2:
                    self.say(self.t("  mgmt так и недоступен: %s") % e2)
            # cn ↔ uid из нашей базы
            known = {u["uid"]: u for u in s.get("users", [])}
            rows = []  # (полный uid, кортеж_для_таблицы)
            for uid, u in uid_map.items():
                kn = known.get(uid, {})
                exp = u.get("ExpiryTime", 0)
                rows.append((uid, (kn.get("cn", "?"),
                             u.get("SessionsCap", "?"),
                             _fmt_limits(u.get("UpRate"), u.get("DownRate")),
                             _fmt_quota(u.get("UpCredit"), u.get("DownCredit")),
                             time.strftime("%d.%m.%Y", time.localtime(exp))
                             if exp else "—",
                             kn.get("mask") or s.get("mask_domain", ""),
                             "●" if kn.get("cn") in online else "")))
            def fill():
                if self._sel_srv_silent() is not s:
                    return  # сервер уже переключён — не затирать чужим списком
                self.users_tv.delete(*self.users_tv.get_children())
                for uid, r in sorted(rows, key=lambda x: x[1][0]):
                    self.users_tv.insert("", "end", iid=uid, values=r)
                self._usr_note = ("%s — актуальные данные с сервера",
                                  (s["name"],))
                self.v_users_srv.set(self.t(self._usr_note[0])
                                     % self._usr_note[1])
            self.ui(fill)
            self.say(self.t("Юзеров: %s, онлайн: %s") % (len(rows), len(online)))
        self._worker(work)

    def _selected_user(self):
        sel = self.users_tv.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, self.t("Выбери юзера в таблице"))
            return None, None, None
        uid = sel[0]  # iid = полный UID
        vals = self.users_tv.item(sel[0], "values")
        cn = vals[0]
        s = self._sel_srv_silent()
        rec = next((u for u in (s or {}).get("users", [])
                    if u.get("uid") == uid or u["cn"] == cn), None)
        return cn, rec, uid

    def _create_user_impl(self, ssh, s, name, expiry, sessions, mask="",
                          up_rate=INT64_MAX, down_rate=INT64_MAX,
                          up_credit=INT64_MAX, down_credit=INT64_MAX):
        """Полный цикл: сертификат на сервере + UID через admin-API + конфиг."""
        # 1. сертификат
        self.vsay("  user-cert.sh «%s»…" % name)
        out = ssh.run_script("user-cert.sh", name, timeout=120)
        self.vsay(self.t("  вывод %d байт") % len(out))
        mats = parse_cert_bundle(out)
        if not all(mats.values()):
            tail = "\n".join(out.strip().splitlines()[-6:])
            raise SSHErr(self.t("user-cert.sh: неполный вывод (нет CA/CERT/KEY/TA).\n"
                         "Хвост вывода: %s") % tail)
        # 2. UID через admin-API
        api = self._api(s)
        api.start()
        try:
            uid = api.create_user(sessions_cap=sessions, expiry=expiry,
                                  up_rate=up_rate, down_rate=down_rate,
                                  up_credit=up_credit, down_credit=down_credit)
        finally:
            api.stop()
        # 3. конфиг (флажно: user_bundles\<сервер>\<юзер>.dgcloak)
        bundle = os.path.join(BUNDLES_DIR, s["name"])
        write_user_bundle(s, name, uid, mats, bundle, mask)
        if provision_client(s["name"], name,
                            os.path.join(bundle, "%s.dgcloak" % name)):
            self.say(self.t("  конфиг передан клиенту: «%s»")
                     % ("%s@%s" % (name, s["name"])))
        # 4. запись в реестр
        users = s.setdefault("users", [])
        users[:] = [u for u in users if u["cn"] != name]
        users.append({"cn": name, "uid": uid, "expiry": expiry,
                      "sessions": sessions, "mask": mask,
                      "up_rate": up_rate, "down_rate": down_rate,
                      "up_credit": up_credit, "down_credit": down_credit,
                      "created": time.strftime("%Y-%m-%d")})
        save_data(self.data)
        self._push_users(ssh, s)
        self.say(self.t("Юзер «%s» создан, выдай конфиг юзеру: %s")
                 % (name, os.path.join(bundle, "%s.dgcloak" % name)))
        return bundle

    def _user_create(self):
        s = self._sel_srv()
        if not s:
            return
        d = UserDialog(self, srv_mask=s.get("mask_domain", "www.bing.com"),
                       title=self.t("Новый юзер"))
        if not d.result:
            return
        r = d.result
        # дубль CN = перезапись чужого сертификата — запрещаем
        if any(u.get("cn") == r["name"] for u in s.get("users", [])):
            messagebox.showerror(APP_NAME,
                                 self.t("Юзер «%s» уже существует на «%s»")
                                 % (r["name"], s["name"]))
            return

        def work():
            ssh = SSH(s, self.say)
            self._create_user_impl(ssh, s, r["name"], r["expiry"],
                                   r["sessions"], r.get("mask", ""),
                                   r["up_rate"], r["down_rate"],
                                   r["up_credit"], r["down_credit"])
            self.ui(self._users_refresh)
        self._worker(work)

    def _user_edit(self):
        s = self._sel_srv()
        if not s:
            return
        cn, rec, uid = self._selected_user()
        if cn is None:
            return
        if not rec:
            messagebox.showinfo(APP_NAME, self.t("«%s» не из нашего реестра — "
                                "править можем только своих") % cn)
            return
        d = UserDialog(self, title=self.t("Изменить «%s»") % cn,
                       srv_mask=s.get("mask_domain", "www.bing.com"), rec=rec)
        if not d.result:
            return
        r = d.result

        api_changed = any(rec.get(k) != r[k] for k in (
            "sessions", "expiry", "up_rate", "down_rate",
            "up_credit", "down_credit"))
        mask_changed = rec.get("mask", "") != r["mask"]

        def work():
            ssh = SSH(s, self.say)
            if api_changed:
                api = self._api(s)
                api.start()
                try:
                    api.update_user(uid, sessions_cap=r["sessions"],
                                    expiry=r["expiry"],
                                    up_rate=r["up_rate"],
                                    down_rate=r["down_rate"],
                                    up_credit=r["up_credit"],
                                    down_credit=r["down_credit"])
                finally:
                    api.stop()
            rec.update({"sessions": r["sessions"], "expiry": r["expiry"],
                        "mask": r["mask"],
                        "up_rate": r["up_rate"], "down_rate": r["down_rate"],
                        "up_credit": r["up_credit"],
                        "down_credit": r["down_credit"]})
            save_data(self.data)
            self._push_users(ssh, s)
            if mask_changed:
                # маскировка живёт в конфиге — перевыпускаем локальную копию
                out = ssh.run_script("user-cert.sh", cn, timeout=60)
                mats = parse_cert_bundle(out)
                bundle = os.path.join(BUNDLES_DIR, s["name"])
                write_user_bundle(s, cn, uid, mats, bundle, r["mask"])
                if provision_client(s["name"], cn,
                                    os.path.join(bundle, "%s.dgcloak" % cn)):
                    self.say(self.t("  конфиг передан клиенту: «%s»")
                             % ("%s@%s" % (cn, s["name"])))
                self.say(self.t("Юзер «%s» обновлён, конфиг перевыпущен, "
                         "выдай его юзеру: %s")
                         % (cn, os.path.join(bundle, "%s.dgcloak" % cn)))
            elif api_changed:
                self.say(self.t("Юзер «%s» обновлён (лимиты/срок — на сервере, "
                         "конфиг тот же)") % cn)
            else:
                self.say(self.t("Юзер «%s» без изменений") % cn)
            self.ui(self._users_refresh)
        self._worker(work)

    def _user_revoke(self):
        s = self._sel_srv()
        if not s:
            return
        cn, rec, uid = self._selected_user()
        if cn is None:
            return
        if cn == "?" or not rec:
            # CN неизвестен → сертификат не отозвать, сессию не сбросить.
            # Но UID можно удалить из Cloak — новые подключения закроются.
            if not messagebox.askyesno(
                    APP_NAME,
                    self.t("UID %s…\nне из реестра админки — CN неизвестен, сертификат "
                    "и живую сессию трогать не можем.\n\n"
                    "Удалить UID из Cloak? (новые подключения закроются)") % uid[:16]):
                return

            def work_uid():
                api = self._api(s)
                api.start()
                try:
                    api.delete_user(uid)
                finally:
                    api.stop()
                self.say(self.t("UID %s… удалён из Cloak") % uid[:16])
                self.ui(self._users_refresh)
            self._worker(work_uid)
            return
        if not messagebox.askyesno(
                APP_NAME,
                self.t("Отозвать «%s»?\n\nСертификат отзовётся (CRL), UID удалится,\n"
                "живая сессия будет сброшена.") % cn):
            return

        def work():
            ssh = SSH(s, self.say)
            # 1. revoke cert + crl (весь конвейер под sudo — CA может быть
            # в /root или в /home/*)
            # сертификата может не быть (сервер откачен/переставлен, а запись
            # в реестре осталась) — это не причина бросать юзера «призраком»:
            # дальше всё равно чистим UID и реестр
            try:
                ssh.run(
                    "%sbash -c 'CADIR=$(ls -d /root/openvpn-ca "
                    "/home/*/openvpn-ca 2>/dev/null | head -1); "
                    "[ -n \"$CADIR\" ] && cd \"$CADIR\" && "
                    "test -s pki/issued/%s.crt && "
                    "{ P=$(cat pki/lock.file 2>/dev/null); "
                    "kill -0 \"$P\" 2>/dev/null || rm -f pki/lock.file; } && "
                    "./easyrsa --batch revoke %s && "
                    "./easyrsa --batch gen-crl && "
                    "install -m644 pki/crl.pem /etc/openvpn/server/crl.pem'"
                    % (ssh.sudo, cn, cn), timeout=60)
                self.say(self.t("  сертификат отозван"))
            except SSHErr as e:
                msg = str(e)
                if "test -s" in msg or "not a valid certificate" in msg \
                        or msg.rstrip().endswith("rc=1:") \
                        or "(пустой вывод)" in msg \
                        or self.t("(пустой вывод)") in msg:
                    self.say(self.t("  сертификата на сервере нет — пропускаю отзыв"))
                elif "already revoked" in msg.lower():
                    self.say(self.t("  сертификат уже отозван"))
                else:
                    raise
            # 2. delete UID
            if rec and rec.get("uid"):
                try:
                    api = self._api(s)
                    api.start()
                    api.delete_user(rec["uid"])
                    api.stop()
                    self.say(self.t("  UID удалён"))
                except Exception as e:
                    if "bucket not found" in str(e) or "404" in str(e):
                        self.say(self.t("  UID в Cloak уже нет"))
                    else:
                        self.say("  UID: %s" % e)
            # 3. kill live session
            try:
                ssh.upload(os.path.join(SCRIPTS_DIR, "ovpn-mgmt.py"),
                           "/tmp/ovpn-mgmt.py")
                out = ssh.run("%senv DG_LANG=%s python3 /tmp/ovpn-mgmt.py kill %s"
                              % (ssh.sudo, _LANG, cn), timeout=20)
                # не просто SUCCESS — его mgmt шлёт и на пароль
                if "SUCCESS: common name" in out:
                    self.say(self.t("  сессия сброшена"))
                elif "not found" in out:
                    self.say(self.t("  сессии не было (юзер офлайн)"))
                else:
                    self.say("  mgmt: %s" % out.strip()[:200])
            except Exception as e:
                # чаще всего mgmt просто не поднят (сервер чист) —
                # traceback не нужен, хватит последней строки
                self.say(self.t("  mgmt недоступен: %s")
                         % str(e).strip().splitlines()[-1][:160])
            s["users"] = [u for u in s.get("users", []) if u["cn"] != cn]
            save_data(self.data)
            self._push_users(ssh, s)
            try:  # файл юзера в плоской user_bundles\<сервер>\
                os.remove(os.path.join(BUNDLES_DIR, s["name"],
                                       "%s.dgcloak" % cn))
            except OSError:
                pass
            if provision_client(s["name"], cn, delete=True):
                self.say(self.t("  профиль «%s» удалён и у клиента")
                         % ("%s@%s" % (cn, s["name"])))
            self.say(self.t("Юзер «%s» отозван и удалён.") % cn)
            self.ui(self._users_refresh)
        self._worker(work)

    def _user_kill(self):
        s = self._sel_srv()
        if not s:
            return
        cn, rec, _uid = self._selected_user()
        if cn is None:
            return
        if cn == "?" or not rec:
            messagebox.showinfo(APP_NAME, self.t("CN этого юзера неизвестен — "
                                "mgmt kill работает только по CN."))
            return

        def work():
            ssh = SSH(s, self.say)
            ssh.upload(os.path.join(SCRIPTS_DIR, "ovpn-mgmt.py"),
                       "/tmp/ovpn-mgmt.py")
            online = ssh.run("%senv DG_LANG=%s python3 /tmp/ovpn-mgmt.py clients"
                             % (ssh.sudo, _LANG), timeout=20)
            if cn not in {l.strip() for l in online.splitlines()}:
                self.say(self.t("«%s» не онлайн — сбрасывать нечего") % cn)
                return
            out = ssh.run("%senv DG_LANG=%s python3 /tmp/ovpn-mgmt.py kill %s"
                          % (ssh.sudo, _LANG, cn), timeout=20)
            if "SUCCESS: common name" in out:
                self.say(self.t("Сессия «%s» сброшена") % cn)
            else:
                self.say(self.t("сброс сессии %s: %s") % (cn, out.strip()[:200]))
        self._worker(work)

    def _user_export(self):
        s = self._sel_srv()
        if not s:
            return
        cn, rec, _uid = self._selected_user()
        if cn is None:
            return
        if not rec:
            messagebox.showinfo(APP_NAME, self.t("«%s» заведён вне этой админки — "
                                "экспорт конфига недоступен") % cn)
            return
        # «Сохранить как…» — файл один, подпапка с именем юзера не нужна;
        # юзер сам выбирает имя и место
        dst = filedialog.asksaveasfilename(
            title=self.t("Куда сохранить конфиг «%s»") % cn,
            initialfile="%s.dgcloak" % cn, defaultextension=".dgcloak",
            filetypes=[("DGCloak", "*.dgcloak"), (self.t("Все файлы"), "*.*")])
        if not dst:
            return

        def work():
            ssh = SSH(s, self.say)
            out = ssh.run_script("user-cert.sh", cn, timeout=60)
            mats = parse_cert_bundle(out)
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(make_dgcloak(s, cn, rec["uid"], mats,
                                       rec.get("mask") or ""),
                          f, ensure_ascii=False, indent=2)
            # каноническая копия в %APPDATA% — у восстановленных
            # импортом юзеров её может не быть вовсе
            write_user_bundle(s, cn, rec["uid"], mats,
                              os.path.join(BUNDLES_DIR, s["name"]),
                              rec.get("mask") or "")
            self.say(self.t("Конфиг «%s» → %s") % (cn, dst))
        self._worker(work)


def single_instance_ok():
    """Второй экземпляр админки не запускаем (mutex, Windows)."""
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local\\DGCloakAdminSingleton")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(
                0, T("DGCloak Admin уже запущен."), APP_NAME, 0x40)
            return False
    except Exception:
        pass  # не Windows или нет ctypes — не блокируем
    return True


if __name__ == "__main__":
    if single_instance_ok():
        App().mainloop()
