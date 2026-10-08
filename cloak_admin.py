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

import base64
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_NAME = "DGCloak Admin"
DGCLOAK_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloak")
OLD_APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakAdmin")
OLD_VPN_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakVPN")
APP_DIR = os.path.join(DGCLOAK_DIR, "Admin")
DATA_FILE = os.path.join(APP_DIR, "data.json")
BUNDLES_DIR = os.path.join(APP_DIR, "bundles")
BIN_DIR = os.path.join(DGCLOAK_DIR, "bin")  # общие зависимости: plink/pscp/ck-client

# Скрипты лежат рядом с исходником/exe (для onefile — внутри _MEIPASS)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
if not os.path.isdir(SCRIPTS_DIR) and getattr(sys, "_MEIPASS", None):
    SCRIPTS_DIR = os.path.join(sys._MEIPASS, "scripts")

CK_VERSION = "2.12.0"
INT64_MAX = 9223372036854775807
FAR_FUTURE = 2000000000  # ~2033 — «бессрочный» юзер Cloak


def _fmt_rate(bps):
    """байт/с → Мбит/с компактно; отсутствие/INT64_MAX = безлимит (∞)."""
    if bps is None or bps >= INT64_MAX:
        return "∞"
    mb = bps * 8 / 1e6
    return "%gМ" % round(mb, 1) if mb >= 1 else "%dк" % round(bps * 8 / 1e3)


def _fmt_bytes(v):
    """байт → объём (МБ/ГБ); отсутствие/INT64_MAX = безлимит (∞)."""
    if v is None or v >= INT64_MAX:
        return "∞"
    return ("%gG" % round(v / 1073741824, 1)) if v >= 1073741824 \
        else "%dM" % round(v / 1048576)


def _fmt_limits(up, down):
    u, d = _fmt_rate(up), _fmt_rate(down)
    return "∞" if u == "∞" and d == "∞" else "↑%s ↓%s" % (u, d)


def _fmt_quota(up, down):
    u, d = _fmt_bytes(up), _fmt_bytes(down)
    return "∞" if u == "∞" and d == "∞" else "↑%s ↓%s" % (u, d)

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
            raise RuntimeError("%s: скачалось не-exe (%d байт)"
                               % (name, len(blob)))
        p = os.path.join(BIN_DIR, name)
        with open(p, "wb") as f:
            f.write(blob)
        say("  %s → %s (%.1f МБ)" % (name, p, len(blob) / 1e6))


def install_openssh(say):
    """OpenSSH-клиент — это Windows-компонент; ставится только из-под
    админа. Возвращает True, если ssh.exe появился."""
    try:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return False
        say("  ставлю компонент «Клиент OpenSSH» (dism)…")
        p = subprocess.run(
            ["dism", "/Online", "/Add-Capability",
             "/CapabilityName:OpenSSH.Client~~~~0.0.1.0"],
            capture_output=True, text=True, timeout=600,
            creationflags=CREATE_NO_WINDOW)
        say("  dism: %s" % (p.stdout or p.stderr).strip().splitlines()[-1][:120])
    except Exception as e:
        say("  dism не сработал: %s" % e)
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
    raise RuntimeError("в последнем релизе Cloak нет ck-client-windows-amd64*.exe")


