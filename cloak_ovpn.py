"""DGCloak VPN — Cloak (ck-client) + OpenVPN (консольный openvpn.exe), лаунчер для Windows.

Без окон стороннего GUI, статус берётся из management-интерфейса OpenVPN.
Требует прав администратора (создание сетевого адаптера и маршрутов).
Для иконки и трея нужны pystray и pillow (без них программа работает, но без трея).
"""
import base64
import ctypes
import io
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import tkinter as tk
import urllib.request
from tkinter import filedialog, messagebox, ttk

try:
    import pystray
    import cloak_icon
    from PIL import Image  # noqa: F401  (нужен pystray)
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

APP_NAME = "DGCloak VPN"
BTN_W = 16   # одинаковая ширина кнопок «Подключить/Отключить», «Дополнительно», «Копировать/Очистить лог»
BTN_S = 8    # одинаковая ширина маленьких кнопок (+, Изм., Удал., Выход)
DGCLOAK_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloak")
OLD_APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakVPN")
APP_DIR = os.path.join(DGCLOAK_DIR, "VPN")
BIN_DIR = os.path.join(DGCLOAK_DIR, "bin")  # общие зависимости: ck-client и др.
DATA_FILE = os.path.join(APP_DIR, "data.json")
PROFILES_DIR = os.path.join(APP_DIR, "profiles")  # сюда копируются файлы профилей при добавлении
PIDS_FILE = os.path.join(APP_DIR, "pids.json")    # PID наших ck-client/openvpn (для добивания зависших)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_DATA = {
    "ck_client": r"C:\Tools\Cloak\ck-client-windows-amd64.exe",
    "openvpn_exe": r"C:\Program Files\OpenVPN\bin\openvpn.exe",
    "profiles": [],
    "last_profile": "",
    "language": "ru",
}

LANG_NAMES = {"ru": "Русский", "en": "English"}

# Перевод интерфейса: ключ — русская строка, значение — английская.
# Строка, которой нет в словаре, показывается по-русски.
STRINGS_EN = {
    # главное окно
    "Подключить": "Connect",
    "Отключить": "Disconnect",
    "Выход": "Exit",
    "Изм.": "Edit",
    "Удал.": "Delete",
    "Дополнительно ▾": "Advanced ▾",
    "Дополнительно ▴": "Advanced ▴",
    "Копировать лог": "Copy log",
    "Очистить лог": "Clear log",
    "Пути к Cloak и OpenVPN…": "Paths to Cloak and OpenVPN…",
    "Отладочный лог OpenVPN (применится при следующем подключении)":
        "OpenVPN debug log (applies on next connect)",
    "Язык:": "Language:",
    # статусы
    "Отключено": "Disconnected",
    "Подключение…": "Connecting…",
    "Ожидание ответа сервера…": "Waiting for server…",
    "Аутентификация…": "Authenticating…",
    "Получение настроек…": "Getting config…",
    "Назначение IP…": "Assigning IP…",
    "Добавление маршрутов…": "Adding routes…",
    "Подключено: {name}": "Connected: {name}",
    "Переподключение…": "Reconnecting…",
    "Отключение…": "Disconnecting…",
    "Запуск Cloak…": "Starting Cloak…",
    "Запуск OpenVPN…": "Starting OpenVPN…",
    "Маршруты не добавлены — нужны права администратора":
        "Routes not added — administrator rights required",
    "Cloak остановлен — VPN не работает": "Cloak stopped — VPN down",
    "OpenVPN завершился": "OpenVPN exited",
    # диалоги
    "Профиль": "Profile",
    "Сохранить": "Save",
    "Отмена": "Cancel",
    "Ошибка": "Error",
    "Заполните название, конфиг Cloak и профиль OpenVPN.":
        "Fill in name, Cloak config and OpenVPN profile.",
    "Заполните .dgcloak ИЛИ название, конфиг Cloak и профиль OpenVPN.":
        "Fill in a .dgcloak OR name, Cloak config and OpenVPN profile.",
    "Файл не похож на DGCloak-профиль (нет секций cloak/ovpn).":
        "File does not look like a DGCloak profile (no cloak/ovpn sections).",
    "DGCloak конфиг (.dgcloak)": "DGCloak config (.dgcloak)",
    "— ИЛИ —": "— OR —",
    "Порт должен быть числом.": "Port must be a number.",
    "Удалить": "Delete",
    "Удалить профиль «{name}»?": "Delete profile «{name}»?",
    "Нет профиля": "No profile",
    "Сначала добавьте профиль кнопкой «+».": "Add a profile first («+» button).",
    "VPN подключён. Отключить и выйти из программы?":
        "VPN is connected. Disconnect and exit?",
    "Профили не добавлены. Добавить первый профиль сейчас?":
        "No profiles yet. Add the first profile now?",
    "Путь к {t}": "Path to {t}",
    "Все": "All",
    # поля профиля
    "Название": "Name",
    "Cloak конфиг (.json)": "Cloak config (.json)",
    "OpenVPN профиль (.ovpn)": "OpenVPN profile (.ovpn)",
    "Локальный порт Cloak (-l)": "Local Cloak port (-l)",
    "Сервер Cloak (-s), если не в конфиге": "Cloak server (-s), if not in config",
    "Порт сервера Cloak (-p)": "Cloak server port (-p)",
    "IP обхода туннеля (пусто = из ck-конфига)": "Bypass IP (empty = from ck config)",
    "UDP-режим (-u, для OpenVPN по UDP)": "UDP mode (-u, for OpenVPN over UDP)",
    "Весь трафик через VPN (redirect-gateway local def1)":
        "Route all traffic via VPN (redirect-gateway local def1)",
    "Порт — куда ck-client принимает OpenVPN (-l); в .ovpn remote\n"
    "перезаписывается на 127.0.0.1:порт. Сервер (-s) и его порт (-p)\n"
    "переопределяют RemoteHost/RemotePort из ck-конфига. IP обхода —\n"
    "маршрут до сервера через основной шлюз, чтобы трафик Cloak не\n"
    "заворачивался в сам VPN; пусто = RemoteHost из ck-конфига.\n"
    "UDP — транспорт Cloak по UDP (-u); включается и по \"UDP\":true\n"
    "в ck-конфиге. Файлы профиля копируются в папку программы —\n"
    "исходные после этого можно удалить.":
        "Port — where ck-client accepts OpenVPN (-l); remote in .ovpn is\n"
        "rewritten to 127.0.0.1:port. Server (-s) and its port (-p) override\n"
        "RemoteHost/RemotePort from the ck config. Bypass IP — a route to\n"
        "the server via the main gateway so Cloak traffic doesn't loop into\n"
        "the VPN itself; empty = RemoteHost from the ck config.\n"
        "UDP — Cloak transport over UDP (-u); also enabled by \"UDP\":true\n"
        "in the ck config. Profile files are copied into the program\n"
        "folder — the originals can be deleted afterwards.",
    "Файлы профиля скопированы в папку данных программы.":
        "Profile files copied to the program data folder.",
    # трей
    "Открыть": "Open",
    "Подключить «{name}»": "Connect «{name}»",
    "Отключить «{name}»": "Disconnect «{name}»",
    "Отключить «{name}» и подключить": "Disconnect «{name}» and connect",
    "Отключить VPN и выйти из программы": "Disconnect VPN and exit",
    "Программа продолжает работать в трее. Выход: "
    "правый клик по значку → «Отключить VPN и выйти из программы».":
        "App keeps running in tray. Exit: right-click icon → «Disconnect VPN and exit».",
    # мастер установки
    "Настройка программ": "Program setup",
    "не найден": "not found",
    "Установить автоматически": "Install automatically",
    "Указать пути вручную": "Set paths manually",
    "Закрыть": "Close",
    "Автоустановка: OpenVPN через winget, Cloak — свежий релиз с GitHub "
    "в %APPDATA%\\DGCloak\\bin. Пути можно изменить позже: "
    "Дополнительно → «Пути к Cloak и OpenVPN…».":
        "Auto setup: OpenVPN via winget, Cloak — latest GitHub release "
        "into %APPDATA%\\DGCloak\\bin. Paths can be changed later: "
        "Advanced → «Paths to Cloak and OpenVPN…».",
    "скачиваю свежий ck-client с GitHub…": "downloading latest ck-client from GitHub…",
    "устанавливаю OpenVPN через winget…": "installing OpenVPN via winget…",
    "winget не найден. Установите OpenVPN Community вручную: "
    "openvpn.net/community-downloads/":
        "winget not found. Install OpenVPN Community manually: "
        "openvpn.net/community-downloads/",
    "готово": "done",
    "Установка": "Setup",
    # логовые сообщения, которые важны пользователю
    "отключено": "offline",
    "Лог скопирован в буфер обмена.": "Log copied to clipboard.",
    "Лог очищен.": "Log cleared.",
    "Подключено.": "Connected.",
    "Отключено.": "Disconnected.",
    "Отключаюсь…": "Disconnecting…",
    "Cloak готов.": "Cloak ready.",
    "management подключён.": "management connected.",
    "Состояние OpenVPN: {st}": "OpenVPN state: {st}",
    "Ошибка: {e}": "Error: {e}",
    "Профиль: {w}": "Profile: {w}",
    "Запуск: {a}": "Launching: {a}",
    "Завершил оставшиеся от прошлого запуска: {lst}":
        "Killed leftovers from the previous run: {lst}",
    "Уже запущен {exe} (PID {pids}). Завершите процесс и повторите.":
        "{exe} is already running (PID {pids}). Terminate it and retry.",
    "Порт {port} уже занят другим процессом.":
        "Port {port} is already in use by another process.",
    "Системный маршрут 127.0.0.1 отсутствовал — восстановлен.":
        "System route 127.0.0.1 was missing — restored.",
    "Локальный адрес 127.0.0.1 не отвечает — сломана таблица маршрутов "
    "(обычно после аварийного завершения OpenVPN). Перезагрузите ПК.":
        "Local address 127.0.0.1 does not respond — the routing table is broken "
        "(usually after an abnormal OpenVPN exit). Reboot the PC.",
    "После отключения маршрут 127.0.0.1 пропал — восстановлен.":
        "Route 127.0.0.1 disappeared after disconnect — restored.",
    "ВНИМАНИЕ: 127.0.0.1 перестал отвечать — таблица маршрутов повреждена. "
    "Остальные программы (например v2rayN) могут не работать до перезагрузки.":
        "WARNING: 127.0.0.1 stopped responding — the routing table is corrupted. "
        "Other programs (e.g. v2rayN) may not work until reboot.",
    "Не удалось резолвить RemoteHost «{h}» — обход не добавлен.":
        "Failed to resolve RemoteHost «{h}» — bypass route not added.",
    "Обходной маршрут до сервера: {h} → {ip}": "Server bypass route: {h} → {ip}",
    "В ck-конфиге UDP:true — включаю UDP-режим автоматически.":
        "ck config has UDP:true — enabling UDP mode automatically.",
    "Cloak завершился с кодом {rc}": "Cloak exited with code {rc}",
    "OpenVPN завершился с кодом {rc}": "OpenVPN exited with code {rc}",
    "Cloak не поднял порт за 15 с (ошибка: {err})":
        "Cloak did not open the port within 15 s (error: {err})",
    "OpenVPN не вышел в CONNECTED за 60 с (последнее состояние: {st})":
        "OpenVPN did not reach CONNECTED within 60 s (last state: {st})",
    "…жду OpenVPN {s} с; management: {m}; состояние: {st}":
        "…waiting for OpenVPN {s} s; management: {m}; state: {st}",
    "подключён": "connected",
    "нет": "none",
    "нет данных": "no data",
    "не определён": "unknown",
    "ВНИМАНИЕ: обходной маршрут до сервера Cloak не задан "
    "(ни bypass_ip, ни RemoteHost) — при полном туннеле "
    "транспорт зациклится через ~25 с!":
        "WARNING: no bypass route to the Cloak server (neither bypass_ip nor "
        "RemoteHost) — in full-tunnel mode the transport will loop within ~25 s!",
    "ВНИМАНИЕ: OpenVPN не смог добавить маршруты (Access is denied) — трафик не идёт "
    "через VPN. Запустите программу от имени администратора.":
        "WARNING: OpenVPN failed to add routes (Access is denied) — traffic does not "
        "go through the VPN. Run the program as administrator.",
    "Не удалось подключиться к management-интерфейсу OpenVPN.":
        "Failed to connect to the OpenVPN management interface.",
    "OpenVPN запрашивает логин/пароль — это пока не поддерживается: {line}":
        "OpenVPN requests login/password — not supported yet: {line}",
    "OpenVPN: {line}": "OpenVPN: {line}",
    "mgmt: {line}": "mgmt: {line}",
    "management: ошибка чтения {e}": "management: read error {e}",
    "management-соединение закрыто.": "management connection closed.",
    "Cloak остановился (код {rc}) — VPN не работает.":
        "Cloak stopped (code {rc}) — VPN down.",
    "OpenVPN завершился (код {rc}).": "OpenVPN exited (code {rc}).",
    "Внешний IP до подключения: {ip}": "External IP before connect: {ip}",
    "Внешний IP через VPN: {ip} (до подключения: {prev})":
        "External IP via VPN: {ip} (before connect: {prev})",
    "Внимание: нет прав администратора. OpenVPN не сможет создать адаптер — "
    "запустите программу от имени администратора.":
        "Warning: no administrator rights. OpenVPN cannot create an adapter — "
        "run the program as administrator.",
    "Иконка и трей отключены: установите пакеты  pip install pystray pillow":
        "Tray icon disabled: run  pip install pystray pillow",
    "Не удалось создать иконку в трее: {e}": "Failed to create tray icon: {e}",
    "Уже выведенные в лог записи останутся на прежнем языке — "
    "переводятся только новые.":
        "Log entries already written remain in the previous language — "
        "only new ones are translated.",
    "В последнем релизе Cloak не найден ck-client-windows-amd64*.exe":
        "ck-client-windows-amd64*.exe not found in the latest Cloak release",
    "Cloak не скачался: {e}. Скачайте вручную: github.com/cbeuw/Cloak/releases":
        "Cloak download failed: {e}. Download manually: github.com/cbeuw/Cloak/releases",
    "winget вернул код {rc} — установите OpenVPN вручную.":
        "winget returned code {rc} — install OpenVPN manually.",
    "{title} есть, но не запускается: {path}": "{title} exists but failed to run: {path}",
    "ВНУТРЕННЯЯ ОШИБКА:\n{tb}": "INTERNAL ERROR:\n{tb}",
    "не удалось создать папку {d}: {e}": "cannot create folder {d}: {e}",
    "{k}: {e}": "{k}: {e}",
    "Подключено:": "Connected:",
    "Подключено в течение:": "Uptime:",
    "Скорость:": "Speed:",
    "НЕТ ОБХОДА — риск петли": "NO BYPASS — loop risk",
    "Соединение оборвалось — переподключение ({n}/3)…":
        "Connection dropped — reconnecting ({n}/3)…",
    "Автопереподключение при обрыве (до 3 попыток)": "Auto-reconnect on drop (up to 3 tries)",
    "Проверить": "Check",
    "проверяю…": "checking…",
    "порт {p} ЗАНЯТ": "port {p} BUSY",
    "порт {p} свободен": "port {p} free",
    "нет сервера (поле -s или RemoteHost в ck-конфиге)":
        "no server (-s field or RemoteHost in ck config)",
    "сервер {h} → {ip}": "server {h} → {ip}",
    "не резолвится: {h}": "cannot resolve: {h}",
    "Внимание: нет прав администратора. OpenVPN не сможет создать адаптер — "
    "запустите программу от имени администратора.":
        "Warning: no administrator rights. OpenVPN cannot create the adapter — "
        "run the app as administrator.",
    "ВНИМАНИЕ: OpenVPN не смог добавить маршруты (Access is denied) — трафик не идёт "
    "через VPN. Запустите программу от имени администратора.":
        "WARNING: OpenVPN could not add routes (Access is denied) — traffic does not "
        "go through VPN. Run the app as administrator.",
    "ВНИМАНИЕ: 127.0.0.1 перестал отвечать — таблица маршрутов повреждена. "
    "Остальные программы (например v2rayN) могут не работать до перезагрузки.":
        "WARNING: 127.0.0.1 stopped responding — routing table damaged. "
        "Other apps (e.g. v2rayN) may not work until reboot.",
    "Системный маршрут 127.0.0.1 отсутствовал — восстановлен.":
        "System route 127.0.0.1 was missing — restored.",
    "После отключения маршрут 127.0.0.1 пропал — восстановлен.":
        "Route 127.0.0.1 disappeared after disconnect — restored.",
}

