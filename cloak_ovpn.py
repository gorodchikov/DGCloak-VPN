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
APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakVPN")
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
    "IP сервера Cloak: исключить из туннеля": "Cloak server IP: bypass tunnel",
    "UDP-режим (-u, для OpenVPN по UDP)": "UDP mode (-u, for OpenVPN over UDP)",
    "Весь трафик через VPN (redirect-gateway local def1)":
        "Route all traffic via VPN (redirect-gateway local def1)",
    "Порт — локальный порт Cloak (-l); remote в .ovpn подставляется автоматически.\n"
    "IP-обход добавит маршрут через основной шлюз, чтобы трафик Cloak\n"
    "не заворачивался в сам VPN. Файлы профиля копируются в папку\n"
    "программы — исходные после этого можно удалить.":
        "Port — local Cloak port (-l); remote in .ovpn is injected automatically.\n"
        "Bypass IP adds a route via the main gateway so Cloak traffic\n"
        "doesn't go into the VPN itself. Profile files are copied into the\n"
        "program folder — the originals can be deleted.",
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
    "в %APPDATA%\\DGCloakVPN. Пути можно изменить позже: "
    "Дополнительно → «Пути к Cloak и OpenVPN…».":
        "Auto setup: OpenVPN via winget, Cloak — latest GitHub release "
        "into %APPDATA%\\DGCloakVPN. Paths can be changed later: "
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
    "Состояние OpenVPN: ": "OpenVPN state: ",
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
                             capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == exe_name.lower():
            pids.append(parts[1])
    return pids


