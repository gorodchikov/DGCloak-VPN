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
  - экспорт клиентских бандлов (.ovpn + ckclient-*.json).

SSH-слой — внешние plink/pscp (ppk нативно). Юзеры Cloak — через локальный
`ck-client.exe -a` (admin-API), без SSH.
"""

import base64
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_NAME = "DGCloak Admin"
APP_DIR = os.path.join(os.environ.get("APPDATA", "."), "DGCloakAdmin")
DATA_FILE = os.path.join(APP_DIR, "data.json")
BUNDLES_DIR = os.path.join(APP_DIR, "bundles")

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
    plink = find_exe(["plink.exe", "plink"],
                     [r"C:\Program Files\PuTTY", r"C:\Program Files (x86)\PuTTY"])
    pscp = find_exe(["pscp.exe", "pscp"],
                    [r"C:\Program Files\PuTTY", r"C:\Program Files (x86)\PuTTY"])
    return plink, pscp


def find_ck_client(data):
    cands = [data.get("ck_client") or "",
             os.path.join(APP_DIR, "ck-client.exe"),
             os.path.join(os.environ.get("APPDATA", "."), "DGCloakVPN", "ck-client.exe")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return find_exe(["ck-client.exe", "ck-client-windows-amd64.exe", "ck-client"],
                    [BASE_DIR])


def valid_cn(name):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", name or ""))


def uid_to_b64url(uid):
    return uid.replace("+", "-").replace("/", "_")


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
        # -n: без пароля → сразу ошибка, а не подвисший промпт
        self.sudo = "" if srv.get("user") == "root" else "sudo -n "
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

    def _argv(self, cmd):
        if self.backend == "openssh":
            return [self.ssh_exe, "-n", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=15",
                    "-i", self.srv["key"], "-p", str(self.srv.get("ssh_port", 22)),
                    self._target(), cmd]
        a = [self.plink, "-batch"]
        if self.srv.get("ppk"):
            a += ["-i", self.srv["ppk"]]
        elif self.srv.get("password"):
            a += ["-pw", self.srv["password"]]
        a += ["-P", str(self.srv.get("ssh_port", 22)), self._target(), cmd]
        return a

    def _argv_upload(self, local_path, remote_path):
        if self.backend == "openssh":
            return [self.scp_exe, "-B", "-o", "StrictHostKeyChecking=accept-new",
                    "-i", self.srv["key"], "-P", str(self.srv.get("ssh_port", 22)),
                    local_path, "%s:%s" % (self._target(), remote_path)]
        a = [self.pscp, "-batch"]
        if self.srv.get("ppk"):
            a += ["-i", self.srv["ppk"]]
        elif self.srv.get("password"):
            a += ["-pw", self.srv["password"]]
        a += ["-P", str(self.srv.get("ssh_port", 22)),
              local_path, "%s:%s" % (self._target(), remote_path)]
        return a

    def _spawn(self, args, input_text=None, timeout=180):
        """Запуск. Для plink — авто-принятие host key (TOFU): сначала -batch,
        при 'host key is not cached' — повтор с ответом 'y'."""
        # stdin=PIPE обязателен: в --noconsole exe нет консольного stdin,
        # наследование битого хэндла роняет ssh/scp молча с пустым выводом
        p = subprocess.run(args, input=input_text or "", capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        out = (p.stdout or "") + (p.stderr or "")
        if (self.backend == "putty" and p.returncode != 0
                and "not cached" in out):
            args2 = [a for a in args if a != "-batch"]
            p = subprocess.run(args2,
                               input=(input_text or "") + "\ny\n",
                               capture_output=True, text=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
            out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out

    def run(self, cmd, timeout=120):
        args = self._argv(cmd)
        rc, out = self._spawn(args, timeout=timeout)
        if rc != 0:
            raise SSHErr("SSH rc=%s: %s" % (rc, out.strip()[:400] or "(пустой вывод)"))
        return out

    def preflight(self):
        """Быстрый гейт перед деплоем: SSH жив + sudo доступен.
        Бросает SSHErr с понятным текстом."""
        out = self.run("echo PF:$(id -u):$("
                       "sudo -n true 2>/dev/null && echo np || echo nop)",
                       timeout=30)
        if "PF:0:" in out or ":np" in out:
            return
        raise SSHErr("sudo требует пароль — деплой повиснет.\n"
                     "Дай юзеру NOPASSWD (visudo: user ALL=(ALL) NOPASSWD:ALL)\n"
                     "или логинься как root.")

    def run_stream(self, cmd, on_line, timeout=None):
        """Стриминг stdout+stderr построчно (для долгих деплой-скриптов)."""
        args = self._argv(cmd)
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
        t0 = time.time()
        assert p.stdout is not None
        for line in p.stdout:
            on_line(line.rstrip("\n"))
            if timeout and time.time() - t0 > timeout:
                p.kill()
                raise SSHErr("Таймаут %s с: %s" % (timeout, cmd[:80]))
        p.wait(timeout=10)
        out_tail = ""
        if self.backend == "putty" and p.returncode == 255:
            # возможно host key не принят — повтор без -batch с 'y'
            args2 = [a for a in args if a != "-batch"]
            p2 = subprocess.Popen(args2, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace",
                                  stdin=subprocess.PIPE,
                                  creationflags=CREATE_NO_WINDOW)
            try:
                out, _ = p2.communicate(input="y\n", timeout=timeout or 300)
            except subprocess.TimeoutExpired:
                p2.kill()
                raise SSHErr("Таймаут: %s" % cmd[:80])
            for line in (out or "").splitlines():
                on_line(line)
            out_tail = out or ""
        if self.backend == "putty" and p.returncode == 255 and not out_tail:
            raise SSHErr("plink exit 255")
        return p.returncode

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

    def __init__(self, ck_exe, srv, log):
        self.ck = ck_exe
        self.srv = srv
        self.log = log
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
            "RemotePort": "443",
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
            self.log("  ck-client: %s" % line)
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
                    up_credit=INT64_MAX, down_credit=INT64_MAX):
        uid = base64.b64encode(secrets.token_bytes(16)).decode()
        body = {"UID": uid, "SessionsCap": sessions_cap,
                "UpRate": up_rate, "DownRate": down_rate,
                "UpCredit": up_credit, "DownCredit": down_credit,
                "ExpiryTime": expiry}
        code, r = self._req("POST", "/admin/users/" + uid_to_b64url(uid), body)
        if code not in (200, 201):
            raise CloakAPIErr("POST user → %s: %s" % (code, r))
        return uid

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


def make_ckclient(srv, uid):
    return {
        "Transport": "direct",
        "ProxyMethod": "openvpn",
        "EncryptionMethod": "plain",
        "UID": uid,
        "PublicKey": srv["pubkey"],
        "ServerName": srv.get("mask_domain", "www.bing.com"),
        "NumConn": 16,
        "BrowserSig": "firefox",
        "StreamTimeout": 300,
        "RemoteHost": srv["host"],
        "RemotePort": "443",
        "LocalHost": "127.0.0.1",
        "LocalPort": "1984",
        "UDP": srv.get("proto", "udp") == "udp",
    }


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
            ("name", "Имя", self.srv.get("name", "")),
            ("host", "Хост (DNS/IP)", self.srv.get("host", "")),
            ("ssh_port", "SSH порт", str(self.srv.get("ssh_port", 22))),
            ("user", "SSH логин", self.srv.get("user", "ubuntu")),
            ("ppk", "Ключ .ppk (PuTTY)", self.srv.get("ppk", "")),
            ("key", "Ключ OpenSSH (-i)", self.srv.get("key", "")),
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
    """Диалог создания юзера."""

    def body(self, f):
        self.vars = {}
        fields = [("name", "Имя (CN, [a-z0-9_-])", ""),
                  ("expiry_days", "Срок жизни, дней (0 = бессрочно)", "0"),
                  ("sessions", "Макс. сессий", "16")]
        for i, (k, label, val) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", padx=4, pady=3)
            v = tk.StringVar(value=val)
            self.vars[k] = v
            ttk.Entry(f, textvariable=v, width=30).grid(row=i, column=1, padx=4, pady=3)
        return f

    def validate(self):
        if not valid_cn(self.vars["name"].get().strip()):
            messagebox.showerror("Юзер", "Имя: только латиница, цифры, _ и -",
                                 parent=self)
            return False
        return True

    def apply(self):
        days = int(self.vars["expiry_days"].get() or 0)
        self.result = {
            "name": self.vars["name"].get().strip(),
            "expiry": FAR_FUTURE if days <= 0 else int(time.time()) + days * 86400,
            "sessions": max(1, int(self.vars["sessions"].get() or 16)),
        }


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x640")
        self.data = load_data()
        self.uiq = queue.Queue()
        self.busy = False
        self._build()
        self._refresh_servers()
        self.after(100, self._drain)

    # ---- UI plumbing (как в клиенте) ----
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

    def say(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        def w():
            self.logw.insert("end", line + "\n")
            self.logw.see("end")
        self.ui(w)

    def _set_busy(self, b):
        self.busy = b
        state = "disabled" if b else "normal"
        for btn in self._all_buttons:
            btn.config(state=state)

    def _worker(self, fn):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Идёт другая операция — подожди")
            return
        self.busy = True
        self.ui(lambda: self._set_busy(True))

        def run():
            try:
                fn()
            except Exception as e:
                self.say("ОШИБКА: %s" % e)
            finally:
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
        nb = ttk.Notebook(top)
        nb.pack(side="left", fill="both", expand=True)
        self._tab_deploy(nb)
        self._tab_users(nb)

        # --- лог ---
        bot = ttk.LabelFrame(self, text="Лог")
        bot.pack(fill="both", padx=6, pady=(0, 6))
        self.logw = tk.Text(bot, height=12, wrap="none",
                            font=("Consolas", 9))
        sb = ttk.Scrollbar(bot, command=self.logw.yview)
        self.logw.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.logw.pack(fill="both", padx=4, pady=4)

    def _mk_btn(self, parent, text, cmd):
        b = ttk.Button(parent, text=text, command=cmd)
        self._all_buttons.append(b)
        return b

    # ---- вкладка «Развёртывание» ----
    def _tab_deploy(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="Развёртывание")
        pad = {"padx": 6, "pady": 4}

        row = 0
        ttk.Label(f, text="Домен маскировки (RedirAddr/ServerName):").grid(
            row=row, column=0, sticky="w", **pad)
        self.v_mask = tk.StringVar(value="www.bing.com")
        ttk.Entry(f, textvariable=self.v_mask, width=28).grid(row=row, column=1, **pad)

        row += 1
        ttk.Label(f, text="Протокол OpenVPN↔Cloak:").grid(row=row, column=0, sticky="w", **pad)
        self.v_proto = tk.StringVar(value="udp")
        fr = ttk.Frame(f)
        fr.grid(row=row, column=1, sticky="w")
        ttk.Radiobutton(fr, text="UDP (быстрее)", value="udp",
                        variable=self.v_proto).pack(side="left")
        ttk.Radiobutton(fr, text="TCP", value="tcp",
                        variable=self.v_proto).pack(side="left")

        row += 1
        self.v_purge = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Сначала снести Amnezia (чужой docker не трогаем)",
                        variable=self.v_purge).grid(row=row, column=0,
                                                  columnspan=2, sticky="w", **pad)

        row += 1
        self.v_mss = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="MSS-clamp 800 (лечение PMTUD-blackhole; только по симптому!)",
                        variable=self.v_mss).grid(row=row, column=0,
                                                  columnspan=2, sticky="w", **pad)

        row += 1
        self.v_first_user = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Создать юзера после деплоя:",
                        variable=self.v_first_user).grid(row=row, column=0, sticky="w", **pad)
        self.v_first_name = tk.StringVar(value="user1")
        ttk.Entry(f, textvariable=self.v_first_name, width=20).grid(row=row, column=1,
                                                                  sticky="w", **pad)

        row += 1
        bf = ttk.Frame(f)
        bf.grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        self._mk_btn(bf, "Аудит сервера", self._do_audit).pack(side="left", padx=2)
        self._mk_btn(bf, "Развернуть", self._do_deploy).pack(side="left", padx=2)
        self._mk_btn(bf, "Подтянуть ключи (импорт)", self._do_import).pack(side="left", padx=2)

        row += 1
        ttk.Label(f, text="Деплой НЕ трогает SSH-порт. Перед развёртыванием\n"
                          "убедись, что SSH доступен по указанному порту.",
                  foreground="#666").grid(row=row, column=0, columnspan=2,
                                          sticky="w", **pad)

    # ---- вкладка «Пользователи» ----
    def _tab_users(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="Пользователи")
        self.v_users_srv = tk.StringVar(value="—")
        ttk.Label(f, textvariable=self.v_users_srv,
                  foreground="#666").pack(anchor="w", padx=6, pady=(6, 0))
        cols = ("cn", "uid", "sessions", "expiry", "online")
        self.users_tv = ttk.Treeview(f, columns=cols, show="headings", height=12)
        heads = {"cn": "Имя (CN)", "uid": "UID", "sessions": "Сессий",
                 "expiry": "Истекает", "online": "Онлайн"}
        widths = {"cn": 120, "uid": 180, "sessions": 60, "expiry": 110, "online": 60}
        for c in cols:
            self.users_tv.heading(c, text=heads[c])
            self.users_tv.column(c, width=widths[c])
        self.users_tv.pack(fill="both", expand=True, padx=6, pady=6)

        bf = ttk.Frame(f)
        bf.pack(fill="x", padx=6, pady=4)
        self._mk_btn(bf, "Обновить", self._users_refresh).pack(side="left", padx=2)
        self._mk_btn(bf, "Создать…", self._user_create).pack(side="left", padx=2)
        self._mk_btn(bf, "Отключить сейчас", self._user_kill).pack(side="left", padx=2)
        self._mk_btn(bf, "Отозвать и удалить", self._user_revoke).pack(side="left", padx=2)
        self._mk_btn(bf, "Экспорт бандла…", self._user_export).pack(side="left", padx=2)

    # ---- серверы ----
    def _refresh_servers(self):
        self.srv_list.delete(0, "end")
        for s in self.data["servers"]:
            mark = " ✓" if s.get("deployed") else ""
            self.srv_list.insert("end", "%s%s" % (s["name"], mark))

    def _sel_srv(self):
        i = self.srv_list.curselection()
        if not i:
            messagebox.showinfo(APP_NAME, "Выбери сервер слева")
            return None
        return self.data["servers"][i[0]]

    def _on_srv_select(self):
        s = self._sel_srv_silent()
        if s:
            self.v_mask.set(s.get("mask_domain", "www.bing.com"))
            self.v_proto.set(s.get("proto", "udp"))
            self._fill_users_local(s)

    def _fill_users_local(self, s):
        """Мгновенный вид по локальному реестру (без SSH). UID/expiry
        известны только для юзеров, созданных админкой; чужие UID
        появятся после «Обновить»."""
        self.users_tv.delete(*self.users_tv.get_children())
        for u in s.get("users", []):
            exp = u.get("expiry", 0)
            self.users_tv.insert("", "end", iid=u["uid"], values=(
                u["cn"], u["uid"][:20] + "…", u.get("sessions", "?"),
                time.strftime("%d.%m.%Y", time.localtime(exp)) if exp else "—",
                ""))
        self.v_users_srv.set("%s — реестр админки" % s["name"])

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
        if messagebox.askyesno(APP_NAME, "Удалить сервер «%s» из списка?\n"
                               "(сам сервер не трогаем)" % s["name"]):
            self.data["servers"].remove(s)
            save_data(self.data)
            self._refresh_servers()

    # ---- аудит ----
    def _do_audit(self):
        s = self._sel_srv()
        if not s:
            return

        def work():
            self.say("Аудит %s (%s:%s)…" % (s["name"], s["host"],
                                          s.get("ssh_port", 22)))
            ssh = SSH(s, self.say)
            ssh.preflight()
            out = ssh.run_script("detect.sh", timeout=60)
            for ln in out.splitlines():
                self.say("  " + ln)
            m = re.search(r"EXT_IF=(\S+)", out)
            if m:
                s["ext_if"] = m.group(1)
            pub = parse_section(out, "PUBIP")
            if pub and pub != "?":
                s["public_ip"] = pub
            save_data(self.data)
            self.say("Аудит завершён.")
        self._worker(work)

    # ---- деплой ----
    def _do_deploy(self):
        s = self._sel_srv()
        if not s:
            return
        mask = self.v_mask.get().strip() or "www.bing.com"
        proto = self.v_proto.get()
        do_purge = self.v_purge.get()
        do_mss = self.v_mss.get()
        make_user = self.v_first_user.get()
        first_name = self.v_first_name.get().strip() or "user1"

        done_stage = s.get("deploy_stage", -1)
        resume = ""
        if done_stage >= 0:
            resume = ("\n\nПрошлый деплой дошёл до этапа %s — "
                      "продолжу с него (этапы 0-%s пропущу)."
                      % (done_stage + 1, done_stage))
        if not messagebox.askyesno(
                APP_NAME,
                "Развернуть OpenVPN+Cloak на «%s» (%s)?\n\n"
                "Маскировка: %s\nПротокол: %s\nЧистка Amnezia/Docker: %s\n"
                "MSS-clamp: %s\n\nSSH-порт %s не трогаем.%s" %
                (s["name"], s["host"], mask, proto,
                 "да" if do_purge else "нет",
                 "да" if do_mss else "нет", s.get("ssh_port", 22), resume)):
            return

        def work():
            log = self.say
            log("=== Деплой на %s (%s) ===" % (s["name"], s["host"]))
            ssh = SSH(s, log)
            stage = s.get("deploy_stage", -1)  # индекс последнего УСПЕШНОГО

            def mark(n):
                s["deploy_stage"] = n
                save_data(self.data)

            # 0. гейт: SSH жив + sudo без пароля
            ssh.preflight()

            # 1. аудит
            if stage < 1:
                log("[1/6] Аудит…")
            out = ssh.run_script("detect.sh", timeout=60)
            has_docker = "docker" in out.lower() and "no docker" not in out.lower()
            ufw_active = bool(re.search(r"Status: active", out))
            log("  docker=%s ufw_active=%s" % (has_docker, ufw_active))
            pkg = parse_section(out, "PKG").strip()
            osid = re.search(r"ID=(\S+)", parse_section(out, "OS"))
            arch = parse_section(out, "ARCH").strip()
            log("  os=%s pkg=%s arch=%s" % (osid.group(1) if osid else "?",
                                            pkg, arch))
            if pkg != "apt":
                raise SSHErr("Деплой умеет только Debian/Ubuntu (apt). "
                             "На сервере пакетный менеджер: %s" % pkg)
            sshd_ports = [l.strip() for l in
                          parse_section(out, "SSHD_PORTS").splitlines()
                          if l.strip().isdigit()]
            m = re.search(r"EXT_IF=(\S+)", out)
            if m:
                s["ext_if"] = m.group(1)
            pub = parse_section(out, "PUBIP")
            if pub and pub != "?":
                s["public_ip"] = pub
            if stage < 1:
                mark(1)

            # 2. чистка
            if stage < 2:
                if has_docker:
                    if do_purge:
                        log("[2/6] Чистка Amnezia/Docker…")
                        ssh.run_script("purge-amnezia.sh", timeout=600)
                    else:
                        log("!! Docker есть, чистка отключена — 443 может быть занят")
                mark(2)

            # 3. openvpn
            if stage < 3:
                log("[3/6] OpenVPN + PKI…")
                ssh.run_script("deploy-openvpn.sh", proto, timeout=900)
                mark(3)

            # 4. сеть
            if stage < 4:
                log("[4/6] Сеть и NAT…")
                if ufw_active:
                    ssh.run_script("deploy-net-ufw.sh", timeout=300)
                else:
                    # все реальные порты sshd + порт подключения (DNAT!)
                    ports = sorted({str(s["ssh_port"])} | set(sshd_ports),
                                   key=int)
                    ssh.run_script("deploy-net-iptables.sh",
                                   ",".join(ports), timeout=300)
                    # canary: на сервере стоит 120с откат firewall;
                    # если НОВОЕ ssh-подключение живо — откат отменяем
                    ssh.run("touch /tmp/dgcloak-fw-ok", timeout=30)
                    log("  firewall подтверждён (rollback отменён)")
                mark(4)

            # 5. cloak
            if stage < 5:
                log("[5/6] ck-server (маскировка: %s, proto %s)…" % (mask, proto))
                out = ssh.run_script("deploy-cloak.sh",
                                     "%s %s %s" % (mask, proto, CK_VERSION),
                                     timeout=300)
                pub_k = parse_section(out, "PUB")
                admin_uid = parse_section(out, "ADMIN_UID")
                if pub_k and admin_uid:
                    s["pubkey"] = pub_k
                    s["admin_uid"] = admin_uid
                    s["mask_domain"] = mask
                    s["proto"] = proto
                    s["ck_ver"] = CK_VERSION
                    log("  ключи сохранены (AdminUID получен)")
                else:
                    log("!! не удалось прочитать PUB/ADMIN_UID из вывода")
                mark(5)

            # 6. mss + юзер
            if do_mss and stage < 6:
                log("[6/6] MSS-clamp 800…")
                try:
                    ssh.run_script("mss-clamp.sh", timeout=120)
                except Exception as e:
                    log("  mss-clamp пропущен: %s" % e)

            if make_user and s.get("admin_uid"):
                log("Создаю первого юзера «%s»…" % first_name)
                try:
                    self._create_user_impl(ssh, s, first_name, FAR_FUTURE, 16)
                except Exception as e:
                    log("  юзер не создан: %s (сделаешь вручную)" % e)

            s.pop("deploy_stage", None)
            s["deployed"] = bool(s.get("admin_uid"))
            save_data(self.data)
            log("=== Деплой завершён ===")
            self.ui(self._refresh_servers)

        self._worker(work)

    def _do_import(self):
        """Подтянуть pubkey/admin_uid с уже развёрнутого сервера."""
        s = self._sel_srv()
        if not s:
            return

        def work():
            ssh = SSH(s, self.say)
            ssh.preflight()
            out = ssh.run(
                "echo PUB:$(%scat /etc/ck-server/publickey.txt 2>/dev/null); "
                "echo AUID:$(%scat /etc/ck-server/adminuid.txt 2>/dev/null); "
                % (ssh.sudo, ssh.sudo) +
                "grep '^proto ' /etc/openvpn/server/server.conf 2>/dev/null; "
                "grep RedirAddr /etc/ck-server/ckserver.json 2>/dev/null")
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
                save_data(self.data)
                self.say("Импорт: ключи подтянуты с «%s»" % s["name"])
            else:
                self.say("!! Не нашёл /etc/ck-server/* — сервер развёрнут?")
            self.ui(self._refresh_servers)
        self._worker(work)

    # ---- юзеры ----
    def _api(self, s):
        ck = find_ck_client(self.data)
        if not ck:
            raise CloakAPIErr("Не найден ck-client.exe — укажи путь в data.json "
                              "или положи рядом")
        if not s.get("admin_uid") or not s.get("pubkey"):
            raise CloakAPIErr("Нет admin_uid/pubkey — сделай «Импорт» или деплой")
        return CloakAPI(ck, s, self.say)

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
                             uid[:20] + "…" if len(uid) > 20 else uid,
                             u.get("SessionsCap", "?"),
                             time.strftime("%d.%m.%Y", time.localtime(exp))
                             if exp else "—",
                             "●" if kn.get("cn") in online else "")))
            def fill():
                if self._sel_srv_silent() is not s:
                    return  # сервер уже переключён — не затирать чужим списком
                self.users_tv.delete(*self.users_tv.get_children())
                for uid, r in sorted(rows, key=lambda x: x[1][0]):
                    self.users_tv.insert("", "end", iid=uid, values=r)
                self.v_users_srv.set("%s — живой список" % s["name"])
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

    def _create_user_impl(self, ssh, s, name, expiry, sessions):
        """Полный цикл: сертификат на сервере + UID через admin-API + бандл."""
        # 1. сертификат
        self.say("  user-cert.sh «%s»…" % name)
        out = ssh.run_script("user-cert.sh", name, timeout=120)
        self.say("  вывод %d байт" % len(out))
        mats = parse_cert_bundle(out)
        if not all(mats.values()):
            tail = "\n".join(out.strip().splitlines()[-6:])
            raise SSHErr("user-cert.sh: неполный вывод (нет CA/CERT/KEY/TA).\n"
                         "Хвост вывода: %s" % tail)
        # 2. UID через admin-API
        api = self._api(s)
        api.start()
        try:
            uid = api.create_user(sessions_cap=sessions, expiry=expiry)
        finally:
            api.stop()
        # 3. бандл
        proto = s.get("proto", "udp")
        bundle = os.path.join(BUNDLES_DIR, s["name"], name)
        os.makedirs(bundle, exist_ok=True)
        with open(os.path.join(bundle, "%s.ovpn" % name), "w",
                  encoding="utf-8") as f:
            f.write(make_ovpn(proto, mats["ca"], mats["cert"], mats["key"],
                              mats["ta"]))
        with open(os.path.join(bundle, "ckclient-%s.json" % name), "w",
                  encoding="utf-8") as f:
            json.dump(make_ckclient(s, uid), f, indent=2)
        # 4. запись в реестр
        users = s.setdefault("users", [])
        users[:] = [u for u in users if u["cn"] != name]
        users.append({"cn": name, "uid": uid, "expiry": expiry,
                      "sessions": sessions,
                      "created": time.strftime("%Y-%m-%d")})
        save_data(self.data)
        self.say("Юзер «%s» создан. Бандл: %s" % (name, bundle))
        return bundle

    def _user_create(self):
        s = self._sel_srv()
        if not s:
            return
        d = UserDialog(self, title="Новый юзер")
        if not d.result:
            return
        r = d.result

        def work():
            ssh = SSH(s, self.say)
            self._create_user_impl(ssh, s, r["name"], r["expiry"], r["sessions"])
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
            out = ssh.run(
                "%sbash -c 'CADIR=$(ls -d /root/openvpn-ca "
                "/home/*/openvpn-ca 2>/dev/null | head -1) && cd \"$CADIR\" && "
                "printf \"yes\\n%.0s\" $(seq 50) | ./easyrsa revoke %s && "
                "./easyrsa gen-crl && "
                "install -m644 pki/crl.pem /etc/openvpn/server/crl.pem'"
                % (ssh.sudo, cn), timeout=60)
            self.say("  сертификат отозван")
            # 2. delete UID
            if rec and rec.get("uid"):
                try:
                    api = self._api(s)
                    api.start()
                    api.delete_user(rec["uid"])
                    api.stop()
                    self.say("  UID удалён")
                except Exception as e:
                    self.say("  UID: %s" % e)
            # 3. kill live session
            try:
                ssh.upload(os.path.join(SCRIPTS_DIR, "ovpn-mgmt.py"),
                           "/tmp/ovpn-mgmt.py")
                ssh.run("%spython3 /tmp/ovpn-mgmt.py kill %s"
                        % (ssh.sudo, cn), timeout=20)
                self.say("  сессия сброшена")
            except Exception as e:
                self.say("  mgmt: %s" % e)
            s["users"] = [u for u in s.get("users", []) if u["cn"] != cn]
            save_data(self.data)
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
            out = ssh.run("%spython3 /tmp/ovpn-mgmt.py kill %s"
                          % (ssh.sudo, cn),
                          timeout=20)
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
            messagebox.showinfo(APP_NAME, "«%s» не из нашего реестра — "
                                "пересоздай через «Создать»" % cn)
            return
        dst = filedialog.askdirectory(title="Куда сложить бандл «%s»" % cn)
        if not dst:
            return

        def work():
            ssh = SSH(s, self.say)
            out = ssh.run_script("user-cert.sh", cn, timeout=60)
            mats = parse_cert_bundle(out)
            bundle = os.path.join(dst, cn)
            os.makedirs(bundle, exist_ok=True)
            with open(os.path.join(bundle, "%s.ovpn" % cn), "w",
                      encoding="utf-8") as f:
                f.write(make_ovpn(s.get("proto", "udp"), mats["ca"],
                                  mats["cert"], mats["key"], mats["ta"]))
            with open(os.path.join(bundle, "ckclient-%s.json" % cn), "w",
                      encoding="utf-8") as f:
                json.dump(make_ckclient(s, rec["uid"]), f, indent=2)
            self.say("Бандл «%s» → %s" % (cn, bundle))
        self._worker(work)


if __name__ == "__main__":
    App().mainloop()