# состояние OpenVPN -> (текст, цвет)
STATES = {
    "RESOLVE": ("Подключение…", "orange"),
    "TCP_CONNECT": ("Подключение…", "orange"),
    "CONNECTING": ("Подключение…", "orange"),
    "WAIT": ("Ожидание ответа сервера…", "orange"),
    "AUTH": ("Аутентификация…", "orange"),
    "GET_CONFIG": ("Получение настроек…", "orange"),
    "ASSIGN_IP": ("Назначение IP…", "orange"),
    "ADD_ROUTES": ("Добавление маршрутов…", "orange"),
    "CONNECTED": ("Подключено: {name}", "green"),
    "RECONNECTING": ("Переподключение…", "orange"),
    "EXITING": ("Отключение…", "orange"),
}


# строка состояния: ">STATE:ts,CONNECTED,..." (реальное время) или "ts,CONNECTED,..." (история)
STATE_RE = re.compile(r"^(?:>STATE:)?\d+,([A-Z_]+),")


def _rewrite_paths(obj, old, new):
    """Префикс старой папки → новой внутри data.json (профили и т.п.)."""
    if isinstance(obj, str):
        return new + obj[len(old):] if obj.startswith(old) else obj
    if isinstance(obj, list):
        return [_rewrite_paths(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {k: _rewrite_paths(v, old, new) for k, v in obj.items()}
    return obj


def migrate_dirs():
    """Переезд %APPDATA%\\DGCloakVPN → DGCloak\\VPN\\; ck-client — в общий
    DGCloak\\bin\\ (админка мигрирует свою папку сама при старте)."""
    if APP_DIR != os.path.join(DGCLOAK_DIR, "VPN"):
        return
    moved = False
    if os.path.isdir(OLD_APP_DIR) and not os.path.isdir(APP_DIR):
        try:
            os.makedirs(DGCLOAK_DIR, exist_ok=True)
            shutil.move(OLD_APP_DIR, APP_DIR)
            moved = True
        except OSError:
            return
    # свой ck-client — в общий bin; дубликат там — удалить (одна копия
    # на обе программы). Каждый запуск — чистит и хвосты миграции.
    try:
        src = os.path.join(APP_DIR, "ck-client.exe")
        dst = os.path.join(BIN_DIR, "ck-client.exe")
        if os.path.isfile(src):
            if os.path.isfile(dst):
                os.remove(src)
            else:
                os.makedirs(BIN_DIR, exist_ok=True)
                shutil.move(src, dst)
    except OSError:
        pass
    if not moved:
        return
    # пути в data.json: старый префикс → новый; ck_client указывал на
    # ck-client.exe — после переноса в bin переписываем точно
    try:
        old_p = OLD_APP_DIR + os.sep
        d = load_data()
        old_ck = d.get("ck_client", "")
        d = _rewrite_paths(d, old_p, APP_DIR + os.sep)
        if old_ck.startswith(old_p):
            d["ck_client"] = os.path.join(BIN_DIR, "ck-client.exe")
        save_data(d)
    except Exception:
        pass


def load_data():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return {**DEFAULT_DATA, **json.load(f)}
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_DATA))