def pid_running(pid, exe_name):
    """Жив ли процесс с этим PID и ожидаемым именем exe (защита от повторного использования PID)."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
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
    """ck-client в папке данных или PATH."""
    cands = [os.path.join(APP_DIR, "ck-client.exe")]
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


def download(url, dst, on_progress):
    """Скачать файл, вызывая on_progress(получено, всего)."""
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


def import_profile_files(r):
    """Скопировать файлы профиля (конфиг Cloak, .ovpn и его внешние ключи) в
    PROFILES_DIR/<имя>/ и подставить новые пути в r — исходники можно удалить.
    Возвращает список предупреждений."""
    warnings = []
    safe = re.sub(r"[^\w\-]+", "_", r.get("name", "")).strip("_") or "profile"
    dest = os.path.join(PROFILES_DIR, safe)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        return [f"не удалось создать папку {dest}: {e}"]
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
            warnings.append(f"{key}: {e}")
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
            warnings.append(f"ovpn: {e}")
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
                         "в %APPDATA%\\DGCloakVPN. Пути можно изменить позже: "
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
            # Cloak: свежий релиз GitHub -> %APPDATA%\DGCloakVPN\ck-client.exe
            if not os.path.isfile(app.data["ck_client"]):
                self._say("скачиваю свежий ck-client с GitHub…")
                try:
                    url, _ = cloak_latest_url()
                    dst = os.path.join(APP_DIR, "ck-client.exe")
                    download(url, dst, self._set_progress)
                    app.data["ck_client"] = os.path.normpath(dst)
                except Exception as e:  # noqa: BLE001
                    self._say(f"Cloak не скачался: {e}. Скачайте вручную: github.com/cbeuw/Cloak/releases")
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
                        self._say(f"winget вернул код {r.returncode} — установите OpenVPN вручную.")
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
                    self._say(f"{title} есть, но не запускается: {path}")
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
        ("ck_config", "Cloak конфиг (.json)", [("JSON", "*.json"), ("Все", "*.*")]),
        ("ovpn", "OpenVPN профиль (.ovpn)", [("OVPN", "*.ovpn"), ("Все", "*.*")]),
    ]
    ADV_FIELDS = [
        ("port", "Локальный порт Cloak (-l)", None),
        ("server", "Сервер Cloak (-s), если не в конфиге", None),
        ("server_port", "Порт сервера Cloak (-p)", None),
        ("bypass_ip", "IP сервера Cloak: исключить из туннеля", None),
    ]

    def __init__(self, parent, profile=None):
        super().__init__(parent)
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
        n = len(self.ADV_FIELDS)
        ttk.Checkbutton(self.advf, text=t("UDP-режим (-u, для OpenVPN по UDP)"),
                        variable=self.udp).grid(row=n, column=1, sticky="w", pady=4)
        ttk.Checkbutton(self.advf, text=t("Весь трафик через VPN (redirect-gateway local def1)"),
                        variable=self.full).grid(row=n + 1, column=1, sticky="w")
        ttk.Label(self.advf, foreground="gray",
                  text=t("Порт — локальный порт Cloak (-l); remote в .ovpn подставляется автоматически.\n"
                         "IP-обход добавит маршрут через основной шлюз, чтобы трафик Cloak\n"
                         "не заворачивался в сам VPN. Файлы профиля копируются в папку\n"
                         "программы — исходные после этого можно удалить.")
                  ).grid(row=n + 2, column=0, columnspan=3, padx=8, sticky="w")
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
            if var is self.vars["ck_config"] and not self.vars["name"].get():
                self.vars["name"].set(os.path.splitext(os.path.basename(path))[0])

    def _ok(self):
        r = {k: v.get().strip() for k, v in self.vars.items()}
        if not (r["name"] and r["ck_config"] and r["ovpn"]):
            messagebox.showerror(self.t("Ошибка"),
                                 self.t("Заполните название, конфиг Cloak и профиль OpenVPN."),
                                 parent=self)
            return
        try:
            r["port"] = int(r["port"] or 1984)
        except ValueError:
            messagebox.showerror(self.t("Ошибка"), self.t("Порт должен быть числом."), parent=self)
            return
        r["udp"] = self.udp.get()
        r["full_tunnel"] = self.full.get()
        self.result = r
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.minsize(470, 10)
        self.resizable(False, False)  # фиксированный размер окна
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
        self.tray = None
        self.cur_name = ""
        self._icons = {}
        self._last_tray_text = None
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

    def t(self, s, **kw):
        """Перевод строки интерфейса на выбранный язык (ключа нет → русский)."""
        if self.lang() == "en":
            s = STRINGS_EN.get(s, s)
        return s.format(**kw) if kw else s

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

        mid = ttk.Frame(box)
        mid.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.btn = ttk.Button(mid, text=t("Подключить"), width=BTN_W, command=self._toggle)
        self.btn.pack(side="left")
        self.status = ttk.Label(mid, text=t("Отключено"), foreground="gray", wraplength=380)
        self.status.pack(side="left", padx=12)

        self.b_adv = ttk.Button(box, text=t("Дополнительно ▾"), width=BTN_W, command=self._toggle_adv)
        self.b_adv.grid(row=2, column=0, sticky="w")
        # выбор языка — в основном окне справа снизу (та же колонка, что и «Выход»)
        langf = ttk.Frame(box)
        langf.grid(row=2, column=1, sticky="e")
        self.lang_label = ttk.Label(langf, text=t("Язык:"))
        self.lang_label.pack(side="left")
        self.lang_combo = ttk.Combobox(langf, state="readonly", width=8,
                                       values=[LANG_NAMES["ru"], LANG_NAMES["en"]])
        self.lang_combo.set(LANG_NAMES[self.lang()])
        self.lang_combo.pack(side="left", padx=(4, 0))
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_lang_pick)

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

    def _log_file(self, line):
        if self.logfile:
            try:
                self.logfile.write(line)
            except (OSError, ValueError):
                pass

    def say(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {self.t(msg)}\n"
        self._log_file(line)
        self.ui(lambda: self._append(line))

    def _report_exc(self, exc, val, tb):
        import traceback
        self.say("ВНУТРЕННЯЯ ОШИБКА:\n" + "".join(traceback.format_exception(exc, val, tb)))

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

    def set_status(self, text, color, **kw):
        """text — русский шаблон; kw подставляются после перевода ({name} и т.п.)."""
        self._status_msg = (text, color, kw)
        def apply():
            self.status.config(text=self.t(text, **kw), foreground=color)
            self._tray_update(self.t(text, **kw), color)
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
            self.say(f"Не удалось создать иконку в трее: {e!r}")

    def _tray_items(self):
        MI = pystray.MenuItem
        idle = lambda it: not self.busy  # noqa: E731
        yield MI(self.t("Открыть"), lambda i, it: self.ui(self._show), default=True)
        if self.active:
            # один пункт: отключить и/или переключиться — отдельное «Отключить» путало
            yield MI(self.t("Отключить «{name}» и подключить", name=self.active["name"]),
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
        """Подменю при активном VPN: простое отключение и переключение на другой профиль."""
        yield pystray.MenuItem(self.t("Отключить"), lambda i, it: self.ui(self._toggle))
        yield pystray.Menu.SEPARATOR
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
            self.tray.title = f"{APP_NAME}: {text}"[:120]
            self.tray.update_menu()
            changed = text != self._last_tray_text
            self._last_tray_text = text
            if changed and color in ("green", "red") and self.state() == "withdrawn":
                self.tray.notify(text, APP_NAME)
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
            for w in import_profile_files(d.result):
                self.say(f"Профиль: {w}")
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
            for w in import_profile_files(p):
                self.say(f"Профиль: {w}")
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
        self._refresh_tray_menu()
        self.btn.config(state="disabled")
        self._set_locked(True)

        def wrapper():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.say(f"Ошибка: {e}")
                self._stop_all()
                self._check_loopback()
                self.set_status(f"Ошибка: {e}", "red")
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

    def _connect(self, p):
        ck_exe, ov_exe = self.data["ck_client"], self.data["openvpn_exe"]
        for path in (ck_exe, ov_exe, p["ck_config"], p["ovpn"]):
            if not os.path.isfile(path):
                raise FileNotFoundError(path)
        self.up.clear()
        self.last_state = None
        self.route_failed = False

        # 0. Предполётные проверки: остатки прошлых запусков
        if repair_loopback():
            self.say("Системный маршрут 127.0.0.1 отсутствовал — восстановлен.")
        if not loopback_ok():
            raise RuntimeError("Локальный адрес 127.0.0.1 не отвечает — сломана таблица маршрутов "
                               "(обычно после аварийного завершения OpenVPN). Перезагрузите ПК.")
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
            self.say("Завершил оставшиеся от прошлого запуска: " + ", ".join(killed))
            time.sleep(1)  # дать портам освободиться
        # чужие процессы с такими же именами — как раньше, предупреждение
        for exe in (ck_exe, ov_exe):
            pids = find_procs(os.path.basename(exe))
            if pids:
                raise RuntimeError(f"Уже запущен {os.path.basename(exe)} (PID {', '.join(pids)}). "
                                   "Завершите процесс и повторите.")
        if not p.get("udp") and port_open("127.0.0.1", p["port"]):
            raise RuntimeError(f"Порт {p['port']} уже занят другим процессом.")

        # 1. Cloak
        self.set_status("Запуск Cloak…", "orange")
        args = [ck_exe, "-c", p["ck_config"], "-l", str(p["port"])]
        if p.get("server"):
            args += ["-s", p["server"]]
        if p.get("server_port"):
            args += ["-p", p["server_port"]]
        if p.get("udp"):
            args.append("-u")
        self.say("Запуск: " + " ".join(args))
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
                    raise RuntimeError(f"Cloak завершился с кодом {self.ck.returncode}")
                if time.time() > deadline:
                    raise TimeoutError(f"Cloak не поднял порт за 15 с (ошибка: {err})")
                time.sleep(0.5)
        if self.ck.poll() is not None:
            raise RuntimeError(f"Cloak завершился с кодом {self.ck.returncode}")
        self.say("Cloak готов.")

        # 2. OpenVPN (консольный) — без GUI, статус через management
        self.set_status("Запуск OpenVPN…", "orange")
        mport = free_port()
        ov_args = [ov_exe, "--config", self._runtime_ovpn(p),
                   "--cd", os.path.dirname(os.path.abspath(p["ovpn"])),
                   "--management", "127.0.0.1", str(mport),
                   "--disable-dco"]  # DCO плохо дружит с локальным прокси
        if p.get("bypass_ip"):
            ov_args += ["--route", p["bypass_ip"], "255.255.255.255", "net_gateway"]
        if p.get("full_tunnel", True):
            # Флаг local: не создавать маршрут до remote. У нас remote = 127.0.0.1,
            # и без этого флага OpenVPN при выходе удаляет системный маршрут loopback.
            ov_args += ["--redirect-gateway", "local", "def1"]
        if self.verb:
            ov_args += ["--verb", "4"]
        self.say("Запуск: " + " ".join(ov_args))
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
                raise RuntimeError(f"OpenVPN завершился с кодом {self.vpn.returncode}")
            if self.ck.poll() is not None:
                raise RuntimeError(f"Cloak завершился с кодом {self.ck.returncode}")
            now = time.time()
            if now > t0 + 60:
                raise TimeoutError("OpenVPN не вышел в CONNECTED за 60 с "
                                   f"(последнее состояние: {self.last_state or 'нет данных'})")
            if now >= next_beat:
                next_beat += 5
                self.say(f"…жду OpenVPN {int(now - t0)} с; management: "
                         f"{'подключён' if self.mgmt else 'нет'}; состояние: {self.last_state or 'нет'}")

        self.active = p
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
            return
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
                    self.say("OpenVPN запрашивает логин/пароль — это пока не поддерживается: " + line)
                elif line.startswith(">FATAL:"):
                    self.say("OpenVPN: " + line)
                elif line:
                    self.say("mgmt: " + line)
        except (OSError, ValueError) as e:
            self.say(f"management: ошибка чтения {e!r}")
        self.say("management-соединение закрыто.")

    def _on_state(self, state, p):
        self.last_state = state
        self.say(f"Состояние OpenVPN: {state}")
        text, color = STATES.get(state, (state, "orange"))
        self.set_status(text, color, name=p["name"])
        if state == "CONNECTED":
            self.up.set()

    def _start_monitor(self):
        stop = threading.Event()
        self._mon_stop = stop
        threading.Thread(target=self._monitor, args=(stop,), daemon=True).start()

    def _monitor(self, stop):
        warned = set()
        while not stop.wait(2):
            if self.ck and self.ck.poll() is not None and "ck" not in warned:
                warned.add("ck")
                self.say(f"Cloak остановился (код {self.ck.returncode}) — VPN не работает.")
                self.set_status("Cloak остановлен — VPN не работает", "red")
            if self.vpn and self.vpn.poll() is not None and "vpn" not in warned:
                warned.add("vpn")
                self.say(f"OpenVPN завершился (код {self.vpn.returncode}).")
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


if __name__ == "__main__":
    App().mainloop()