def download_ck_client(say):
    """Скачать ck-client.exe в BIN_DIR (как plink/pscp — все внешние
    инструменты в одном месте)."""
    url, size = cloak_release_url()
    dst = os.path.join(BIN_DIR, "ck-client.exe")
    os.makedirs(BIN_DIR, exist_ok=True)
    say("  скачиваю ck-client с GitHub: %s" % url.split("/")[-1])
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
                say("  …%.1f / %.1f МБ" % (done / 1e6, (size or done) / 1e6))
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
        tw.geometry("+%d+%d" % (x, y))
        tk.Label(tw, text=self.text, bg="#ffffd8", fg="#222",
                 relief="solid", bd=1, padx=6, pady=4,
                 font=("", 9), justify="left").pack()
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

    def __init__(self, srv, log):
        self.srv = srv
        self.log = log
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
                raise SSHErr("Не найдены ssh/scp (Windows OpenSSH).")
        else:
            self.backend = "putty"
            self.plink, self.pscp = find_putty()
            if not self.plink or not self.pscp:
                raise SSHErr("Не найдены plink/pscp (PuTTY). "
                             "Установи PuTTY или укажи OpenSSH-ключ в настройках сервера.")

    def _target(self):
        return "%s@%s" % (self.srv.get("user", "ubuntu"), self.srv["host"])

    def _find_putty(self):
        if not self.plink:
            self.plink, self.pscp = find_putty()
        return self.plink, self.pscp

    def _argv(self, cmd):
        if (self.backend == "openssh" and not self._auth_pw
                and self.srv.get("key")):
            # -n (stdin=/dev/null) ломает sudo -S: пароль не доедет.
            # Отключаем, когда есть пароль юзера (потенциально нужен sudo -S).
            no_stdin = [] if self.srv.get("password") else ["-n"]
            return [self.ssh_exe] + no_stdin + ["-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=15",
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
                and self.srv.get("key")):
            return [self.scp_exe, "-B", "-o", "StrictHostKeyChecking=accept-new",
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

    def _try(self, args, input_text, timeout):
        """Один запуск + TOFU retry по fingerprint для plink-семейства."""
        # stdin=PIPE обязателен: в --noconsole exe нет консольного stdin,
        # наследование битого хэндла роняет ssh/scp молча с пустым выводом
        p = subprocess.run(args, input=input_text or "", capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        out = (p.stdout or "") + (p.stderr or "")
        base = os.path.basename(args[0]).lower()
        if (base.startswith(("plink", "pscp")) and p.returncode != 0
                and "host key" in out and "-hostkey" not in args):
            # plink в нон-консоли не читает 'y' со stdin — достаём fingerprint
            # из текста промпта и повторяем с -hostkey (TOFU, без интерактива)
            fp = re.search(r"fingerprint is:\s*\S+\s+\d+\s+(SHA256:\S+)", out)
            if fp:
                # -hostkey — опция, должна стоять ДО host (иначе уедет в remote-команду)
                p = subprocess.run(args[:-2] + ["-hostkey", fp.group(1)]
                                   + args[-2:],
                                   input=input_text or "", capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=timeout,
                                   creationflags=CREATE_NO_WINDOW)
                out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out

    def _spawn(self, args, input_text=None, timeout=180):
        """Запуск с фолбэком: ключ openssh отвергнут сервером
        (VM пересоздана/снапшот откачен) → повтор по паролю через plink."""
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
                    self.log("  ключ отвергнут, пароль тоже не подошёл")
                    return rc2, out2
                # подключились: ключ мёртв → весь объект дальше по паролю.
                # rc2 != 0 здесь — ошибка КОМАНДЫ на сервере, её и возвращаем
                self._auth_pw = True
                self.log("  ключ отвергнут сервером — работаю по паролю")
                return rc2, out2
        return rc, out

    def run(self, cmd, timeout=120):
        args = self._argv(cmd)
        # в режиме sudo-по-паролю шлём пароль в stdin — его прочитает sudo -S
        inp = (self.sudo_pw + "\n") if self.sudo_pw else ""
        rc, out = self._spawn(args, input_text=inp, timeout=timeout)
        if rc != 0:
            raise SSHErr("SSH rc=%s: %s" % (rc, out.strip()[:400] or "(пустой вывод)"))
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
                "на сервере не установлен sudo.\n"
                "В консоли VM под %s: su - (пароль root), затем\n"
                "  apt install -y sudo && /usr/sbin/usermod -aG sudo %s\n"
                "или разреши вход root по SSH и логинься как root."
                % (self.srv.get("user", "<юзер>"),
                   self.srv.get("user", "<юзер>")))
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
                self.log("  sudo с паролем — ок")
                return
            raise SSHErr("sudo отверг пароль или юзер не в sudoers.")
        raise SSHErr("sudo требует пароль, а пароль не задан.\n"
                     "Варианты: укажи пароль юзера в настройках сервера,\n"
                     "дай NOPASSWD (visudo: user ALL=(ALL) NOPASSWD:ALL)\n"
                     "или логинься как root.")

    def run_stream(self, cmd, on_line, timeout=None):
        """Стриминг stdout+stderr построчно (для долгих деплой-скриптов)."""
        args = self._argv(cmd)
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.PIPE if self.sudo_pw
                             else subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
        if self.sudo_pw:
            try:
                p.stdin.write(self.sudo_pw + "\n")
                p.stdin.close()
            except (OSError, ValueError):
                pass
        t0 = time.time()
        assert p.stdout is not None
        seen = []
        for line in p.stdout:
            seen.append(line)
            on_line(line.rstrip("\n"))
            if timeout and time.time() - t0 > timeout:
                p.kill()
                raise SSHErr("Таймаут %s с: %s" % (timeout, cmd[:80]))
        p.wait(timeout=10)
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
            p2 = subprocess.Popen(args2, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace",
                                  stdin=subprocess.PIPE if need_pipe
                                  else subprocess.DEVNULL,
                                  creationflags=CREATE_NO_WINDOW)
            try:
                out, _ = p2.communicate(input=inp2,
                                        timeout=timeout or 300)
            except subprocess.TimeoutExpired:
                p2.kill()
                raise SSHErr("Таймаут: %s" % cmd[:80])
            for line in (out or "").splitlines():
                on_line(line)
            out_tail = out or ""
        if is_plink and p.returncode == 255 and not out_tail:
            raise SSHErr("plink: соединение прервано (255)")
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
            raise SSHErr("upload: %s" % out.strip()[:300])
        return out

    def run_script(self, filename, args="", timeout=600):
        """Залить скрипт из scripts/ в /tmp и выполнить под sudo bash."""
        local = os.path.join(SCRIPTS_DIR, filename)
        if not os.path.isfile(local):
            raise SSHErr("Нет скрипта: %s" % local)
        remote = "/tmp/dgadm-%s" % filename
        self.upload(local, remote)
        # CRLF-страховка: скрипты редактируются на Windows
        return self.run("sed -i 's/\\r$//' %s && %sbash %s %s"
                        % (remote, self.sudo, remote, args),
                        timeout=timeout)

    def run_script_stream(self, filename, args, on_line, timeout=900):
        local = os.path.join(SCRIPTS_DIR, filename)
        remote = "/tmp/dgadm-%s" % filename
        self.upload(local, remote)
        return self.run_stream(
            "sed -i 's/\\r$//' %s && %sbash %s %s"
            % (remote, self.sudo, remote, args),
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
        self.proc = subprocess.Popen(
            [self.ck, "-a", self.srv["admin_uid"], "-c", self.cfg_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._pump, args=(self.proc.stdout,), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                line = self._outq.get(timeout=0.5)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise CloakAPIErr("ck-client -a завершился, rc=%s" % self.proc.returncode)
                continue
            self._log_line(line)
            m = re.search(r"API base is (?:https?://)?([\d.]+:\d+)", line)
            if m:
                self.base = "http://%s" % m.group(1)
                return
        raise CloakAPIErr("ck-client -a не поднял API base за %s с" % timeout)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.base = None

    def _req(self, method, path, body=None, _retry=True):
        if not self.base:
            raise CloakAPIErr("admin-API не запущен")
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
                self.log("  admin-API: рестарт ck-client (%s)" % e)
                self.stop()
                self.start()
                return self._req(method, path, body, _retry=False)
            raise CloakAPIErr("admin-API недоступен: %s" % e)

    # --- операции ---
    def list_users(self):
        code, r = self._req("GET", "/admin/users")
        if code != 200:
            raise CloakAPIErr("GET /admin/users → %s: %s" % (code, r))
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
            raise CloakAPIErr("POST user → %s: %s" % (code, r))
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
            raise CloakAPIErr("PUT user → %s: %s" % (code, r))
        return True

    def delete_user(self, uid):
        code, r = self._req("DELETE", "/admin/users/" + uid_to_b64url(uid))
        if code not in (200, 204):
            raise CloakAPIErr("DELETE user → %s: %s" % (code, r))
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
        self.srv = srv or {}
        super().__init__(parent, title)

    def body(self, f):
        self.vars = {}
        fields = [
            ("name", "Название", self.srv.get("name", "")),
            ("host", "Доменное имя/IP", self.srv.get("host", "")),
            ("ssh_port", "SSH порт", str(self.srv.get("ssh_port", 22))),
            ("user", "SSH логин", self.srv.get("user", "ubuntu")),
            ("ppk", "Ключ .ppk (PuTTY)", self.srv.get("ppk", "")),
            ("key", "Ключ OpenSSH", self.srv.get("key", "")),
            ("password", "Пароль (если нет ключей)", self.srv.get("password", "")),
        ]
        for i, (k, label, val) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=3)
            v = tk.StringVar(value=val)
            self.vars[k] = v
            e = ttk.Entry(f, textvariable=v, width=42)
            e.grid(row=i, column=1, padx=4, pady=3)
            if k in ("ppk", "key"):
                ttk.Button(f, text="…", width=2,
                           command=lambda v=v: v.set(filedialog.askopenfilename() or v.get())
                           ).grid(row=i, column=2)
            if k == "password":
                e.config(show="*")
        ttk.Label(f, text="Приоритет: ключ OpenSSH → .ppk → пароль",
                  foreground="#666").grid(row=len(fields), column=0,
                                          columnspan=3, sticky="w", padx=4)
        return f

    def validate(self):
        if not self.vars["name"].get().strip() or not self.vars["host"].get().strip():
            messagebox.showerror("Сервер", "Нужны имя и хост", parent=self)
            return False
        if not (self.vars["ppk"].get().strip() or self.vars["key"].get().strip()
                or self.vars["password"].get()):
            messagebox.showerror("Сервер", "Нужен ключ (.ppk/OpenSSH) или пароль",
                                 parent=self)
            return False
        try:
            port = int(self.vars["ssh_port"].get() or 22)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror("Сервер", "SSH порт: число 1-65535", parent=self)
            return False
        # частая ошибка — выбрать .pub вместо приватного ключа
        key = self.vars["key"].get().strip()
        if key.lower().endswith(".pub"):
            priv = key[:-4]
            if os.path.isfile(priv):
                self.vars["key"].set(priv)
            else:
                messagebox.showerror(
                    "Сервер", "Это публичный ключ (.pub) — нужен приватный,\n"
                    "обычно тот же файл без расширения .pub", parent=self)
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
        self.srv_mask = srv_mask
        self.rec = rec
        super().__init__(parent, title)

    def body(self, f):
        rec = self.rec or {}
        exp_days = "0"
        if rec.get("expiry") and rec["expiry"] < FAR_FUTURE:
            exp_days = str(max(1, round((rec["expiry"] - time.time()) / 86400)))
        fields = [("name", "Имя (CN, [a-z0-9_-])", rec.get("cn", "")),
                  ("expiry_days", "Срок жизни, дней (0 = бессрочно)", exp_days),
                  ("sessions", "Макс. одновременных подключений",
                   str(rec.get("sessions") or 16)),
                  ("up_mbits", "Лимит скорости ↑, Мбит/с (0 = безлимит)",
                   "%g" % (rec["up_rate"] * 8 / 1e6)
                   if rec.get("up_rate") and rec["up_rate"] < INT64_MAX else "0"),
                  ("down_mbits", "Лимит скорости ↓, Мбит/с (0 = безлимит)",
                   "%g" % (rec["down_rate"] * 8 / 1e6)
                   if rec.get("down_rate") and rec["down_rate"] < INT64_MAX else "0"),
                  ("up_mb", "Квота трафика ↑, МБ (0 = безлимит)",
                   str(rec["up_credit"] // 1048576)
                   if rec.get("up_credit") and rec["up_credit"] < INT64_MAX else "0"),
                  ("down_mb", "Квота трафика ↓, МБ (0 = безлимит)",
                   str(rec["down_credit"] // 1048576)
                   if rec.get("down_credit") and rec["down_credit"] < INT64_MAX else "0"),
                  ("mask", "Домен для маскировки",
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
            messagebox.showerror("Юзер", "Имя: только латиница, цифры, _ и -",
                                 parent=self)
            return False
        lbl = {"expiry_days": "Срок жизни, дней",
               "sessions": "Макс. одновременных подключений",
               "up_mb": "Квота трафика ↑, МБ", "down_mb": "Квота трафика ↓, МБ",
               "up_mbits": "Лимит скорости ↑, Мбит/с",
               "down_mbits": "Лимит скорости ↓, Мбит/с"}
        for k in ("expiry_days", "sessions", "up_mb", "down_mb"):
            try:
                int(self.vars[k].get() or 0)
            except ValueError:
                messagebox.showerror("Юзер", "Поле «%s» — только целое число"
                                     % lbl[k], parent=self)
                return False
        for k in ("up_mbits", "down_mbits"):
            try:
                float(self.vars[k].get() or 0)
            except ValueError:
                messagebox.showerror("Юзер", "Поле «%s» — только число"
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
        self.srv = srv
        app._ports_dlg = self
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.title("Управление фаерволом — %s" % srv["name"])
        self.txt = tk.Text(self, width=72, height=16, font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True, padx=6, pady=6)
        self._fill(rules_text)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=6)
        ttk.Label(bar, text="Порт:").pack(side="left")
        self.v_port = tk.StringVar()
        ttk.Entry(bar, textvariable=self.v_port, width=7).pack(side="left", padx=4)
        self.v_proto = tk.StringVar(value="tcp")
        ttk.Combobox(bar, textvariable=self.v_proto, width=5,
                     values=["tcp", "udp"], state="readonly").pack(side="left")
        ttk.Button(bar, text="Открыть",
                   command=lambda: self._act("allow")).pack(side="left", padx=4)
        ttk.Button(bar, text="Закрыть",
                   command=lambda: self._act("deny")).pack(side="left")
        ttk.Button(bar, text="Обновить",
                   command=self._reload).pack(side="left", padx=10)

    def _port_label(self, proto, port, cm):
        """Человеческий комментарий к открытому порту (зависит от деплоя)."""
        s = self.srv
        ssh_ports = {str(p) for p in
                     [s.get("ssh_port", 22)] + s.get("sshd_ports", [])}
        ck_port = str(s.get("ck_port")
                      or self.app.v_ckport.get().strip() or "443")
        if proto == "tcp" and port in ssh_ports:
            return "SSH — не удалять"
        if proto == "tcp" and port == ck_port:
            return "Cloak VPN — не удалять"
        if proto == "udp" and port in ("68", "546"):
            return "DHCP-клиент — не удалять"
        return self.COMMENT_RU.get(cm) or "пользовательский порт"

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
        t = "Фаервол: %s\n\nПорты, открытые снаружи:\n\n" % (fw or "?")
        if rows:
            t += "\n".join("   %-9s %s" % ("%s/%s" % (p, pr), cm)
                           for p, pr, cm in sorted(rows, key=lambda r: int(r[0])))
        else:
            t += "   (нет открытых портов)"
        t += "\n\nОстальные входящие соединения закрыты."
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
            messagebox.showerror(APP_NAME, "Порт: число 1-65535", parent=self)
            return
        proto = self.v_proto.get()
        if action == "deny":
            lbl = self._port_label(proto, port, "")
            if "не удалять" in lbl:
                if not messagebox.askyesno(
                        APP_NAME,
                        "Порт %s/%s помечен «%s».\nЗакрытие может отрезать "
                        "доступ к серверу или VPN.\n\nВсё равно закрыть?"
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
            verb = "открыт" if action == "allow" else "закрыт"
            self.app.say("fw: порт %s/%s %s, бэкенд %s%s"
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
        migrate_dirs()
        self.data = load_data()
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
        self.verbose = tk.BooleanVar(value=False)
        self._fix_ctrl_bindings()
        self._build()
        for var, key in ((self.v_mask, "mask_domain"),
                         (self.v_proto, "proto"),
                         (self.v_ckport, "ck_port")):
            var.trace_add("write",
                          lambda *a, k=key, v=var: self._opt_changed(k, v))
        self._refresh_servers()
        self.after(100, self._drain)
        self._ensure_deps()

    def _ensure_deps(self):
        """Первый запуск на чистой машине: SSH-слой и ck-client.
        plink/pscp качаем сами (одиночные exe); OpenSSH — это Windows-
        компонент, ставим через dism только из-под админа, иначе совет."""
        def work():
            self.say("=== Проверка зависимостей ===")
            openssh = [r"C:\Windows\System32\OpenSSH"]
            if find_exe(["ssh.exe", "ssh"], openssh) and \
                    find_exe(["scp.exe", "scp"], openssh):
                self.say("  OpenSSH-клиент: на месте")
            elif install_openssh(self.say):
                self.say("  OpenSSH-клиент: установлен")
            else:
                self.say("  OpenSSH-клиент: НЕТ — нужен для входа по "
                         "OpenSSH-ключу. Установка: Параметры → Приложения "
                         "→ Дополнительные компоненты → «Клиент OpenSSH», "
                         "либо используй пароль/.ppk (для них хватит PuTTY)")
            plink, pscp = find_putty()
            if plink and pscp:
                self.say("  PuTTY (plink/pscp): на месте")
            else:
                try:
                    self.say("  PuTTY (plink/pscp): нет — скачиваю с "
                             "официального сайта…")
                    download_putty(self.say)
                except Exception as e:
                    self.say("  !! plink/pscp не скачались (%s) — вход по "
                             "паролю и по .ppk не будет работать. Поставь "
                             "PuTTY или используй OpenSSH-ключ" % e)
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
                        self.say("  ck-client: нашёл %s → скопировал в "
                                 "bin\\" % found)
                    except OSError:
                        self.say("  ck-client: на месте (%s)" % found)
                else:
                    self.say("  ck-client: на месте")
            else:
                try:
                    ck = download_ck_client(self.say)
                    self.say("  ck-client → %s" % ck)
                except Exception as e:
                    self.say("  !! ck-client не скачался (%s) — вкладка "
                             "«Пользователи» не заработает; деплой — будет"
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
        if (name == self._log_name or name is None) and \
                (not verbose or self.verbose.get()):
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
        for tab, w_ in (("deploy", self.logw_dep), ("users", self.logw_usr)):
            w_.delete("1.0", "end")
            if self._log_ctx and self._log_ctx != name and self._op_tab == tab:
                w_.insert("end", "…идёт операция на «%s» — её вывод пишется "
                                 "в журнал этого сервера…\n\n" % self._log_ctx)
            entries = [l for l, vb, t in self._logs.get(name, [])
                       if t == tab and (not vb or v)]
            if entries:
                w_.insert("end", "\n".join(entries) + "\n")
            w_.see("end")
        self.logframe_dep.config(text="Лог — %s" % name)
        self.logframe_usr.config(text="Лог — %s" % name)

    def _on_verbose_toggle(self):
        s = self._srv_by_name(self._log_name) if self._log_name else None
        if s is not None:
            s["log_verbose"] = self.verbose.get()
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
        self._mark_log(tab, "Лог скопирован в буфер (%d символов)"
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
        self._mark_log(tab, "Лог очищен.")

    def _clear_log_dep(self):
        self._clear_log("deploy")

    def _set_busy(self, b):
        self.busy = b
        state = "disabled" if b else "normal"
        for btn in self._all_buttons:
            btn.config(state=state)

    def _worker(self, fn, ctx=None):
        """ctx — имя сервера, чей журнал получает лог (если операция
        привязана к серверу не по текущему выбору, а по диалогу)."""
        if self.busy:
            messagebox.showinfo(APP_NAME, "Идёт операция на «%s» — подожди"
                                % (self._log_ctx or "?"))
            return
        self.busy = True
        tab = self._cur_tab()  # вкладка-источник — читаем в главном потоке
        self.ui(lambda: self._set_busy(True))

        def run():
            self._log_ctx = ctx or self._log_name  # журнал сервера операции
            self._op_tab = tab                     # и канал её вкладки
            try:
                fn()
            except Exception as e:
                self.say("ОШИБКА: %s" % e)
            finally:
                self._log_ctx = None
                self._op_tab = None
                self.busy = False
                self.ui(lambda: self._set_busy(False))
        threading.Thread(target=run, daemon=True).start()

    # ---- layout ----
    def _build(self):
        self._all_buttons = []
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=6, pady=6)

        # --- левая колонка: серверы ---
        left = ttk.LabelFrame(top, text="Серверы")
        left.pack(side="left", fill="y", padx=(0, 6))
        self.srv_list = tk.Listbox(left, width=30, exportselection=False)
        self.srv_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.srv_list.bind("<<ListboxSelect>>", lambda e: self._on_srv_select())
        btns = ttk.Frame(left)
        btns.pack(fill="x", padx=4, pady=4)
        for t, c in (("Добавить", self._srv_add),
                     ("Изменить", self._srv_edit),
                     ("Удалить", self._srv_del)):
            b = ttk.Button(btns, text=t, command=c)
            b.pack(side="left", padx=2)
            self._all_buttons.append(b)

        # --- правая колонка: вкладки ---
        nb = self.nb = ttk.Notebook(top)
        nb.pack(side="left", fill="both", expand=True)
        self._tab_deploy(nb)
        self._tab_users(nb)

    def _mk_btn(self, parent, text, cmd):
        b = ttk.Button(parent, text=text, command=cmd)
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
        nb.add(f, text="Развёртывание")
        pad = {"padx": 6, "pady": 4}

        row = 0
        optf = ttk.Frame(f)
        optf.grid(row=row, column=0, sticky="ew", **pad)
        ttk.Label(optf, text="Домен для маскировки:").pack(side="left")
        self.v_mask = tk.StringVar(value="www.bing.com")
        ttk.Entry(optf, textvariable=self.v_mask, width=36).pack(side="left", padx=4)
        self.v_ckport = tk.StringVar(value="443")
        self.w_ckport = ttk.Entry(optf, textvariable=self.v_ckport, width=5)
        self.w_ckport.pack(side="right")
        lbl_p = ttk.Label(optf, text="Cloak порт")
        lbl_p.pack(side="right", padx=(10, 4))
        Tooltip(lbl_p, "На развёрнутом сервере поле заблокировано:\n"
                       "смена порта требует пересборки конфигов юзеров")
        self.v_tcpwarn = tk.StringVar()
        ttk.Label(optf, textvariable=self.v_tcpwarn,
                  foreground="#a33").pack(side="right", padx=(2, 0))
        self.v_proto = tk.StringVar(value="udp")
        self.v_proto.trace_add("write", lambda *a: self.v_tcpwarn.set(
            "(TCP медленнее)" if self.v_proto.get() == "tcp" else ""))
        self.w_proto = ttk.Combobox(optf, textvariable=self.v_proto, width=5,
                                    state="readonly", values=["udp", "tcp"])
        self.w_proto.pack(side="right")
        lbl_pr = ttk.Label(optf, text="Протокол OpenVPN:")
        lbl_pr.pack(side="right", padx=(10, 0))
        Tooltip(lbl_pr, "На развёрнутом сервере поле заблокировано:\n"
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
            tk.Label(c, text=t, bg="#e8e8e8", font=("Segoe UI", 9),
                     anchor="w", padx=6).pack(fill="both")

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
        big = tk.Button(bf, text="▶  Развернуть всё", command=self._step_run_all,
                        font=("", 10, "bold"), bg="#2d7", fg="white",
                        activebackground="#2a6", padx=10, pady=2)
        big.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 10))
        self._all_buttons.append(big)
        Tooltip(big, "Шаги идут сверху вниз, готовые шаги — пропускаются")
        for i, (t, c) in enumerate((("Только выбранный шаг", self._step_run_sel),
                                    ("Проверить статусы", self._steps_reset),
                                    ("Управление фаерволом", self._fw_ports)),
                                   start=1):
            b = self._mk_btn(bf, t, c)
            b.grid(row=0, column=i, sticky="ew", padx=2, pady=1)
            bf.columnconfigure(i, weight=1, uniform="btn")
        b_imp = self._mk_btn(bf, "Импорт ключей", self._do_import)
        b_imp.grid(row=0, column=4, sticky="ew", padx=(2, 0), pady=1)
        bf.columnconfigure(4, weight=1, uniform="btn")
        Tooltip(b_imp,
                "Если сервер уже настроен (вручную или через DGCloak Admin)\n"
                "— эта кнопка забирает с него ключи/юзеры Cloak,\n"
                "не переустанавливая ничего. После этого сервером можно\n"
                "управлять: юзеры, конфиги, статусы.")
        for i, (t, c) in enumerate((("Сбросить сервер", self._srv_purge),
                                    ("Перезагрузить сервер", self._srv_reboot),
                                    ("Копировать лог", self._copy_log_dep),
                                    ("Очистить лог", self._clear_log_dep)),
                                   start=1):
            b = self._mk_btn(bf, t, c)
            b.grid(row=1, column=i, sticky="ew",
                   padx=(2, 0) if i == 4 else 2, pady=1)

        row += 1
        leg = ttk.Frame(f)
        leg.grid(row=row, column=0, sticky="ew", **pad)
        ttk.Label(leg, text="✓ готово   ⚠ предупреждение   ✗ ошибка   "
                            "– пропущен   … не выполнялся",
                  foreground="#666").pack(side="left")
        # язык интерфейса — пока только элемент, перевод позже
        self.v_lang = tk.StringVar(value="Русский")
        ttk.Combobox(leg, textvariable=self.v_lang, state="disabled",
                     values=["Русский", "English"], width=9).pack(side="right")
        ttk.Label(leg, text="Язык:").pack(side="right", padx=(0, 4))

        row += 1
        self.logframe_dep = ttk.LabelFrame(f, text="Лог")
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
        nb.add(f, text="Пользователи")
        self.v_users_srv = tk.StringVar(value="— выбери сервер")
        ttk.Label(f, textvariable=self.v_users_srv,
                  foreground="#666").pack(anchor="w", padx=6, pady=(6, 0))
        cols = ("cn", "sessions", "limit", "quota", "expiry", "mask", "online")
        self.users_tv = ttk.Treeview(f, columns=cols, show="headings", height=12)
        heads = {"cn": "Имя (CN)", "sessions": "Макс. сессий",
                 "limit": "Лимит ↑/↓", "quota": "Квота ↑/↓",
                 "expiry": "Истекает", "mask": "Домен маскировки",
                 "online": "Онлайн"}
        widths = {"cn": 120, "sessions": 50, "limit": 100, "quota": 100,
                  "expiry": 90, "mask": 140, "online": 55}
        for c in cols:
            ctr = c in ("sessions", "online")
            self.users_tv.heading(c, text=heads[c],
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
            "Изменить…": "Лимиты/срок/маска выбранного юзера;\n"
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
            Tooltip(b, _user_tips[t])
        for i, (t, c) in enumerate((("Отключить сейчас", self._user_kill),
                                    ("Отозвать и удалить", self._user_revoke),
                                    ("Экспорт конфига…", self._user_export)),
                                   start=3):
            b = self._mk_btn(bf, t, c)
            b.grid(row=0, column=i, sticky="ew", padx=2)
            bf.columnconfigure(i, weight=1, uniform="ug2")
            Tooltip(b, _user_tips[t])
        cb_v = ttk.Checkbutton(bf, text="Подробный вывод",
                               variable=self.verbose,
                               command=self._on_verbose_toggle)
        cb_v.grid(row=0, column=6, sticky="e", padx=(8, 2))
        Tooltip(cb_v, "Служебные строки юзер-операций (ck-client, user-cert).\n"
                      "Состояние запоминается для каждого сервера.")

        self.logframe_usr = ttk.LabelFrame(f, text="Лог")
        self.logframe_usr.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.logw_usr = tk.Text(self.logframe_usr, height=10, wrap="none",
                                font=("Consolas", 9))
        sb = ttk.Scrollbar(self.logframe_usr, command=self.logw_usr.yview)
        self.logw_usr.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.logw_usr.pack(fill="both", expand=True, padx=4, pady=4)

    # ---- серверы ----
    def _refresh_servers(self):
        # delete+insert сносит выделение — сохраняем его, иначе
        # _sel_srv_silent() вернёт None и спиннер шага спрячется
        sel = self.srv_list.curselection()
        keep = sel[0] if sel else None
        self.srv_list.delete(0, "end")
        for s in self.data["servers"]:
            mark = " ✓" if s.get("deployed") else ""
            self.srv_list.insert("end", "%s%s" % (s["name"], mark))
        if keep is not None and keep < self.srv_list.size():
            self.srv_list.selection_set(keep)  # не генерит <<ListboxSelect>>
        self._apply_opt_lock()

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
        i = self.srv_list.curselection()
        if not i:
            messagebox.showinfo(APP_NAME, "Выбери сервер слева")
            return None
        return self.data["servers"][i[0]]

    def _on_srv_select(self):
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
        self.v_users_srv.set("%s — кэш админки, «Обновить» покажет "
                             "данные с сервера" % s["name"])

    def _sel_srv_silent(self):
        i = self.srv_list.curselection()
        return self.data["servers"][i[0]] if i else None

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
        if messagebox.askyesno(APP_NAME, "Удалить сервер «%s» из списка?\n\n"
                               "Локальные конфиги юзеров удалятся.\n"
                               "SSH-ключ остаётся в %s —\n"
                               "им можно зайти на сервер и потом.\n"
                               "Сам сервер не трогаем."
                               % (s["name"], os.path.join(APP_DIR, "keys"))):
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

    def _fill_steps(self):
        s = self._sel_srv_silent()
        st = (s or {}).get("steps", {})
        self.steps_tv.delete(*self.steps_tv.get_children())
        mark = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "–"}
        for key, title in self.STEPS:
            r = st.get(key, {})
            self.steps_tv.insert("", "end", iid=key, text=title,
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
                self.say("=== Аудит статусов на «%s» ===" % s["name"])
                out = ssh.run_script("probe.sh", timeout=120)
            except Exception:
                # probe не отработал (SSH/сеть/sudo) — реальное
                # состояние неизвестно, старой ✓ верить нельзя
                if s.get("deployed"):
                    s["deployed"] = False
                    save_data(self.data)
                    self.say("  метку «развёрнут» снял — сервер не "
                             "отвечает, состояние не проверено")
                    self.ui(self._refresh_servers)
                raise
            if self._apply_probe(s, out):
                self.ui(self._fill_steps)
                self.ui(self._refresh_servers)
        self._worker(work)

    def _apply_probe(self, s, out):
        """Разобрать вывод probe.sh → steps/deployed/reboot_required.
        Возвращает False, если probe не выдал ни одного шага."""
        known = dict(self.STEPS)
        st = {}
        for m in re.finditer(r"===STEP_(\w+)===\s*\n(\w+)\|([^\n]*)", out):
            key, stt, note = m.group(1), m.group(2), m.group(3).strip()
            if key in known:
                st[key] = {"st": stt, "note": note}
                self.say("  %s → %s: %s" % (key, stt, note))
        if not st:
            self.say("!! probe.sh не вернул данных")
            return False
        s["steps"] = st
        note_su = st.get("sysupd", {}).get("note", "")
        if "reboot" in note_su or "перезагруз" in note_su:
            s["reboot_required"] = True
        else:
            s.pop("reboot_required", None)
        s["deployed"] = st.get("cloak", {}).get("st") == "ok"
        save_data(self.data)
        return True

    def _srv_purge(self):
        """Полный сброс сервера: purge-dgcloak.sh по SSH + чистка реестра."""
        s = self._sel_srv()
        if not s:
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Полный сброс «%s»:\n\n"
                "будут удалены Cloak, OpenVPN, PKI, юзеры, наши\n"
                "правила фаервола и локальные конфиги юзеров.\n"
                "SSH-доступ и твой юзер НЕ затрагиваются —\n"
                "сервер можно развернуть заново.\n\n"
                "Продолжить?" % s["name"]):
            return

        def work():
            ssh = SSH(s, self.say)
            self.say("=== Полный сброс «%s» ===" % s["name"])
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
            self.say("=== Сброс завершён (rc=%s) ===" % rc)
        self._worker(work)

    def _srv_reboot(self):
        """Перезагрузка сервера по SSH + ожидание подъёма обратно."""
        s = self._sel_srv()
        if not s:
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Перезагрузить «%s»?\n\n"
                "Сервер будет недоступен ~1 минуту." % s["name"]):
            return

        def work():
            self.say("=== Перезагрузка «%s» ===" % s["name"])
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
            self.say("  …выполняется уже %d мин %d с — процесс жив, "
                     "ждём ответа сервера" % (el // 60, int(el) % 60))
        self.after(125, self._spin_tick)

    def _run_step(self, s, key):
        title = dict(self.STEPS)[key]
        self.say("=== Шаг: %s ===" % title)
        st = s.setdefault("steps", {})
        self.ui(self._spin_start, key, s["name"])
        try:
            ssh = SSH(s, self.say)
            stt, note = getattr(self, "_step_" + key)(ssh, s)
        except Exception as e:
            stt, note = "fail", (str(e).splitlines() or ["?"])[-1][:140]
            self.say("  ОШИБКА: %s" % e)
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
            messagebox.showinfo(APP_NAME, "Выбери шаг в таблице")
            return
        key = sel[0]
        self._worker(lambda: self._run_step(s, key))

    def _step_run_all(self):
        s = self._sel_srv()
        if not s:
            return

        def work():
            # реестр может врать (VM откачена на снапшот, сервер переставлен):
            # перед проходом сверяем реальное состояние — probe дешёвый (~1 с),
            # а пропуск «готовых» шагов на пустом сервере — тупик
            if any(v.get("st") == "ok" for v in s.get("steps", {}).values()):
                try:
                    ssh = SSH(s, self.say)
                    ssh.preflight()
                    self.say("Сверяю статусы с сервером…")
                    if self._apply_probe(s, ssh.run_script("probe.sh", timeout=120)):
                        self.ui(self._fill_steps)
                        self.ui(self._refresh_servers)
                except Exception as e:
                    # preflight упал (нет sudo и т.п.) — шаг 1 скажет то же
                    # самое внятно; но готовые шаги доверять нельзя
                    self.say("  probe не прошёл (%s) — иду с первого шага"
                             % str(e).splitlines()[0][:80])
                    s["steps"] = {}
            for key, _t in self.STEPS:
                if s.get("steps", {}).get(key, {}).get("st") == "ok":
                    continue
                if not self._run_step(s, key):
                    self.say("Остановился на шаге «%s». Исправь и продолжай — "
                             "завершённые шаги не повторятся."
                             % dict(self.STEPS)[key])
                    break
            self.say("=== Проход завершён ===")
            if s.get("reboot_required"):
                if self.ask(APP_NAME,
                            "Обновление системы на «%s» требует перезагрузки.\n"
                            "Перезагрузить сервер сейчас?\n\n"
                            "(поднимется через ~1 минуту; завершённые шаги "
                            "деплоя повторять не нужно)" % s["name"]):
                    self._reboot_and_wait(s)
        self._worker(work)

    def _reboot_and_wait(self, s):
        """Перезагрузка через sudo + ожидание подъёма. Статус «нужен reboot»
        снимается только после реального подъёма — иначе лог врёт."""
        ssh = SSH(s, self.say)
        ssh.preflight()
        boot0 = ssh.run("uptime -s", timeout=15).strip()
        self.say("  отправляю reboot…")
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
                                "note": "обновлено, перезагружен"}
            save_data(self.data)
            self.ui(self._fill_steps)
            self.say("  сервер поднялся после перезагрузки")
        else:
            self.say("  !! сервер не перезагрузился за 3 минуты "
                     "(или флаг reboot-required остался) — проверь консоль VM")
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
                return "ok", "вход по ключу настроен"
            except Exception:
                self.say("  сохранённый ключ отвергнут — ставлю новый")
                s.pop("key", None); s.pop("ppk", None)
        if not s.get("password"):
            return "fail", "нет ни пароля, ни ключа"
        # ключ ставим без вопросов: операция безопасна,
        # пароль остаётся запасным входом
        self.say("  генерирую ключ ed25519…")
        kg = find_exe(["ssh-keygen.exe", "ssh-keygen"],
                      [r"C:\Windows\System32\OpenSSH"])
        if not kg:
            raise SSHErr("не найден ssh-keygen (Windows OpenSSH)")
        kd = os.path.join(APP_DIR, "keys")
        os.makedirs(kd, exist_ok=True)
        # наш старый сгенерированный ключ (имя от старого названия
        # сервера) — после успешной установки нового сносим
        kp = os.path.join(kd, "%s_ed25519" % re.sub(r"[^\w-]", "_", s["name"]))
        if not os.path.isfile(kp):
            r = subprocess.run([kg, "-t", "ed25519", "-N", "", "-f", kp],
                               capture_output=True, text=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                raise SSHErr("ssh-keygen: %s" % (r.stderr or r.stdout))
        pub = open(kp + ".pub", encoding="ascii").read().strip()
        self.say("  ставлю публичный ключ на сервер…")
        ssh.install_pubkey(pub)
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
            self.say("  старый ключ %s удалён"
                     % os.path.basename(old_key))
        return "ok", "ключ установлен: %s" % os.path.basename(kp)

    def _step_audit(self, ssh, s):
        out = ssh.run_script("detect.sh", timeout=60)
        for ln in out.splitlines():
            self.say("  " + ln)
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
            return "fail", "не apt-дистрибутив (pkg=%s) — не поддерживается" % pkg
        os_id = osid.group(1) if osid else "?"
        os_ver = ver.group(1) if ver else "?"
        supported = {"ubuntu": {"20.04", "22.04", "24.04", "26.04"},
                     "debian": {"11", "12", "13"}}
        if os_id not in supported or os_ver not in supported[os_id]:
            if not self.ask(
                    APP_NAME,
                    "«%s»: %s %s не из поддерживаемых\n"
                    "(Ubuntu 20.04/22.04/24.04/26.04, Debian 11/12/13).\n\n"
                    "Продолжить на свой страх и риск?"
                    % (s["name"], os_id, os_ver)):
                return "fail", "%s %s не поддерживается" % (os_id, os_ver)
        res = []
        if s.get("disk_free_mb", 9999) < 400:
            res.append("мало места на диске: %d МБ" % s["disk_free_mb"])
        if s.get("mem_avail_mb", 9999) < 128:
            res.append("мало RAM: %d МБ свободно" % s["mem_avail_mb"])
        txt = "%s %s / %s" % (os_id, os_ver, arch)
        if res:
            return "warn", "%s; %s" % (txt, "; ".join(res))
        return "ok", txt

    def _step_fw(self, ssh, s):
        out = ssh.run_script("fw-detect.sh", timeout=60)
        m = re.search(r"FW=(\S+)", parse_section(out, "FW_BACKEND"))
        fw = m.group(1) if m else "none"
        s["fw_backend"] = fw
        for ln in ("--- правила ---\n" + parse_section(out, "FW_RULES")
                   + "\n--- слушают снаружи (tcp) ---\n"
                   + parse_section(out, "LISTEN_TCP")).splitlines():
            self.say("  " + ln)
        if fw == "none":
            ports = sorted({str(s.get("ssh_port", 22))}
                           | set(s.get("sshd_ports", [])), key=int)
            ck = self.v_ckport.get().strip() or "443"
            # ставим nftables без подтверждения: это суть деплоя,
            # anti-lockout canary откатит правила при потере SSH
            self.say("  фаервола нет — ставлю nftables "
                     "(INPUT DROP + ssh %s + cloak tcp/%s; "
                     "откат через 120 с при потере SSH)"
                     % (",".join(ports), ck))
            ssh.run_script("fw-install.sh", "%s %s" % (",".join(ports), ck),
                           timeout=300)
            # canary: на сервере 120с откат; новое ssh-подключение подтверждает
            ssh.run("touch /tmp/dgcloak-fw-ok", timeout=30)
            self.say("  firewall подтверждён (rollback отменён)")
            s["fw_backend"] = "nftables-dg"
            s["ck_port"] = ck
            return "ok", "nftables: ssh=%s cloak=%s" % (",".join(ports), ck)
        if fw == "iptables-custom":
            return "warn", "чужие правила iptables — открой «Управление фаерволом»"
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
                "На «%s» свободно %d МБ на диске.\n"
                "Обновлению может не хватить места (нужно ~1 ГБ).\n\n"
                "Продолжить?" % (s["name"], free)):
            return "skip", "мало места на диске (%d МБ)" % free
        if not self.ask(APP_NAME,
                        "Полное обновление системы на «%s» "
                        "(apt update + full-upgrade + autoremove)?\n\n"
                        "На свежеустановленной системе это может занять\n"
                        "10–30 минут — прогресс виден в логе.\n"
                        "Если обновление потребует перезагрузку,\n"
                        "приложение предложит её в конце." % s["name"]):
            # отказались от апгрейда — но pending reboot мог остаться
            # с прошлого прогона; тогда статус должен остаться «нужен reboot»
            if "REBOOT" in ssh.run("test -f /var/run/reboot-required "
                                   "&& echo REBOOT || true", timeout=15):
                s["reboot_required"] = True
                save_data(self.data)
                return "warn", "обновлено ранее, нужна перезагрузка"
            return "skip", "отменено пользователем"
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
            return "warn", "обновлено, нужна перезагрузка"
        s.pop("reboot_required", None)
        return "ok", "обновлено"

    def _step_pkgs(self, ssh, s):
        out = ssh.run_script("pkgs.sh", "check", timeout=90)
        for ln in out.splitlines():
            self.say("  " + ln)
        missing = [l.split("=")[0] for l in
                   parse_section(out, "PKGS").splitlines()
                   if l.strip().endswith("=-")]
        latest = parse_section(out, "CK_LATEST").strip()
        if missing:
            self.say("  ставлю недостающие пакеты: %s" % ", ".join(missing))
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
            return "fail", "не встали: %s" % ",".join(missing)
        return "ok", "все пакеты есть; cloak latest: %s" % (latest or "?")

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
            ssh.run_script("deploy-net-ufw.sh", timeout=300)
        elif fw in ("nftables-dg", "nftables"):
            ssh.run_script("nat-enable.sh", timeout=120)
        elif fw == "firewalld":
            ssh.run("%sbash -c 'firewall-cmd --permanent --add-masquerade "
                    "--zone=public && firewall-cmd --reload && "
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
            self.say("  firewall подтверждён (rollback отменён)")
        elif fw == "none":
            return "fail", "фаервола нет — сначала выполни шаг «Фаервол»"
        else:
            return "fail", "неизвестный фаервол: %s" % fw
        # порт Cloak должен быть открыт снаружи на любом бэкенде
        try:
            ssh.run_script("fw-manage.sh", "allow tcp %s" % ck, timeout=60)
            s["ck_port"] = ck
        except Exception as e:
            return "warn", ("NAT ok (%s), но порт Cloak %s/tcp не открылся: %s"
                            % (fw, ck, str(e)[:80]))
        return "ok", "NAT через %s, Cloak tcp/%s открыт" % (fw, ck)

    def _step_cloak(self, ssh, s):
        mask = self.v_mask.get().strip() or "www.bing.com"
        proto = self.v_proto.get()
        ck = str(s.get("ck_port") or self.v_ckport.get().strip() or "443")
        # sudo: без него ss -p прячет имена чужих процессов → ложный "занят"
        busy = ssh.run("%sss -tlnp | grep ':%s ' || echo free"
                       % (ssh.sudo, ck), timeout=20)
        if "free" not in busy and "ck-server" not in busy:
            if s.get("has_docker") or "docker" in busy.lower():
                if not self.ask(APP_NAME,
                                "Порт %s занят (Amnezia/docker).\n"
                                "Снести Amnezia? Чужие контейнеры не трогаем."
                                % ck):
                    return "fail", "%s занят, чистка отменена" % ck
                ssh.run_script("purge-amnezia.sh", timeout=600)
            else:
                return "fail", "%s занят чужим сервисом: %s" % (
                    ck, busy.strip()[:100])
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
            return "ok", "ключи получены, маскировка %s" % mask
        return "fail", "нет PUB/ADMIN_UID в выводе deploy-cloak"

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
                self.say("=== Аудит статусов на «%s» ===" % s["name"])
                out = ssh.run_script("probe.sh", timeout=120)
                if self._apply_probe(s, out):
                    self.ui(self._fill_steps)
                    self.ui(self._refresh_servers)
                if not s.get("deployed"):
                    msg = ("Сервер «%s» не развёрнут — ключей нет.\n"
                           "Сначала «Развернуть всё»." % s["name"])
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
                self.say("Импорт: ключи подтянуты с «%s»%s"
                         % (s["name"], ", юзеров восстановлено: %d"
                            % len(added) if added else ""))
                # бандлы для восстановленных: сертификаты живут на
                # сервере — пересобираем .dgcloak, чтобы bundles\
                # не оставался пустым после переезда
                for rec in added:
                    try:
                        mats = parse_cert_bundle(
                            ssh.run_script("user-cert.sh", rec["cn"],
                                           timeout=60))
                        write_user_bundle(
                            s, rec["cn"], rec["uid"], mats,
                            os.path.join(BUNDLES_DIR, s["name"], rec["cn"]),
                            rec.get("mask") or "")
                        self.say("  конфиг «%s» → bundles\\%s"
                                 % (rec["cn"], s["name"]))
                    except Exception as e:
                        self.say("  !! конфиг «%s» не пересобран: %s"
                                 % (rec["cn"], e))
                self.ui(lambda: self._fill_users_local(s))
            else:
                self.say("!! Не нашёл /etc/ck-server/* — сервер развёрнут?")
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
            self.vsay("  реестр юзеров синхронизирован на сервер")
        except Exception as e:
            self.say("  !! реестр на сервер не записался: %s" % e)

    def _pull_users(self, ssh, s):
        """Забрать реестр с сервера и влить в локальный (по CN; локальная
        запись выигрывает — она свежее, если вдруг разошлись)."""
        try:
            out = ssh.run("%scat %s 2>/dev/null || echo '[]'"
                          % (ssh.sudo, self.REMOTE_USERS), timeout=20)
            remote = json.loads(out.strip() or "[]")
        except Exception as e:
            self.say("  реестр юзеров с сервера не прочитан: %s" % e)
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
            raise CloakAPIErr("Сервер «%s» не развёрнут (по реестру) — "
                              "сделай «Развернуть всё» или «Проверить статусы»"
                              % s["name"])
        ck = find_ck_client(self.data)
        if not ck:
            try:
                ck = download_ck_client(self.say)
            except Exception as e:
                raise CloakAPIErr(
                    "Не найден ck-client.exe и не скачался (%s) — "
                    "укажи путь в data.json или положи рядом" % e)
            self.say("  ck-client.exe → %s" % ck)
        if not s.get("admin_uid") or not s.get("pubkey"):
            raise CloakAPIErr("Нет admin_uid/pubkey — сделай «Импорт ключей» "
                              "или деплой")
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
                out = ssh.run("%spython3 /tmp/ovpn-mgmt.py clients" % ssh.sudo,
                              timeout=20)
                online = {l.strip() for l in out.splitlines() if l.strip()}
            except Exception as e:
                self.say("  mgmt не отвечает (%s) — включаю enable-mgmt…"
                         % str(e).splitlines()[-1][:120])
                try:
                    ssh.run_script("enable-mgmt.sh", timeout=60)
                    out = ssh.run("%spython3 /tmp/ovpn-mgmt.py clients"
                                  % ssh.sudo, timeout=20)
                    online = {l.strip() for l in out.splitlines() if l.strip()}
                    self.say("  mgmt включён")
                except Exception as e2:
                    self.say("  mgmt так и недоступен: %s" % e2)
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
                self.v_users_srv.set("%s — актуальные данные с сервера"
                                     % s["name"])
            self.ui(fill)
            self.say("Юзеров: %s, онлайн: %s" % (len(rows), len(online)))
        self._worker(work)

    def _selected_user(self):
        sel = self.users_tv.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Выбери юзера в таблице")
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
        self.vsay("  вывод %d байт" % len(out))
        mats = parse_cert_bundle(out)
        if not all(mats.values()):
            tail = "\n".join(out.strip().splitlines()[-6:])
            raise SSHErr("user-cert.sh: неполный вывод (нет CA/CERT/KEY/TA).\n"
                         "Хвост вывода: %s" % tail)
        # 2. UID через admin-API
        api = self._api(s)
        api.start()
        try:
            uid = api.create_user(sessions_cap=sessions, expiry=expiry,
                                  up_rate=up_rate, down_rate=down_rate,
                                  up_credit=up_credit, down_credit=down_credit)
        finally:
            api.stop()
        # 3. конфиг
        bundle = os.path.join(BUNDLES_DIR, s["name"], name)
        write_user_bundle(s, name, uid, mats, bundle, mask)
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
        self.say("Юзер «%s» создан, выдай конфиг юзеру: %s"
                 % (name, os.path.join(bundle, "%s.dgcloak" % name)))
        return bundle

    def _user_create(self):
        s = self._sel_srv()
        if not s:
            return
        d = UserDialog(self, srv_mask=s.get("mask_domain", "www.bing.com"),
                       title="Новый юзер")
        if not d.result:
            return
        r = d.result
        # дубль CN = перезапись чужого сертификата — запрещаем
        if any(u.get("cn") == r["name"] for u in s.get("users", [])):
            messagebox.showerror(APP_NAME,
                                 "Юзер «%s» уже существует на «%s»"
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
            messagebox.showinfo(APP_NAME, "«%s» не из нашего реестра — "
                                "править можем только своих" % cn)
            return
        d = UserDialog(self, title="Изменить «%s»" % cn,
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
                bundle = os.path.join(BUNDLES_DIR, s["name"], cn)
                write_user_bundle(s, cn, uid, mats, bundle, r["mask"])
                self.say("Юзер «%s» обновлён, конфиг перевыпущен, "
                         "выдай его юзеру: %s"
                         % (cn, os.path.join(bundle, "%s.dgcloak" % cn)))
            elif api_changed:
                self.say("Юзер «%s» обновлён (лимиты/срок — на сервере, "
                         "конфиг тот же)" % cn)
            else:
                self.say("Юзер «%s» без изменений" % cn)
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
                    "UID %s…\nне из реестра админки — CN неизвестен, сертификат "
                    "и живую сессию трогать не можем.\n\n"
                    "Удалить UID из Cloak? (новые подключения закроются)" % uid[:16]):
                return

            def work_uid():
                api = self._api(s)
                api.start()
                try:
                    api.delete_user(uid)
                finally:
                    api.stop()
                self.say("UID %s… удалён из Cloak" % uid[:16])
                self.ui(self._users_refresh)
            self._worker(work_uid)
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Отозвать «%s»?\n\nСертификат отзовётся (CRL), UID удалится,\n"
                "живая сессия будет сброшена." % cn):
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
                self.say("  сертификат отозван")
            except SSHErr as e:
                msg = str(e)
                if "test -s" in msg or "not a valid certificate" in msg \
                        or msg.rstrip().endswith("rc=1:") or "(пустой вывод)" in msg:
                    self.say("  сертификата на сервере нет — пропускаю отзыв")
                elif "already revoked" in msg.lower():
                    self.say("  сертификат уже отозван")
                else:
                    raise
            # 2. delete UID
            if rec and rec.get("uid"):
                try:
                    api = self._api(s)
                    api.start()
                    api.delete_user(rec["uid"])
                    api.stop()
                    self.say("  UID удалён")
                except Exception as e:
                    if "bucket not found" in str(e) or "404" in str(e):
                        self.say("  UID в Cloak уже нет")
                    else:
                        self.say("  UID: %s" % e)
            # 3. kill live session
            try:
                ssh.upload(os.path.join(SCRIPTS_DIR, "ovpn-mgmt.py"),
                           "/tmp/ovpn-mgmt.py")
                out = ssh.run("%spython3 /tmp/ovpn-mgmt.py kill %s"
                              % (ssh.sudo, cn), timeout=20)
                # не просто SUCCESS — его mgmt шлёт и на пароль
                if "SUCCESS: common name" in out:
                    self.say("  сессия сброшена")
                elif "not found" in out:
                    self.say("  сессии не было (юзер офлайн)")
                else:
                    self.say("  mgmt: %s" % out.strip()[:200])
            except Exception as e:
                # чаще всего mgmt просто не поднят (сервер чист) —
                # traceback не нужен, хватит последней строки
                self.say("  mgmt недоступен: %s"
                         % str(e).strip().splitlines()[-1][:160])
            s["users"] = [u for u in s.get("users", []) if u["cn"] != cn]
            save_data(self.data)
            self._push_users(ssh, s)
            shutil.rmtree(os.path.join(BUNDLES_DIR, s["name"], cn),
                          ignore_errors=True)
            self.say("Юзер «%s» отозван и удалён." % cn)
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
            messagebox.showinfo(APP_NAME, "CN этого юзера неизвестен — "
                                "mgmt kill работает только по CN.")
            return

        def work():
            ssh = SSH(s, self.say)
            ssh.upload(os.path.join(SCRIPTS_DIR, "ovpn-mgmt.py"),
                       "/tmp/ovpn-mgmt.py")
            online = ssh.run("%spython3 /tmp/ovpn-mgmt.py clients"
                             % ssh.sudo, timeout=20)
            if cn not in {l.strip() for l in online.splitlines()}:
                self.say("«%s» не онлайн — сбрасывать нечего" % cn)
                return
            out = ssh.run("%spython3 /tmp/ovpn-mgmt.py kill %s"
                          % (ssh.sudo, cn), timeout=20)
            if "SUCCESS: common name" in out:
                self.say("Сессия «%s» сброшена" % cn)
            else:
                self.say("kill %s: %s" % (cn, out.strip()[:200]))
        self._worker(work)

    def _user_export(self):
        s = self._sel_srv()
        if not s:
            return
        cn, rec, _uid = self._selected_user()
        if cn is None:
            return
        if not rec:
            messagebox.showinfo(APP_NAME, "«%s» заведён вне этой админки — "
                                "экспорт конфига недоступен" % cn)
            return
        dst = filedialog.askdirectory(title="Куда сложить конфиг «%s»" % cn)
        if not dst:
            return

        def work():
            ssh = SSH(s, self.say)
            out = ssh.run_script("user-cert.sh", cn, timeout=60)
            mats = parse_cert_bundle(out)
            bundle = os.path.join(dst, cn)
            write_user_bundle(s, cn, rec["uid"], mats, bundle,
                              rec.get("mask") or "")
            # каноническая копия в %APPDATA% — у восстановленных
            # импортом юзеров её может не быть вовсе
            write_user_bundle(s, cn, rec["uid"], mats,
                              os.path.join(BUNDLES_DIR, s["name"], cn),
                              rec.get("mask") or "")
            self.say("Конфиг «%s» → %s"
                     % (cn, os.path.join(bundle, "%s.dgcloak" % cn)))
        self._worker(work)


def single_instance_ok():
    """Второй экземпляр админки не запускаем (mutex, Windows)."""
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local\\DGCloakAdminSingleton")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(
                0, "DGCloak Admin уже запущен.", APP_NAME, 0x40)
            return False
    except Exception:
        pass  # не Windows или нет ctypes — не блокируем
    return True


if __name__ == "__main__":
    if single_instance_ok():
        App().mainloop()