def save_data(data):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def probe(host, port, timeout=2.0):
    """None, если порт принимает соединения, иначе текст ошибки."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as e:
        return repr(e)


def port_open(host, port):
    return probe(host, port) is None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def loopback_ok():
    """Работает ли вообще локальный TCP на 127.0.0.1."""
    try:
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            with socket.create_connection(srv.getsockname(), timeout=2):
                return True
    except OSError:
        return False


def repair_loopback():
    """Вернуть системный маршрут loopback (OpenVPN может его удалить при выходе).
    True — маршрута не было и он добавлен; False — уже существовал (штатно) или не вышло."""
    try:
        r = subprocess.run(["route", "ADD", "127.0.0.1", "MASK", "255.255.255.255", "0.0.0.0", "IF", "1"],
                           capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                           creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_procs(exe_name):
    """PID уже запущенных процессов с таким именем."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace", timeout=10,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == exe_name.lower():
            pids.append(parts[1])
    return pids


def proc_cmdlines(exe_name):
    """{pid: командная строка} процессов с таким именем exe (PowerShell CIM)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='%s'\" | "
          "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"
          % exe_name)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                              "-Command", ps],
                             capture_output=True, text=True, errors="replace", timeout=15,
                             stdin=subprocess.DEVNULL,
                             creationflags=NO_WINDOW).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return {}
    res = {}
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        if pid.strip().isdigit():
            res[pid.strip()] = cmd.strip()
    return res


def pid_running(pid, exe_name):
    """Жив ли процесс с этим PID и ожидаемым именем exe (защита от повторного использования PID)."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace", timeout=10,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout or ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == exe_name.lower():
            return True
    return False


def kill_pid(pid):
    """Завершить процесс по PID вместе с дочерними."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                   capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                   creationflags=NO_WINDOW)


def load_pids():
    try:
        with open(PIDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_pids(d):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(PIDS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


CLOAK_API = "https://api.github.com/repos/cbeuw/Cloak/releases/latest"


def find_openvpn():
    """Готовый openvpn.exe: PATH, стандартные папки установки."""
    cands = []
    w = shutil.which("openvpn")
    if w:
        cands.append(w)
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    cands += [os.path.join(pf, "OpenVPN", "bin", "openvpn.exe"),
              os.path.join(pfx, "OpenVPN", "bin", "openvpn.exe")]
    for c in cands:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return ""


def find_ck_client():
    """ck-client в общем bin, папке данных (legacy) или PATH."""
    cands = [os.path.join(BIN_DIR, "ck-client.exe"),
             os.path.join(APP_DIR, "ck-client.exe"),
             os.path.join(OLD_APP_DIR, "ck-client.exe")]
    w = shutil.which("ck-client")
    if w:
        cands.insert(0, w)
    for c in cands:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return ""


def find_winget():
    """winget: PATH или WindowsApps (есть в Win11 и обычных Win10, нет на LTSC/Server)."""
    w = shutil.which("winget")
    if w:
        return w
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "winget.exe")
    return p if os.path.isfile(p) else ""


def exe_runs(path):
    """Быстрый запуск бинарника: важно, что он выполняется и завершается (rc любой)."""
    try:
        subprocess.run([path, "--version"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, creationflags=NO_WINDOW)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def cloak_latest_url():
    """Ссылка на свежий ck-client-windows-amd64*.exe из GitHub API (прямая ссылка не нужна)."""
    req = urllib.request.Request(CLOAK_API, headers={"User-Agent": "DGCloakVPN"})
    with urllib.request.urlopen(req, timeout=20) as r:
        meta = json.loads(r.read().decode("utf-8"))
    for a in meta.get("assets", []):
        if re.match(r"ck-client-windows-amd64.*\.exe$", a.get("name", "")):
            return a["browser_download_url"], int(a.get("size") or 0)
    raise RuntimeError("В последнем релизе Cloak не найден ck-client-windows-amd64*.exe")


def _fmt_bytes(n):
    """«3.4 MB» — для счётчика трафика в статусе."""
    v = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or u == "TB":
            return ("%.1f %s" % (v, u)).replace(".0 ", " ")
        v /= 1024


def external_ip(timeout=4):
    """Внешний IP по api.ipify.org (для лога до/после подключения). None при ошибке."""
    try:
        req = urllib.request.Request("https://api.ipify.org",
                                     headers={"User-Agent": "DGCloakVPN"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ip = r.read().decode("ascii", "replace").strip()
        return ip if re.fullmatch(r"[0-9a-fA-F:.]{3,45}", ip) else None
    except Exception:  # noqa: BLE001 — нет сети/таймаут: это только информация для лога
        return None


def download(url, dst, on_progress):
    """Скачать файл, вызывая on_progress(получено, всего)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "DGCloakVPN"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            on_progress(done, total)


# директивы .ovpn, чьи значения — пути к файлам (ключи/cert-файлы рядом с профилем)
_OVPN_FILE_RE = re.compile(
    r"^\s*(ca|cert|key|tls-auth|tls-crypt|tls-crypt-v2|pkcs12|secret|dh|crl-verify|extra-certs)\s+(.+?)\s*$")


def _under_profiles_dir(path):
    return os.path.abspath(path).startswith(os.path.abspath(PROFILES_DIR) + os.sep)


