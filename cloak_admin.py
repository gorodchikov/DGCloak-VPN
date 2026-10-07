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
                if rc2 == 0:
                    # ключ мёртв — весь объект дальше работает по паролю
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
                       "sudo -n true 2>/dev/null && echo np || echo nop)",
                       timeout=30)
        if "PF:0:" in out:
            self.sudo = ""
            self.srv["sudo_mode"] = "root"
            return
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
            raise SSHErr("plink exit 255")
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
        self.app._worker(work)

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
            self.app.say("fw: %s" % out.strip().splitlines()[-1][:120])
            out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
            self.app.ui(lambda: self._fill(out))
        self.app._worker(work)


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x640")
        self.data = load_data()
        self.uiq = queue.Queue()
        self.busy = False
        self._ports_dlg = None
        self._spin_key = None     # ключ шага, который сейчас крутится
        self._spin_i = 0
        self._spin_t0 = 0.0
        self._spin_hb = 0.0
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
        optf.grid(row=row, column=0, sticky="w", **pad)
        ttk.Label(optf, text="Маскировка:").pack(side="left")
        self.v_mask = tk.StringVar(value="www.bing.com")
        ttk.Entry(optf, textvariable=self.v_mask, width=22).pack(side="left", padx=4)
        ttk.Label(optf, text="Протокол OpenVPN:").pack(side="left", padx=(10, 0))
        self.v_proto = tk.StringVar(value="udp")
        ttk.Combobox(optf, textvariable=self.v_proto, width=6, state="readonly",
                     values=["udp", "tcp"]).pack(side="left")
        ttk.Label(optf, text="(TCP — не рекомендуется, медленнее)",
                  foreground="#a33").pack(side="left", padx=(2, 0))
        ttk.Label(optf, text="Cloak порт:").pack(side="left", padx=(10, 0))
        self.v_ckport = tk.StringVar(value="443")
        ttk.Entry(optf, textvariable=self.v_ckport, width=6).pack(side="left")

        row += 1
        self.steps_tv = ttk.Treeview(f, columns=("st", "note"),
                                     show="tree headings", height=11)
        self.steps_tv.heading("#0", text="Шаг")
        self.steps_tv.heading("st", text="Статус")
        self.steps_tv.heading("note", text="Комментарий")
        self.steps_tv.column("#0", width=320)
        self.steps_tv.column("st", width=70, anchor="center")
        self.steps_tv.column("note", width=220)
        self.steps_tv.grid(row=row, column=0, sticky="nsew", **pad)
        f.rowconfigure(row, weight=1)
        f.columnconfigure(0, weight=1)

        row += 1
        bf = ttk.Frame(f)
        bf.grid(row=row, column=0, sticky="w", **pad)
        big = tk.Button(bf, text="▶  Развернуть всё", command=self._step_run_all,
                        font=("", 10, "bold"), bg="#2d7", fg="white",
                        activebackground="#2a6", padx=10, pady=2)
        big.pack(side="left", padx=(2, 10))
        self._all_buttons.append(big)
        self._mk_btn(bf, "Только выбранный шаг",
                     self._step_run_sel).pack(side="left", padx=2)
        self._mk_btn(bf, "Сбросить статусы", self._steps_reset).pack(side="left", padx=2)
        self._mk_btn(bf, "Управление фаерволом", self._fw_ports).pack(side="left", padx=2)
        b_imp = self._mk_btn(bf, "Подтянуть ключи", self._do_import)
        b_imp.pack(side="left", padx=2)
        Tooltip(b_imp,
                "Если сервер уже настроен (вручную или другой версией\n"
                "программы) — эта кнопка забирает с него ключи Cloak,\n"
                "не переустанавливая ничего. После этого сервером можно\n"
                "управлять: юзеры, бандлы, статусы.")

        row += 1
        ttk.Label(f, text="✓ готово   ⚠ обрати внимание   ✗ ошибка   – пропущен   … не выполнялся\n"
                          "Шаги идут сверху вниз; «Выполнить всё» пропускает готовые.",
                  foreground="#666", justify="left").grid(
            row=row, column=0, sticky="w", **pad)

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
            self._fill_steps()

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
        s = self._sel_srv()
        if not s:
            return
        s.pop("steps", None)
        save_data(self.data)
        self._fill_steps()

    # ---- анимация «выполняется» + heartbeat ----
    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def _spin_start(self, key):
        self._spin_key = key
        self._spin_i = 0
        self._spin_t0 = self._spin_hb = time.time()
        self._spin_tick()

    def _spin_stop(self):
        self._spin_key = None

    def _spin_tick(self):
        """Крутит спиннер в статусе шага + heartbeat в лог (главный поток)."""
        k = self._spin_key
        if k is None:
            return
        try:
            vals = list(self.steps_tv.item(k, "values"))
        except tk.TclError:
            vals = []  # строки нет (юзер переключил сервер)
        if vals:
            vals[0] = self.SPIN[self._spin_i % len(self.SPIN)]
            self.steps_tv.item(k, values=tuple(vals))
        self._spin_i += 1
        el = time.time() - self._spin_t0
        if el - self._spin_hb >= 30:
            self._spin_hb = el
            self.say("  …выполняется уже %d мин %d с — процесс жив, "
                     "ждём ответа сервера" % (el // 60, int(el) % 60))
        self.after(150, self._spin_tick)

    def _run_step(self, s, key):
        title = dict(self.STEPS)[key]
        self.say("=== Шаг: %s ===" % title)
        st = s.setdefault("steps", {})
        self.ui(self._spin_start, key)
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
            for key, _t in self.STEPS:
                if s.get("steps", {}).get(key, {}).get("st") == "ok":
                    continue
                if not self._run_step(s, key):
                    self.say("Остановился на шаге «%s». Исправь и продолжай — "
                             "завершённые шаги не повторятся." % key)
                    break
            self.say("=== Проход завершён ===")
        self._worker(work)

    # ---- шаги ----
    def _step_ssh(self, ssh, s):
        ssh.preflight()
        out = ssh.run("echo U=$(id -un) H=$(hostname)", timeout=20)
        return "ok", out.strip().replace("\n", " ")

    def _step_key(self, ssh, s):
        if s.get("key") or s.get("ppk"):
            # проверяем, что ключ реально работает — VM могли откатить
            # на снапшот без него (ключ «мёртв», пароль спасает как фолбэк)
            s2 = dict(s); s2["password"] = None
            try:
                SSH(s2, self.say).run("true", timeout=15)
                return "ok", "уже ключевая авторизация"
            except Exception:
                self.say("  сохранённый ключ отвергнут — ставлю новый")
                s.pop("key", None); s.pop("ppk", None)
        if not s.get("password"):
            return "fail", "нет ни пароля, ни ключа"
        if not self.ask(APP_NAME,
                        "Сгенерировать ключ ed25519 и поставить на «%s»?\n"
                        "Пароль останется как запасной вход." % s["name"]):
            return "skip", "отменено пользователем"
        kg = find_exe(["ssh-keygen.exe", "ssh-keygen"],
                      [r"C:\Windows\System32\OpenSSH"])
        if not kg:
            raise SSHErr("не найден ssh-keygen (Windows OpenSSH)")
        kd = os.path.join(APP_DIR, "keys")
        os.makedirs(kd, exist_ok=True)
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
        m = re.search(r"EXT_IF=(\S+)", out)
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
            if not self.ask(
                    APP_NAME,
                    "На «%s» фаервола нет. Поставить nftables?\n\n"
                    "INPUT DROP + разрешены: SSH (%s), Cloak tcp/%s, ICMP.\n"
                    "Остальные входящие закроются. Анти-локаут: если SSH умрёт,\n"
                    "правила сами откатятся через 120 секунд."
                    % (s["name"], ",".join(ports), ck)):
                return "skip", "фаервола нет, установка отменена"
            ssh.run_script("fw-install.sh", "%s %s" % (",".join(ports), ck),
                           timeout=300)
            # canary: на сервере 120с откат; новое ssh-подключение подтверждает
            ssh.run("touch /tmp/dgcloak-fw-ok", timeout=30)
            self.say("  firewall подтверждён (rollback отменён)")
            s["fw_backend"] = "nftables-dg"
            s["ck_port"] = ck
            return "ok", "nftables: ssh=%s cloak=%s" % (",".join(ports), ck)
        if fw == "iptables-custom":
            return "warn", "чужие правила iptables — смотри «Управление фаерволом»"
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
                        "apt update + full-upgrade + autoremove на «%s»?\n"
                        "Может занять несколько минут." % s["name"]):
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
            return "warn", "обновлено, нужен reboot"
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
            if not self.ask(APP_NAME,
                            "Не хватает пакетов: %s\nПоставить (apt install)?"
                            % ", ".join(missing)):
                return "warn", "не хватает: %s" % ",".join(missing)
            rc = ssh.run_script_stream(
                "pkgs.sh", "install",
                lambda l: self.say("  " + l), timeout=600)
            if rc != 0:
                return "fail", "pkgs install rc=%s" % rc
            out = ssh.run_script("pkgs.sh", "check", timeout=90)
            missing = [l.split("=")[0] for l in
                       parse_section(out, "PKGS").splitlines()
                       if l.strip().endswith("=-")]
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
                                "Порт 443 занят (Amnezia/docker).\n"
                                "Снести Amnezia? Чужие контейнеры не трогаем."):
                    return "fail", "443 занят, чистка отменена"
                ssh.run_script("purge-amnezia.sh", timeout=600)
            else:
                return "fail", "443 занят чужим сервисом: %s" % busy.strip()[:100]
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
            out = ssh.run(
                "echo PUB:$(%scat /etc/ck-server/publickey.txt 2>/dev/null); "
                "echo AUID:$(%scat /etc/ck-server/adminuid.txt 2>/dev/null); "
                % (ssh.sudo, ssh.sudo) +
                "grep '^proto ' /etc/openvpn/server/server.conf 2>/dev/null || true; "
                "grep RedirAddr /etc/ck-server/ckserver.json 2>/dev/null || true")
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
                "./easyrsa --batch revoke %s && "
                "./easyrsa --batch gen-crl && "
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