def import_profile_files(r, tr=None):
    """Скопировать файлы профиля (конфиг Cloak, .ovpn и его внешние ключи) в
    PROFILES_DIR/<имя>/ и подставить новые пути в r — исходники можно удалить.
    Возвращает список предупреждений (переведённых через tr, если передан)."""
    tr = tr or (lambda s, **kw: s.format(**kw) if kw else s)
    warnings = []
    safe = re.sub(r"[^\w\-]+", "_", r.get("name", "")).strip("_") or "profile"
    dest = os.path.join(PROFILES_DIR, safe)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        return [tr("не удалось создать папку {d}: {e}", d=dest, e=e)]
    ovpn_src_dir = None
    for key in ("ck_config", "ovpn"):
        src = (r.get(key) or "").strip()
        if not src or not os.path.isfile(src):
            continue
        src_abs = os.path.abspath(src)
        if _under_profiles_dir(src_abs):
            if key == "ovpn":
                ovpn_src_dir = os.path.dirname(src_abs)
            continue  # уже наша копия
        dst = os.path.join(dest, os.path.basename(src_abs))
        try:
            shutil.copy2(src_abs, dst)
        except OSError as e:
            warnings.append(tr("{k}: {e}", k=key, e=e))
            continue
        r[key] = dst
        if key == "ovpn":
            ovpn_src_dir = os.path.dirname(src_abs)
    if ovpn_src_dir:  # внешние файлы, на которые ссылается .ovpn (ca/cert/key/ta и т.п.)
        try:
            with open(r["ovpn"], encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = _OVPN_FILE_RE.match(line)
                    if not m:
                        continue
                    ref = m.group(2).strip()
                    qm = re.match(r'"([^"]+)"', ref)
                    if qm:
                        ref = qm.group(1)
                    cand = ref if os.path.isabs(ref) else os.path.join(ovpn_src_dir, ref)
                    if not os.path.isfile(cand) and " " in ref:
                        # tls-auth <файл> 1 — второй аргумент (направление ключа) не файл
                        ref = ref.rsplit(None, 1)[0]
                        cand = ref if os.path.isabs(ref) else os.path.join(ovpn_src_dir, ref)
                    if os.path.isfile(cand) and not _under_profiles_dir(cand):
                        shutil.copy2(cand, os.path.join(dest, os.path.basename(cand)))
        except OSError as e:
            warnings.append(tr("{k}: {e}", k="ovpn", e=e))
    return warnings


class SetupDialog(tk.Toplevel):
    """Первый запуск / не найдены программы: автоустановка или ручные пути."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.working = False
        t = app.t
        self.title(t("Настройка программ"))
        self.resizable(False, False)
        self.ck_var = tk.StringVar()
        self.ov_var = tk.StringVar()
        self.status_var = tk.StringVar()
        for i, var in enumerate((self.ov_var, self.ck_var)):
            ttk.Label(self, textvariable=var).grid(row=i, column=0, columnspan=2,
                                                   sticky="w", padx=12, pady=2)
        self.progress = ttk.Progressbar(self, length=420, mode="determinate")
        self.progress.grid(row=2, column=0, columnspan=2, padx=12, pady=(8, 2))
        ttk.Label(self, textvariable=self.status_var, foreground="gray"
                  ).grid(row=3, column=0, columnspan=2, sticky="w", padx=12)
        btns = ttk.Frame(self)
        btns.grid(row=4, column=0, columnspan=2, pady=10)
        self.b_auto = ttk.Button(btns, text=t("Установить автоматически"), command=self._auto)
        self.b_auto.pack(side="left", padx=4)
        self.b_manual = ttk.Button(btns, text=t("Указать пути вручную"), command=self._manual)
        self.b_manual.pack(side="left", padx=4)
        self.b_close = ttk.Button(btns, text=t("Закрыть"), command=self.destroy)
        self.b_close.pack(side="left", padx=4)
        ttk.Label(self, foreground="gray", wraplength=440, justify="left",
                  text=t("Автоустановка: OpenVPN через winget, Cloak — свежий релиз с GitHub "
                         "в %APPDATA%\\DGCloak\\bin. Пути можно изменить позже: "
                         "Дополнительно → «Пути к Cloak и OpenVPN…».")
                  ).grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.refresh()

    def refresh(self):
        d = self.app.data
        for var, key, title in ((self.ov_var, "openvpn_exe", "OpenVPN"),
                                (self.ck_var, "ck_client", "Cloak")):
            path = d[key]
            ok = os.path.isfile(path)
            var.set(f"{'✔' if ok else '✖'} {title}: {path if ok else self.app.t('не найден')}")
        self.b_auto.config(state="normal" if not self.working else "disabled")

    def _manual(self):
        self.app._paths()
        self.refresh()

    def _auto(self):
        self.working = True
        self.refresh()
        threading.Thread(target=self._install, daemon=True).start()

    def _say(self, text):
        self.app.ui(lambda: self.status_var.set(self.app.t(text)))
        self.app.say(self.app.t("Установка") + ": " + text)

    def _set_progress(self, done, total):
        if total:
            self.app.ui(lambda: self.progress.config(value=done * 100 // total))

    def _install(self):
        app = self.app
        try:
            # Cloak: свежий релиз GitHub -> %APPDATA%\DGCloak\bin\ck-client.exe
            if not os.path.isfile(app.data["ck_client"]):
                self._say("скачиваю свежий ck-client с GitHub…")
                try:
                    url, _ = cloak_latest_url()
                    dst = os.path.join(BIN_DIR, "ck-client.exe")
                    download(url, dst, self._set_progress)
                    app.data["ck_client"] = os.path.normpath(dst)
                except Exception as e:  # noqa: BLE001
                    self._say(app.t("Cloak не скачался: {e}. Скачайте вручную: "
                                    "github.com/cbeuw/Cloak/releases", e=e))
            # OpenVPN: winget install (у приложения права администратора)
            if not os.path.isfile(app.data["openvpn_exe"]):
                winget = find_winget()
                if winget:
                    self._say("устанавливаю OpenVPN через winget…")
                    r = subprocess.run([winget, "install", "--id", "OpenVPNTechnologies.OpenVPN",
                                        "-e", "--silent", "--accept-package-agreements",
                                        "--accept-source-agreements"],
                                       stdin=subprocess.DEVNULL, timeout=600,
                                       creationflags=NO_WINDOW)
                    if r.returncode != 0:
                        self._say(app.t("winget вернул код {rc} — установите OpenVPN вручную.",
                                        rc=r.returncode))
                    found = find_openvpn()
                    if found:
                        app.data["openvpn_exe"] = found
                else:
                    self._say("winget не найден. Установите OpenVPN Community вручную: "
                              "openvpn.net/community-downloads/")
            # проверка: бинарники должны запускаться
            save_data(app.data)
            for key, title in (("openvpn_exe", "OpenVPN"), ("ck_client", "Cloak")):
                path = app.data[key]
                if os.path.isfile(path) and not exe_runs(path):
                    self._say(app.t("{title} есть, но не запускается: {path}", title=title, path=path))
            self._say("готово")
        finally:
            self.working = False
            app.ui(self.refresh)

    def destroy(self):
        super().destroy()
        self.app._after_setup()


class ProfileDialog(tk.Toplevel):
    FIELDS = [
        ("name", "Название", None),
        ("dgcloak", "DGCloak конфиг (.dgcloak)", [("DGCloak", "*.dgcloak"), ("Все", "*.*")]),
    ]
    PAIR_FIELDS = [
        ("ck_config", "Cloak конфиг (.json)", [("JSON", "*.json"), ("Все", "*.*")]),
        ("ovpn", "OpenVPN профиль (.ovpn)", [("OVPN", "*.ovpn"), ("Все", "*.*")]),
    ]
    ADV_FIELDS = [
        ("port", "Локальный порт Cloak (-l)", None),
        ("server", "Сервер Cloak (-s), если не в конфиге", None),
        ("server_port", "Порт сервера Cloak (-p)", None),
        ("bypass_ip", "IP обхода туннеля (пусто = из ck-конфига)", None),
    ]

    def __init__(self, parent, profile=None):
        super().__init__(parent)
        self.app = parent
        self.t = parent.t
        t = self.t
        self.title(t("Профиль"))
        self.resizable(False, False)
        self.result = None
        p = profile or {}
        self.vars = {}
        r = 0
        for key, label, ftypes in self.FIELDS:
            ttk.Label(self, text=t(label)).grid(row=r, column=0, sticky="w", padx=8, pady=4)
            v = tk.StringVar(value=str(p.get(key, "")))
            self.vars[key] = v
            ttk.Entry(self, textvariable=v, width=48).grid(row=r, column=1, padx=4)
            if ftypes:
                ttk.Button(self, text="…", width=3,
                           command=lambda v=v, t=ftypes: self._browse(v, t)).grid(row=r, column=2, padx=4)
            r += 1
        ttk.Label(self, text=t("— ИЛИ —"), foreground="gray").grid(
            row=r, column=0, columnspan=3, pady=(2, 0))
        r += 1
        for key, label, ftypes in self.PAIR_FIELDS:
            ttk.Label(self, text=t(label)).grid(row=r, column=0, sticky="w", padx=8, pady=4)
            v = tk.StringVar(value=str(p.get(key, "")))
            self.vars[key] = v
            ttk.Entry(self, textvariable=v, width=48).grid(row=r, column=1, padx=4)
            if ftypes:
                ttk.Button(self, text="…", width=3,
                           command=lambda v=v, t=ftypes: self._browse(v, t)).grid(row=r, column=2, padx=4)
            r += 1
        # редко нужные поля — под раскрывашкой, чтобы не пугать обилием настроек
        self._adv_open = False
        self.b_adv = ttk.Button(self, text=t("Дополнительно ▾"), command=self._toggle_adv)
        self.b_adv.grid(row=r, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 0))
        r += 1
        self.advf = ttk.Frame(self)
        self.advf.grid(row=r, column=0, columnspan=3, sticky="ew")
        for i, (key, label, ftypes) in enumerate(self.ADV_FIELDS):
            ttk.Label(self.advf, text=t(label)).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            v = tk.StringVar(value=str(p.get(key, "1984" if key == "port" else "")))
            self.vars[key] = v
            ttk.Entry(self.advf, textvariable=v, width=48).grid(row=i, column=1, padx=4)
        self.udp = tk.BooleanVar(value=p.get("udp", False))
        self.full = tk.BooleanVar(value=p.get("full_tunnel", True))
        self.recon = tk.BooleanVar(value=p.get("reconnect", True))
        n = len(self.ADV_FIELDS)
        ttk.Checkbutton(self.advf, text=t("UDP-режим (-u, для OpenVPN по UDP)"),
                        variable=self.udp).grid(row=n, column=1, sticky="w", pady=4)
        ttk.Checkbutton(self.advf, text=t("Весь трафик через VPN (redirect-gateway local def1)"),
                        variable=self.full).grid(row=n + 1, column=1, sticky="w")
        ttk.Checkbutton(self.advf, text=t("Автопереподключение при обрыве (до 3 попыток)"),
                        variable=self.recon).grid(row=n + 2, column=1, sticky="w")
        row = ttk.Frame(self.advf)
        row.grid(row=n + 3, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Button(row, text=t("Проверить"), width=10,
                   command=self._check).pack(side="left")
        self._check_lbl = ttk.Label(row, text="", foreground="gray")
        self._check_lbl.pack(side="left", padx=8)
        ttk.Label(self.advf, foreground="gray", justify="left",
                  text=t("Порт — куда ck-client принимает OpenVPN (-l); в .ovpn remote\n"
                         "перезаписывается на 127.0.0.1:порт. Сервер (-s) и его порт (-p)\n"
                         "переопределяют RemoteHost/RemotePort из ck-конфига. IP обхода —\n"
                         "маршрут до сервера через основной шлюз, чтобы трафик Cloak не\n"
                         "заворачивался в сам VPN; пусто = RemoteHost из ck-конфига.\n"
                         "UDP — транспорт Cloak по UDP (-u); включается и по \"UDP\":true\n"
                         "в ck-конфиге. Файлы профиля копируются в папку программы —\n"
                         "исходные после этого можно удалить.")
                  ).grid(row=n + 4, column=0, columnspan=3, padx=8, sticky="w")
        r += 1
        btns = ttk.Frame(self)
        btns.grid(row=r, column=0, columnspan=3, pady=8)
        ttk.Button(btns, text=t("Сохранить"), command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Отмена"), command=self.destroy).pack(side="left", padx=4)
        # у сохранённого профиля с нестандартными расширенными настройками раскрываем их сразу
        adv_filled = (str(p.get("port", "1984")) not in ("", "1984") or p.get("server")
                      or p.get("server_port") or p.get("bypass_ip") or p.get("udp")
                      or not p.get("full_tunnel", True))
        if adv_filled:
            self._toggle_adv()
        else:
            self.advf.grid_remove()
        self.transient(parent)
        self.grab_set()

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.advf.grid()
        else:
            self.advf.grid_remove()
        self.b_adv.config(text=self.t("Дополнительно ▴" if self._adv_open else "Дополнительно ▾"))
        self.update_idletasks()
        self.geometry(f"{self.winfo_reqwidth()}x{self.winfo_reqheight()}")  # поджать высоту

    def _browse(self, var, ftypes):
        ftypes = [(self.t(lbl), pat) for lbl, pat in ftypes]
        path = filedialog.askopenfilename(filetypes=ftypes)
        if path:
            var.set(os.path.normpath(path))
            if var in (self.vars["ck_config"], self.vars.get("dgcloak")) \
                    and not self.vars["name"].get():
                self.vars["name"].set(os.path.splitext(os.path.basename(path))[0])

    def _check(self):
        """Проверка профиля до сохранения: порт числом и свободен,
        сервер (поле -s или RemoteHost из ck-конфига) резолвится."""
        t = self.t
        self._check_lbl.config(text=t("проверяю…"), foreground="gray")
        r = {k: v.get().strip() for k, v in self.vars.items()}

        def work():
            ok, msgs = True, []
            try:
                port = int(r["port"] or 1984)
                if port_open("127.0.0.1", port):
                    ok = False
                    msgs.append(t("порт {p} ЗАНЯТ", p=port))
                else:
                    msgs.append(t("порт {p} свободен", p=port))
            except ValueError:
                ok = False
                msgs.append(t("Порт должен быть числом."))
            host = r.get("server") or ""
            if not host and os.path.isfile(r.get("ck_config") or ""):
                try:
                    with open(r["ck_config"], encoding="utf-8") as f:
                        host = json.load(f).get("RemoteHost", "")
                except Exception:
                    pass
            if not host:
                ok = False
                msgs.append(t("нет сервера (поле -s или RemoteHost в ck-конфиге)"))
            else:
                try:
                    msgs.append(t("сервер {h} → {ip}", h=host,
                                  ip=socket.gethostbyname(host)))
                except OSError:
                    ok = False
                    msgs.append(t("не резолвится: {h}", h=host))
            self.app.ui(lambda: self._check_lbl.config(
                text=" · ".join(msgs), foreground="#060" if ok else "#b00"))

        threading.Thread(target=work, daemon=True).start()

    def _from_dgcloak(self, r):
        """Разобрать .dgcloak → ck-конфиг + .ovpn материализуются в
        PROFILES_DIR/<имя>/, в r подставляются их пути."""
        path = r["dgcloak"]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        cloak, ovpn = data.get("cloak"), data.get("ovpn")
        if not isinstance(cloak, dict) or not isinstance(ovpn, str) \
                or not ovpn.strip():
            raise ValueError(self.t("Файл не похож на DGCloak-профиль "
                                    "(нет секций cloak/ovpn)."))
        name = r["name"] or data.get("name") \
            or os.path.splitext(os.path.basename(path))[0]
        safe = re.sub(r"[^\w\-]+", "_", name).strip("_") or "profile"
        dest = os.path.join(PROFILES_DIR, safe)
        os.makedirs(dest, exist_ok=True)
        ck = os.path.join(dest, "ckclient-%s.json" % safe)
        ov = os.path.join(dest, "%s.ovpn" % safe)
        with open(ck, "w", encoding="utf-8") as f:
            json.dump(cloak, f, indent=2)
        with open(ov, "w", encoding="utf-8") as f:
            f.write(ovpn)
        r["name"], r["ck_config"], r["ovpn"] = name, ck, ov
        if isinstance(cloak.get("UDP"), bool):
            self.udp.set(cloak["UDP"])
        return r

    def _ok(self):
        r = {k: v.get().strip() for k, v in self.vars.items()}
        if r.get("dgcloak"):
            try:
                r = self._from_dgcloak(r)
            except Exception as e:
                messagebox.showerror(self.t("Ошибка"), str(e), parent=self)
                return
        if not (r["name"] and r["ck_config"] and r["ovpn"]):
            messagebox.showerror(self.t("Ошибка"),
                                 self.t("Заполните .dgcloak ИЛИ название, "
                                        "конфиг Cloak и профиль OpenVPN."),
                                 parent=self)
            return
        try:
            r["port"] = int(r["port"] or 1984)
        except ValueError:
            messagebox.showerror(self.t("Ошибка"), self.t("Порт должен быть числом."), parent=self)
            return
        r["udp"] = self.udp.get()
        r["full_tunnel"] = self.full.get()
        r["reconnect"] = self.recon.get()
        self.result = r
        self.destroy()


class Tooltip:
    """Простой всплывающий текст при наведении на виджет."""

    def __init__(self, widget, text):
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _e=None):
        if self.tip or _e is None:
            return
        wgt = _e.widget
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.minsize(470, 10)
        self.resizable(False, False)  # фиксированный размер окна
        migrate_dirs()
        self.data = load_data()
        self._status_msg = ("Отключено", "gray", {})
        self.ck = None
        self.vpn = None
        self.mgmt = None
        self.active = None
        self.busy = False
        self.up = threading.Event()
        self._mon_stop = None
        self.uiq = queue.Queue()
        self.last_state = None
        self.route_failed = False
        self.verb = False
        self.ext_ip = None
        self.up_since = None
        self.bypass_missing = False
        self.traffic = None
        self._stats_prev = None
        self._rate = None
        self.tray = None
        self.cur_name = ""
        self._icons = {}
        self._last_tray_text = None
        self._last_tray_color = None
        self.verbose = tk.BooleanVar(value=False)
        self.adv_open = False
        self._hint_shown = False
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            last_log = os.path.join(APP_DIR, "last.log")
            if os.path.exists(last_log):
                # ротация: храним лог текущего и предыдущего запуска (prev.log)
                try:
                    os.replace(last_log, os.path.join(APP_DIR, "prev.log"))
                except OSError:
                    pass
            self.logfile = open(last_log, "w", encoding="utf-8", buffering=1)
        except OSError:
            self.logfile = None
        threading.excepthook = lambda a: self._report_exc(a.exc_type, a.exc_value, a.exc_traceback)
        self._fix_ctrl_bindings()
        self._build()
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), 10)
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_x)
        self.bind("<Unmap>", self._on_unmap)
        self._init_icons()
        if not is_admin():
            self.say("Внимание: нет прав администратора. OpenVPN не сможет создать адаптер — "
                     "запустите программу от имени администратора.")
        self.after(400, self._first_run)

    def _first_run(self):
        """Первый запуск: подставить найденные пути; если чего-то нет — мастер установки.
        Если программы на месте, а профилей нет — предложить добавить первый."""
        if not os.path.isfile(self.data["ck_client"]):
            found = find_ck_client()
            if found:
                self.data["ck_client"] = found
        if not os.path.isfile(self.data["openvpn_exe"]):
            found = find_openvpn()
            if found:
                self.data["openvpn_exe"] = found
        save_data(self.data)
        if not (os.path.isfile(self.data["ck_client"]) and os.path.isfile(self.data["openvpn_exe"])):
            SetupDialog(self)
        else:
            self._suggest_profile()

    def _after_setup(self):
        self._suggest_profile()

    def _suggest_profile(self):
        if self.data["profiles"]:
            return
        if messagebox.askyesno(APP_NAME, self.t("Профили не добавлены. Добавить первый профиль сейчас?")):
            self._add()

    def lang(self):
        return self.data.get("language", "ru")

    def t(self, msg, **kw):
        """Перевод строки интерфейса на выбранный язык (ключа нет → русский)."""
        if self.lang() == "en":
            msg = STRINGS_EN.get(msg, msg)
        return msg.format(**kw) if kw else msg

    # ---------- UI ----------
    def _build(self):
        t = self.t
        # Три строки — в одной grid-сетке: правая колонка выравнивает «Выход» и
        # выбор языка по одному правому краю.
        box = ttk.Frame(self)
        box.pack(fill="x", padx=10, pady=(10, 10))
        box.columnconfigure(0, weight=1)

        self.combo = ttk.Combobox(box, state="readonly", width=30)
        self.combo.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._sync_current())
        btns = ttk.Frame(box)
        btns.grid(row=0, column=1, sticky="e", pady=(0, 6))
        self.b_add = ttk.Button(btns, text="+", width=BTN_S, command=self._add)
        self.b_edit = ttk.Button(btns, text=t("Изм."), width=BTN_S, command=self._edit)
        self.b_del = ttk.Button(btns, text=t("Удал."), width=BTN_S, command=self._delete)
        for b in (self.b_add, self.b_edit, self.b_del):
            b.pack(side="left", padx=2)
        self.b_exit = ttk.Button(btns, text=t("Выход"), width=BTN_S, command=self._exit_clicked)
        self.b_exit.pack(side="left", padx=(2, 0))

        self.btn = ttk.Button(box, text=t("Подключить"), width=BTN_W, command=self._toggle)
        self.btn.grid(row=1, column=0, sticky="w", pady=(0, 4))
        # статус — отдельной строкой под кнопкой, столбиком; окно подгоняет высоту
        # моноширинный шрифт — значения статуса выравниваются столбцом
        self.status = ttk.Label(box, text=t("Отключено"), foreground="gray",
                                justify="left", anchor="nw", font=("Consolas", 9))
        self.status.grid(row=2, column=0, columnspan=2, sticky="w", padx=2, pady=(0, 4))

        self.b_adv = ttk.Button(box, text=t("Дополнительно ▾"), width=BTN_W, command=self._toggle_adv)
        self.b_adv.grid(row=3, column=0, sticky="w")
        # выбор языка — в основном окне справа снизу (та же колонка, что и «Выход»)
        langf = ttk.Frame(box)
        langf.grid(row=3, column=1, sticky="e")
        self.lang_label = ttk.Label(langf, text=t("Язык:"))
        self.lang_label.pack(side="left")
        self.lang_combo = ttk.Combobox(langf, state="readonly", width=8,
                                       values=[LANG_NAMES["ru"], LANG_NAMES["en"]])
        self.lang_combo.set(LANG_NAMES[self.lang()])
        self.lang_combo.pack(side="left", padx=(4, 0))
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_lang_pick)
        tip = self.t("Уже выведенные в лог записи останутся на прежнем языке — "
                     "переводятся только новые.")
        self._lang_tips = [Tooltip(w, tip) for w in (self.lang_label, self.lang_combo)]

        # Скрываемая панель: лог и редко нужные настройки
        self.adv = ttk.Frame(self)
        bar = ttk.Frame(self.adv)
        bar.pack(fill="x")
        logbtns = ttk.Frame(bar)
        logbtns.pack(side="left")
        self.b_copy = ttk.Button(logbtns, text=t("Копировать лог"), width=BTN_W, command=self._copy_log)
        self.b_copy.pack()
        self.b_clear = ttk.Button(logbtns, text=t("Очистить лог"), width=BTN_W, command=self._clear_log)
        self.b_clear.pack(pady=(4, 0))
        self.b_paths = ttk.Button(bar, text=t("Пути к Cloak и OpenVPN…"), command=self._paths)
        self.b_paths.pack(side="left", padx=6, anchor="n")
        self.chk_verbose = ttk.Checkbutton(
            self.adv, text=t("Отладочный лог OpenVPN (применится при следующем подключении)"),
            variable=self.verbose)
        self.chk_verbose.pack(anchor="w", pady=(6, 0))
        self.log = tk.Text(self.adv, height=16, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        self._refresh_combo(self.data.get("last_profile"))

    def _on_lang_pick(self, _e):
        code = "en" if self.lang_combo.get() == LANG_NAMES["en"] else "ru"
        if code != self.lang():
            self.data["language"] = code
            save_data(self.data)
            self._apply_lang()

    def _apply_lang(self):
        """Перетекстировать все виджеты и меню трея на выбранном языке."""
        t = self.t
        self.b_edit.config(text=t("Изм."))
        self.b_del.config(text=t("Удал."))
        self.b_exit.config(text=t("Выход"))
        self.btn.config(text=t("Отключить" if self.active else "Подключить"))
        self.b_adv.config(text=t("Дополнительно ▴" if self.adv_open else "Дополнительно ▾"))
        self.b_copy.config(text=t("Копировать лог"))
        self.b_clear.config(text=t("Очистить лог"))
        self.b_paths.config(text=t("Пути к Cloak и OpenVPN…"))
        self.chk_verbose.config(text=t("Отладочный лог OpenVPN (применится при следующем подключении)"))
        self.lang_label.config(text=t("Язык:"))
        for tp in getattr(self, "_lang_tips", ()):
            tp.text = t("Уже выведенные в лог записи останутся на прежнем языке — "
                        "переводятся только новые.")
        if self._status_msg:
            text, color, kw = self._status_msg
            self.set_status(text, color, **kw)
        self._refresh_tray_menu()

    def _exit_clicked(self):
        if self.active and not messagebox.askyesno(
                APP_NAME, self.t("VPN подключён. Отключить и выйти из программы?")):
            return
        self._quit()

    def _toggle_adv(self):
        if self.adv_open:
            self._hide_adv()
        else:
            self._show_adv()

    def _hide_adv(self):
        self.adv.pack_forget()
        self.b_adv.config(text=self.t("Дополнительно ▾"))
        self.geometry("")                # вернуть компактный размер
        self.adv_open = False

    def _show_adv(self):
        if not self.adv_open:            # флаг ведём сами: winfo_ismapped() отстаёт от pack()
            self.adv.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            self.b_adv.config(text=self.t("Дополнительно ▴"))
            self.geometry("720x560")
            self.adv_open = True

    def _refresh_combo(self, select=None):
        names = [p["name"] for p in self.data["profiles"]]
        self.combo["values"] = names
        if select in names:
            self.combo.set(select)
        elif names and self.combo.get() not in names:
            self.combo.current(0)
        elif not names:
            self.combo.set("")
        self._sync_current()

    def _sync_current(self):
        # cur_name читается из потока трея, поэтому дублируем выбор в обычной переменной
        self.cur_name = self.combo.get()
        self._refresh_tray_menu()

    def _refresh_tray_menu(self):
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception as e:  # noqa: BLE001
                self._log_file(f"[tray menu] {e!r}\n")

    def _current(self):
        name = self.combo.get()
        return next((p for p in self.data["profiles"] if p["name"] == name), None)

    # Любое обновление интерфейса из потоков идёт через очередь
    def ui(self, fn):
        self.uiq.put(fn)

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

    def _log_file(self, line):
        if self.logfile:
            try:
                self.logfile.write(line)
            except (OSError, ValueError):
                pass

    def say(self, msg, **kw):
        line = f"[{time.strftime('%H:%M:%S')}] {self.t(msg, **kw)}\n"
        self._log_file(line)
        self.ui(lambda: self._append(line))

    def _report_exc(self, exc, val, tb):
        import traceback
        self.say("ВНУТРЕННЯЯ ОШИБКА:\n{tb}", tb="".join(traceback.format_exception(exc, val, tb)))

    def report_callback_exception(self, exc, val, tb):  # исключения внутри Tk-колбэков
        self._report_exc(exc, val, tb)

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end"))
        self.say("Лог скопирован в буфер обмена.")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self.say("Лог очищен.")

    def _append(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")

    def set_status(self, text, color, _tray=None, **kw):
        """text — русский шаблон; kw подставляются после перевода ({name} и т.п.).
        _tray — готовый текст для тултипа (если отличается от text: например без
        моноширинной набивки — в тултипе шрифт пропорциональный, набивка съезжает)."""
        self._status_msg = (text, color, kw)
        def apply():
            self.status.config(text=self.t(text, **kw), foreground=color)
            self._tray_update(_tray if _tray is not None else self.t(text, **kw), color)
            self.update_idletasks()
            h = self.winfo_reqheight()  # многострочный статус → подогнать высоту окна
            if abs(h - self.winfo_height()) > 4:
                self.geometry(f"{self.winfo_reqwidth()}x{h}")
        self.ui(apply)

    # ---------- иконка и трей ----------
    def _init_icons(self):
        if not HAS_TRAY:
            self.say("Иконка и трей отключены: установите пакеты  pip install pystray pillow")
            return
        try:
            self._icons = {k: cloak_icon.draw_tray_icon(v, 64) for k, v in cloak_icon.STATUS_COLORS.items()}
            buf = io.BytesIO()
            cloak_icon.draw_icon(cloak_icon.BRAND, 64).save(buf, "PNG")
            self._win_icon = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
            self.iconphoto(True, self._win_icon)
            menu = pystray.Menu(self._tray_items)   # меню пересобирается при update_menu()
            self.tray = pystray.Icon("DGCloakVPN", self._icons["gray"],
                                     f"{APP_NAME}: " + self.t("отключено"), menu)
            self.tray.run_detached()
        except Exception as e:  # noqa: BLE001
            self.tray = None
            self.say("Не удалось создать иконку в трее: {e}", e=repr(e))

    def _tray_items(self):
        MI = pystray.MenuItem
        idle = lambda it: not self.busy  # noqa: E731
        yield MI(self.t("Открыть"), lambda i, it: self.ui(self._show), default=True)
        if self.active:
            yield MI(self.t("Отключить «{name}»", name=self.active["name"]),
                     lambda i, it: self.ui(self._toggle), enabled=idle)
            if any(p["name"] != self.active["name"] for p in self.data["profiles"]):
                yield MI(self.t("Отключить «{name}» и подключить",
                                name=self.active["name"]),
                         pystray.Menu(self._tray_connected_items), enabled=idle)
        else:
            if self.cur_name in [p["name"] for p in self.data["profiles"]]:
                yield MI(self.t("Подключить «{name}»", name=self.cur_name),
                         lambda i, it: self.ui(self._toggle), enabled=idle)
            # подменю: профили кроме выбранного (для него есть кнопка выше)
            if any(p["name"] != self.cur_name for p in self.data["profiles"]):
                yield MI(self.t("Подключить"), pystray.Menu(self._tray_profile_items), enabled=idle)
        yield pystray.Menu.SEPARATOR
        yield MI(self.t("Отключить VPN и выйти из программы"), lambda i, it: self.ui(self._exit_clicked))

    def _tray_profile_items(self):
        for p in list(self.data["profiles"]):
            if p["name"] == self.cur_name:
                continue
            yield pystray.MenuItem(p["name"], self._tray_connect_action(p["name"]),
                                   checked=self._tray_checked(p["name"]), radio=True)

    def _tray_connected_items(self):
        """Подменю при активном VPN: переключение на другой профиль."""
        for p in list(self.data["profiles"]):
            if p["name"] == self.active["name"]:
                continue
            yield pystray.MenuItem(p["name"], self._tray_connect_action(p["name"]),
                                   checked=self._tray_checked(p["name"]), radio=True)

    def _tray_connect_action(self, name):
        return lambda i, it: self.ui(lambda: self._connect_named(name))

    def _tray_checked(self, name):
        return lambda it: name == self.cur_name

    def _connect_named(self, name):
        """Выбрать профиль и подключиться; при активном VPN — переключиться на него."""
        if self.busy:
            return
        p = next((x for x in self.data["profiles"] if x["name"] == name), None)
        if not p:
            return
        self.combo.set(name)
        self._sync_current()
        self.verb = self.verbose.get()
        if self.active:
            self._run(lambda: (self._disconnect(), self._connect(p)))   # переключение
        else:
            self._run(lambda: self._connect(p))

    def _tray_update(self, text, color):
        if not self.tray:
            return
        try:
            self.tray.icon = self._icons.get(color, self._icons["gray"])
            self.tray.title = text[:127]  # тултип: пропорц. шрифт — без выравнивания
            self.tray.update_menu()
            # уведомление — только при смене цвета (иначе аптайм спамит каждые 2 с)
            if color != self._last_tray_color and color in ("green", "red") \
                    and self.state() == "withdrawn":
                self.tray.notify(text, APP_NAME)
            self._last_tray_color = color
            self._last_tray_text = text
        except Exception as e:  # noqa: BLE001
            self._log_file(f"[tray] {e!r}\n")

    def _on_unmap(self, e):
        # сворачивание в панель задач -> прячем в трей
        if self.tray and e.widget is self and self.state() == "iconic":
            self.after(50, self._hide_to_tray)

    def _hide_to_tray(self):
        self.withdraw()
        if self.tray and not self._hint_shown:
            self._hint_shown = True
            try:
                self.tray.notify(self.t("Программа продолжает работать в трее. Выход: "
                                        "правый клик по значку → «Отключить VPN и выйти из программы»."),
                                 APP_NAME)
            except Exception:  # noqa: BLE001
                pass

    def _show(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _on_x(self):
        # крестик не закрывает программу, а прячет её в трей (если трей доступен)
        if self.tray:
            self._hide_to_tray()
        else:
            self._quit()

    def _drain(self):
        try:
            while True:
                fn = self.uiq.get_nowait()
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    self._log_file(f"[ui error] {e!r}\n")
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain)

    def _set_locked(self, locked):
        state = "disabled" if locked else "normal"
        for w in (self.b_add, self.b_edit, self.b_del, self.b_paths):
            w.config(state=state)
        self.combo.config(state="disabled" if locked else "readonly")

    # ---------- профили ----------
    def _add(self):
        d = ProfileDialog(self)
        self.wait_window(d)
        if d.result:
            for w in import_profile_files(d.result, self.t):
                self.say("Профиль: {w}", w=w)
            self.data["profiles"].append(d.result)
            save_data(self.data)
            self._refresh_combo(d.result["name"])
            self.say("Файлы профиля скопированы в папку данных программы.")

    def _edit(self):
        p = self._current()
        if not p:
            return
        old_dir = ""
        if p.get("ovpn") and _under_profiles_dir(p["ovpn"]):
            old_dir = os.path.dirname(os.path.abspath(p["ovpn"]))
        d = ProfileDialog(self, p)
        self.wait_window(d)
        if d.result:
            old = p["name"]
            p.update(d.result)
            for w in import_profile_files(p, self.t):
                self.say("Профиль: {w}", w=w)
            new_dir = os.path.dirname(os.path.abspath(p.get("ovpn", "")))
            if old_dir and old_dir != new_dir:  # профиль переименован — старые копии не нужны
                shutil.rmtree(old_dir, ignore_errors=True)
            if self.data.get("last_profile") == old:
                self.data["last_profile"] = p["name"]
            save_data(self.data)
            self._refresh_combo(p["name"])

    def _delete(self):
        p = self._current()
        if p and messagebox.askyesno(self.t("Удалить"),
                                     self.t("Удалить профиль «{name}»?", name=p["name"])):
            self.data["profiles"].remove(p)
            if p.get("ovpn") and _under_profiles_dir(p["ovpn"]):  # убрать нашу копию файлов
                shutil.rmtree(os.path.dirname(os.path.abspath(p["ovpn"])), ignore_errors=True)
            save_data(self.data)
            self._refresh_combo()

    def _paths(self):
        for key, title in (("ck_client", "ck-client.exe"), ("openvpn_exe", "openvpn.exe")):
            path = filedialog.askopenfilename(title=self.t("Путь к {t}", t=title),
                                              initialfile=self.data[key],
                                              filetypes=[("EXE", "*.exe")])
            if path:
                self.data[key] = os.path.normpath(path)
        save_data(self.data)

    # ---------- подключение ----------
    def _toggle(self):
        if self.busy:
            return
        if self.active:
            self._run(self._disconnect)
        else:
            p = self._current()
            if not p:
                messagebox.showinfo(self.t("Нет профиля"), self.t("Сначала добавьте профиль кнопкой «+»."))
                return
            self.verb = self.verbose.get()
            self._run(lambda: self._connect(p))

    def _run(self, fn):
        self.busy = True
        # tk-виджеты трогаем только в главном потоке — _run зовётся и из монитора
        def prep():
            self._refresh_tray_menu()
            self.btn.config(state="disabled")
            self._set_locked(True)
        self.ui(prep)

        def wrapper():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.say("Ошибка: {e}", e=e)
                self._stop_all()
                self._check_loopback()
                self.set_status("Ошибка: {e}", "red", e=e)
                self.ui(self._show_adv)   # открыть панель с логом, чтобы была видна причина
            finally:
                self.ui(self._finish)

        threading.Thread(target=wrapper, daemon=True).start()

    def _finish(self):
        self.busy = False
        self._refresh_tray_menu()
        self.btn.config(state="normal", text=self.t("Отключить" if self.active else "Подключить"))
        self._set_locked(bool(self.active))

    def _pump(self, proc, tag):
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                if tag == "openvpn" and ("route add command failed" in line
                                         or "route addition failed" in line):
                    self.route_failed = True
                self.say(f"{tag}: {line}")

    def _runtime_ovpn(self, p):
        """Копия .ovpn в папке данных с remote 127.0.0.1:<порт Cloak> из профиля.
        Порт в настройках профиля — единственное место, где он задаётся: строки
        remote из исходного .ovpn убираются, своя добавляется с proto по галочке UDP."""
        with open(p["ovpn"], encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f.read().splitlines() if not re.match(r"remote[ \t]", ln.lstrip())]
        proto = "udp" if p.get("udp") else "tcp-client"
        lines.append(f"remote 127.0.0.1 {p['port']} {proto}")
        dst = os.path.join(APP_DIR, "runtime.ovpn")
        with open(dst, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return dst

    def _bypass_ip(self, p):
        """IP сервера Cloak для обходного маршрута. Приоритет — ручной
        bypass_ip; иначе берётся RemoteHost из ck-конфига (домен резолвится
        при каждом подключении — смена IP сервера не ломает обход)."""
        if p.get("bypass_ip"):
            return p["bypass_ip"]
        try:
            with open(p["ck_config"], encoding="utf-8") as f:
                host = json.load(f).get("RemoteHost", "")
        except Exception:
            return None
        if not host:
            return None
        try:
            ip = socket.gethostbyname(host)
        except OSError:
            self.say("Не удалось резолвить RemoteHost «{h}» — обход не добавлен.", h=host)
            return None
        if ip != host:
            self.say("Обходной маршрут до сервера: {h} → {ip}", h=host, ip=ip)
        return ip

    def _connect(self, p):
        ck_exe, ov_exe = self.data["ck_client"], self.data["openvpn_exe"]
        for path in (ck_exe, ov_exe, p["ck_config"], p["ovpn"]):
            if not os.path.isfile(path):
                raise FileNotFoundError(path)
        # UDP-режим определяется самим ck-конфигом ("UDP": true), галочка
        # профиля — лишь фолбэк для конфигов без этого поля. Иначе рассинхрон:
        # ck-client слушает UDP, а клиент ждёт TCP-пробу → «не поднял порт».
        try:
            with open(p["ck_config"], encoding="utf-8") as f:
                if json.load(f).get("UDP") and not p.get("udp"):
                    self.say("В ck-конфиге UDP:true — включаю UDP-режим автоматически.")
                    p = dict(p, udp=True)
        except Exception:
            pass
        self.up.clear()
        self.last_state = None
        self.route_failed = False

        ip_before = external_ip()
        self.say("Внешний IP до подключения: {ip}", ip=ip_before or self.t("не определён"))

        # 0. Предполётные проверки: остатки прошлых запусков
        if repair_loopback():
            self.say("Системный маршрут 127.0.0.1 отсутствовал — восстановлен.")
        if not loopback_ok():
            raise RuntimeError(self.t(
                "Локальный адрес 127.0.0.1 не отвечает — сломана таблица маршрутов "
                "(обычно после аварийного завершения OpenVPN). Перезагрузите ПК."))
        # добить свои процессы, оставшиеся от аварийного прошлого запуска (по сохранённым PID)
        saved = load_pids()
        killed = []
        for key, exe in (("ck_pid", ck_exe), ("vpn_pid", ov_exe)):
            pid = saved.get(key)
            if pid and pid_running(pid, os.path.basename(exe)):
                kill_pid(pid)
                killed.append(f"{os.path.basename(exe)} (PID {pid})")
        save_pids({})
        if killed:
            self.say("Завершил оставшиеся от прошлого запуска: {lst}", lst=", ".join(killed))
            time.sleep(1)  # дать портам освободиться
        # чужие процессы с такими же именами — как раньше, предупреждение.
        # ck-client в режиме admin-API (флаг -a) локальный порт не занимает —
        # это админка, с клиентом не конфликтует → пропускаем.
        for exe in (ck_exe, ov_exe):
            pids = find_procs(os.path.basename(exe))
            if exe is ck_exe and pids:
                cls = proc_cmdlines(os.path.basename(exe))
                pids = [p for p in pids
                        if not re.search(r"(?:^|\s)-a(?:\s|$)", cls.get(p, ""))]
            if pids:
                raise RuntimeError(self.t("Уже запущен {exe} (PID {pids}). Завершите процесс и повторите.",
                                          exe=os.path.basename(exe), pids=", ".join(pids)))
        if not p.get("udp") and port_open("127.0.0.1", p["port"]):
            raise RuntimeError(self.t("Порт {port} уже занят другим процессом.", port=p["port"]))

        # 1. Cloak
        self.set_status("Запуск Cloak…", "orange")
        args = [ck_exe, "-c", p["ck_config"], "-l", str(p["port"])]
        if p.get("server"):
            args += ["-s", p["server"]]
        if p.get("server_port"):
            args += ["-p", p["server_port"]]
        if p.get("udp"):
            args.append("-u")
        self.say("Запуск: {a}", a=" ".join(args))
        self.ck = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace",
                                   creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, args=(self.ck, "cloak"), daemon=True).start()

        if p.get("udp"):
            time.sleep(3)  # в UDP-режиме порт проверить нельзя
        else:
            deadline = time.time() + 15
            while True:
                err = probe("127.0.0.1", p["port"], 1.0)
                if err is None:
                    break
                if self.ck.poll() is not None:
                    raise RuntimeError(self.t("Cloak завершился с кодом {rc}", rc=self.ck.returncode))
                if time.time() > deadline:
                    raise TimeoutError(self.t("Cloak не поднял порт за 15 с (ошибка: {err})", err=err))
                time.sleep(0.5)
        if self.ck.poll() is not None:
            raise RuntimeError(self.t("Cloak завершился с кодом {rc}", rc=self.ck.returncode))
        self.say("Cloak готов.")

        # 2. OpenVPN (консольный) — без GUI, статус через management
        self.set_status("Запуск OpenVPN…", "orange")
        mport = free_port()
        ov_args = [ov_exe, "--config", self._runtime_ovpn(p),
                   "--cd", os.path.dirname(os.path.abspath(p["ovpn"])),
                   "--management", "127.0.0.1", str(mport),
                   "--disable-dco"]  # DCO плохо дружит с локальным прокси
        bypass = self._bypass_ip(p)
        self.bypass_missing = p.get("full_tunnel", True) and not bypass
        if bypass:
            ov_args += ["--route", bypass, "255.255.255.255", "net_gateway"]
        elif p.get("full_tunnel", True):
            self.say("ВНИМАНИЕ: обходной маршрут до сервера Cloak не задан "
                     "(ни bypass_ip, ни RemoteHost) — при полном туннеле "
                     "транспорт зациклится через ~25 с!")
        if p.get("full_tunnel", True):
            # Флаг local: не создавать маршрут до remote. У нас remote = 127.0.0.1,
            # и без этого флага OpenVPN при выходе удаляет системный маршрут loopback.
            ov_args += ["--redirect-gateway", "local", "def1"]
        if self.verb:
            ov_args += ["--verb", "4"]
        self.say("Запуск: {a}", a=" ".join(ov_args))
        self.vpn = subprocess.Popen(ov_args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace",
                                    creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, args=(self.vpn, "openvpn"), daemon=True).start()
        save_pids({"ck_pid": self.ck.pid, "vpn_pid": self.vpn.pid})
        threading.Thread(target=self._mgmt_loop, args=(mport, self.vpn, p), daemon=True).start()

        t0 = time.time()
        next_beat = t0 + 5
        while not self.up.wait(0.5):
            if self.vpn.poll() is not None:
                raise RuntimeError(self.t("OpenVPN завершился с кодом {rc}", rc=self.vpn.returncode))
            if self.ck.poll() is not None:
                raise RuntimeError(self.t("Cloak завершился с кодом {rc}", rc=self.ck.returncode))
            now = time.time()
            if now > t0 + 60:
                raise TimeoutError(self.t("OpenVPN не вышел в CONNECTED за 60 с (последнее состояние: {st})",
                                          st=self.last_state or self.t("нет данных")))
            if now >= next_beat:
                next_beat += 5
                self.say("…жду OpenVPN {s} с; management: {m}; состояние: {st}",
                         s=int(now - t0),
                         m=self.t("подключён") if self.mgmt else self.t("нет"),
                         st=self.last_state or self.t("нет данных"))

        self.active = p
        self.up_since = time.time()
        if self.data.get("last_profile") != p["name"]:
            self.data["last_profile"] = p["name"]
            save_data(self.data)
        self._start_monitor()
        time.sleep(1)
        if self.route_failed:
            self.say("ВНИМАНИЕ: OpenVPN не смог добавить маршруты (Access is denied) — трафик не идёт "
                     "через VPN. Запустите программу от имени администратора.")
            self.set_status("Маршруты не добавлены — нужны права администратора", "red")
        else:
            self.say("Подключено.")
        ip_after = external_ip()
        self.ext_ip = ip_after
        self.say("Внешний IP через VPN: {ip} (до подключения: {prev})",
                 ip=ip_after or self.t("не определён"),
                 prev=ip_before or self.t("не определён"))

    def _mgmt_loop(self, port, proc, p):
        sock = None
        for _ in range(50):
            if proc.poll() is not None:
                return
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                time.sleep(0.2)
        if sock is None:
            self.say("Не удалось подключиться к management-интерфейсу OpenVPN.")
            return  # туннель сам упадёт по таймауту CONNECTED
        sock.settimeout(None)
        self.mgmt = sock
        self.say("management подключён.")
        f = sock.makefile("rw", encoding="utf-8", errors="replace", newline="\n")
        try:
            f.write("state on all\n")
            f.flush()
            for line in f:
                line = line.strip()
                m = STATE_RE.match(line)
                if m:
                    self._on_state(m.group(1), p)
                elif line.startswith(">PASSWORD:"):
                    self.say("OpenVPN запрашивает логин/пароль — это пока не поддерживается: {line}", line=line)
                elif "bytesin=" in line:
                    m = re.search(r"bytesin=(\d+),bytesout=(\d+)", line)
                    if m:
                        now, i, o = time.time(), int(m.group(1)), int(m.group(2))
                        if self._stats_prev:
                            dt = now - self._stats_prev[0]
                            if dt > 0:  # байт/с: in = ↓, out = ↑
                                self._rate = ((i - self._stats_prev[1]) / dt,
                                              (o - self._stats_prev[2]) / dt)
                        self._stats_prev = (now, i, o)
                        self.traffic = (i, o)
                elif line.startswith(">FATAL:"):
                    self.say("OpenVPN: {line}", line=line)
                elif line:
                    self.say("mgmt: {line}", line=line)
        except (OSError, ValueError) as e:
            self.say("management: ошибка чтения {e}", e=repr(e))
        self.say("management-соединение закрыто.")

    def _on_state(self, state, p):
        self.last_state = state
        self.say("Состояние OpenVPN: {st}", st=state)
        text, color = STATES.get(state, (state, "orange"))
        self.set_status(text, color, name=p["name"])
        if state == "CONNECTED":
            self.up.set()

    def _start_monitor(self):
        stop = threading.Event()
        self._mon_stop = stop
        threading.Thread(target=self._monitor, args=(stop,), daemon=True).start()

    def _conn_status(self):
        """Статус живого соединения: имя / VPN IP / аптайм / скорость / «нет обхода»."""
        p = self.active
        if not p:
            return
        up = time.strftime("%H:%M:%S",
                           time.gmtime(max(0, time.time() - (self.up_since or time.time()))))
        rate = ("↓{}/s ↑{}/s".format(_fmt_bytes(self._rate[0]), _fmt_bytes(self._rate[1]))
                if self._rate else "—")
        # метки добиваются пробелами до одной ширины → значения строго друг под другом
        labels = (self.t("Подключено:"), "VPN IP:",
                  self.t("Подключено в течение:"), self.t("Скорость:"))
        w = max(len(x) for x in labels) + 1
        vals = (p["name"], self.ext_ip or "—", up, rate)
        lines = ["{:<{w}}{}".format(lbl, v, w=w) for lbl, v in zip(labels, vals)]
        tray = [lbl + " " + str(v) for lbl, v in zip(labels, vals)]  # тултип — без набивки
        color = "green"
        if self.bypass_missing:
            color = "orange"
            warn = self.t("НЕТ ОБХОДА — риск петли")
            lines.append(warn)
            tray.append(warn)
        self.set_status("\n".join(lines), color, _tray="\n".join(tray))

    def _monitor(self, stop):
        warned = set()
        retries = 0
        while not stop.wait(2):
            if self.active and self.up_since:
                self._conn_status()
                try:
                    if self.mgmt:
                        self.mgmt.sendall(b"load-stats\n")
                except OSError:
                    pass
            ck_dead = self.ck and self.ck.poll() is not None
            vpn_dead = self.vpn and self.vpn.poll() is not None
            if not (ck_dead or vpn_dead):
                continue
            p = self.active
            if p and p.get("reconnect", True) and retries < 3 and not self.busy:
                retries += 1
                self.say("Соединение оборвалось — переподключение ({n}/3)…", n=retries)
                self._stop_all()
                self._run(lambda: self._connect(p))
                return
            if ck_dead and "ck" not in warned:
                warned.add("ck")
                self.say("Cloak остановился (код {rc}) — VPN не работает.", rc=self.ck.returncode)
                self.set_status("Cloak остановлен — VPN не работает", "red")
            if vpn_dead and "vpn" not in warned:
                warned.add("vpn")
                self.say("OpenVPN завершился (код {rc}).", rc=self.vpn.returncode)
                self.set_status("OpenVPN завершился", "red")

    # ---------- отключение ----------
    def _disconnect(self):
        self.say("Отключаюсь…")
        self._stop_all()
        self._check_loopback()
        self.say("Отключено.")
        self.set_status("Отключено", "gray")

    def _check_loopback(self):
        if repair_loopback():
            self.say("После отключения маршрут 127.0.0.1 пропал — восстановлен.")
        if not loopback_ok():
            self.say("ВНИМАНИЕ: 127.0.0.1 перестал отвечать — таблица маршрутов повреждена. "
                     "Остальные программы (например v2rayN) могут не работать до перезагрузки.")

    def _stop_all(self):
        if self._mon_stop:
            self._mon_stop.set()
        self._stop_vpn()
        self._stop_ck()
        save_pids({})
        self.active = None
        self.up_since = None
        self.ext_ip = None
        self.bypass_missing = False
        self.traffic = None
        self._stats_prev = None
        self._rate = None

    def _stop_vpn(self):
        vpn = self.vpn
        if vpn and vpn.poll() is None:
            try:
                if self.mgmt:
                    self.mgmt.sendall(b"signal SIGTERM\n")  # штатное завершение
            except OSError:
                pass
            try:
                vpn.wait(10)
            except subprocess.TimeoutExpired:
                vpn.terminate()
                try:
                    vpn.wait(5)
                except subprocess.TimeoutExpired:
                    vpn.kill()
        self.vpn = None
        try:
            if self.mgmt:
                self.mgmt.close()
        except OSError:
            pass
        self.mgmt = None

    def _stop_ck(self):
        if self.ck and self.ck.poll() is None:
            self.ck.terminate()
            try:
                self.ck.wait(5)
            except subprocess.TimeoutExpired:
                self.ck.kill()
        self.ck = None

    def _quit(self):
        self._stop_all()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


def single_instance_ok():
    """Второй экземпляр клиента не запускаем (mutex, Windows)."""
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local\\DGCloakVPNSingleton")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(
                0, "DGCloak VPN уже запущен / is already running.", APP_NAME, 0x40)
            return False
    except Exception:
        pass  # не Windows или нет ctypes — не блокируем
    return True


if __name__ == "__main__":
    if single_instance_ok():
        App().mainloop()
