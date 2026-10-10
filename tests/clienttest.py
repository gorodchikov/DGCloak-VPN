# -*- coding: utf-8 -*-
"""Авто-тесты клиента DGCloak VPN (cloak_ovpn.py).

Песочница:
- весь App живёт в tempdir: APP_DIR/DATA_FILE/PROFILES_DIR/PIDS_FILE/BIN_DIR подменены;
- трей, поиск бинарников и первый запуск отключены; сеть/процессы — фейки;
- реальный %APPDATA%\\DGCloak не трогаем; ничего не подключаем и не ставим.

Запуск: python tests\\clienttest.py   (выход 0 — всё зелёное)
"""
import sys, os, io, re, ast, json, time, tempfile, threading, shutil

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import cloak_ovpn as CO  # noqa: E402
from tkinter import messagebox  # noqa: E402

TMP = tempfile.mkdtemp(prefix="dgctest-")
CO.DGCLOAK_DIR = TMP
CO.APP_DIR = os.path.join(TMP, "VPN")
CO.DATA_FILE = os.path.join(CO.APP_DIR, "data.json")
CO.PROFILES_DIR = os.path.join(CO.APP_DIR, "profiles")
CO.PIDS_FILE = os.path.join(CO.APP_DIR, "pids.json")
CO.INBOX_DIR = os.path.join(CO.APP_DIR, "inbox")
CO.BIN_DIR = os.path.join(TMP, "bin")
CO.OLD_APP_DIR = os.path.join(TMP, "DGCloakVPN")
CO.HAS_TRAY = False                    # трей не поднимаем
CO.is_admin = lambda: True             # без предупреждения про права
CO.App._first_run = lambda self: None  # без мастера установки/диалогов

# фикстура миграции до создания App: старая папка %APPDATA%\DGCloakVPN
os.makedirs(CO.OLD_APP_DIR, exist_ok=True)
with open(os.path.join(CO.OLD_APP_DIR, "ck-client.exe"), "w") as f:
    f.write("x")
with open(os.path.join(CO.OLD_APP_DIR, "data.json"), "w", encoding="utf-8") as f:
    json.dump({"ck_client": CO.OLD_APP_DIR + "\\ck-client.exe",
               "profiles": [{"name": "p", "ovpn": CO.OLD_APP_DIR + "\\profiles\\p.ovpn"}]}, f)

MSGBOX = []
CO.messagebox.showinfo = lambda *a, **kw: MSGBOX.append(("info", a))
CO.messagebox.showerror = lambda *a, **kw: MSGBOX.append(("error", a))
CO.messagebox.askyesno = lambda *a, **kw: True

_NPASS = [0]
_NFAIL = [0]


def check(name, cond, extra=""):
    _NPASS[0] += 1 if cond else 0
    _NFAIL[0] += 0 if cond else 1
    print(("PASS " if cond else "FAIL ") + name +
          (" | " + str(extra)[:200] if extra else ""))


def pump(app, t=0.3):
    end = time.time() + t
    while time.time() < end:
        try:
            app.update()
        except Exception:
            pass
        time.sleep(0.02)


def logfile():
    p = os.path.join(CO.APP_DIR, "last.log")
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""


def wait_log(marker, timeout=15):
    """Подождать строку в last.log (фоновые потоки пишут асинхронно)."""
    end = time.time() + timeout
    while time.time() < end:
        if marker in logfile():
            return True
        time.sleep(0.1)
    return False


def _tray_active_shape(app):
    """Меню трея при активном VPN: «Отключить «name»» — отдельным пунктом,
    «Отключить «name» и подключить» — подменю только с другими профилями."""
    old_active, old_profiles = app.active, app.data["profiles"]
    app.active = {"name": "u1"}
    app.data["profiles"] = [{"name": "u1"}, {"name": "u2"}, {"name": "u3"}]
    try:
        items = list(app._tray_items())
        texts = [str(getattr(i, "text", i)) for i in items]
        if "Отключить «u1»" not in texts:
            return False
        parent = next((i for i in items
                       if getattr(i, "text", "") == "Отключить «u1» и подключить"),
                      None)
        if parent is None or parent.submenu is None:
            return False
        sub = [getattr(i, "text", "") for i in list(parent.submenu)]
        return sub == ["u2", "u3"]
    finally:
        app.active, app.data["profiles"] = old_active, old_profiles


# ---------- T1. Целостность локализации (аудит как тест) ------------------

def t_strings():
    src = open(os.path.join(REPO, "cloak_ovpn.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    en, docids = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "STRINGS_EN":
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                    en[k.value] = v.value
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant):
                docids.add(id(node.body[0].value))
    cyr = re.compile("[а-яА-ЯёЁ]")
    ph = re.compile(r"\{(\w+)\}")
    skip = {"Русский", "DGCloak VPN уже запущен / is already running."}
    missing, mismatch = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docids and cyr.search(node.value):
            s = node.value.strip()
            if len(s) > 3 and s not in en and s not in skip \
                    and not any(s == k for k in en):
                missing.append(s)
    for k, v in en.items():
        if set(ph.findall(k)) != set(ph.findall(v)):
            mismatch.append(k)
    check("T1.1 все RU-строки UI/лога имеют EN-перевод", not missing,
          "; ".join(repr(m[:60]) for m in missing[:5]))
    check("T1.2 плейсхолдеры {..} совпадают RU↔EN", not mismatch,
          "; ".join(repr(m[:60]) for m in mismatch[:5]))


# ---------- T2-T3. Язык --------------------------------------------------

def t_lang(app):
    app.data["language"] = "en"
    check("T2.1 t() переводит при en", app.t("Подключить") == "Connect")
    app.data["language"] = "ru"
    check("T2.2 t() оставляет RU при ru", app.t("Подключить") == "Подключить")
    check("T2.3 t()/say() не падают на kwarg 's' (heartbeat)",
          app.t("…жду OpenVPN {s} с; management: {m}; состояние: {st}",
                s=5, m="x", st="y").startswith("…жду OpenVPN 5"))
    MSGBOX.clear()
    app.lang_combo.set("English")
    app._on_lang_pick(None)
    pump(app)
    check("T3.1 при смене языка диалог НЕ показывается (тултип на «Язык:»)",
          not MSGBOX, MSGBOX)
    check("T3.1b тултип перепереведён на en",
          app._lang_tips and "previous language" in app._lang_tips[0].text,
          app._lang_tips[0].text if app._lang_tips else "no tips")
    check("T3.2 язык сохранён в data.json",
          json.load(open(CO.DATA_FILE, encoding="utf-8"))["language"] == "en")
    app.lang_combo.set("Русский")
    app._on_lang_pick(None)
    pump(app)
    check("T3.3 обратно на ru — тултип по-русски",
          "останутся на прежнем языке" in app._lang_tips[0].text,
          app._lang_tips[0].text)
    check("T3.4 меню трея при active: «Отключить» вынесен, подменю — только профили",
          _tray_active_shape(app))


# ---------- T4-T5. Диалог профиля -----------------------------------------

def t_profile_dlg(app):
    # фикстура .dgcloak: ck-конфиг с UDP + текст ovpn
    cloak = {"RemoteHost": "vpn.test.local", "RemotePort": 443, "UDP": True}
    dgc = os.path.join(TMP, "u1.dgcloak")
    with open(dgc, "w", encoding="utf-8") as f:
        json.dump({"name": "u1", "cloak": cloak, "ovpn": "client\nremote x 1194\n"}, f)

    d = CO.ProfileDialog(app)
    check("T4.1 порт по умолчанию 1984", d.vars["port"].get() == "1984")
    check("T4.2 ADV по умолчанию свёрнут", not d._adv_open)
    d.vars["name"].set("u1")
    d.vars["dgcloak"].set(dgc)
    # «Проверить» до сохранения (после _ok диалог уничтожается)
    ck_json = os.path.join(TMP, "check-ck.json")
    with open(ck_json, "w", encoding="utf-8") as f:
        json.dump({"RemoteHost": "localhost"}, f)
    d.vars["ck_config"].set(ck_json)
    d._check()
    t_end = time.time() + 5
    while "проверяю" in d._check_lbl["text"] and time.time() < t_end:
        try:
            app.update()
        except Exception:
            pass
        time.sleep(0.1)
    txt = d._check_lbl["text"]
    check("T4.6 «Проверить»: порт + резолв сервера",
          "порт 1984" in txt and "127.0.0.1" in txt, txt)
    d._ok()
    r = d.result
    ok = r and r["ck_config"].startswith(CO.PROFILES_DIR) and os.path.isfile(r["ovpn"])
    check("T4.3 .dgcloak материализован в profiles\\", bool(ok), r)
    check("T4.4 UDP поднят из cloak.UDP", r and r["udp"] is True)
    check("T4.5 reconnect=True по умолчанию", r and r.get("reconnect") is True)

    MSGBOX.clear()
    d2 = CO.ProfileDialog(app)
    d2._ok()
    check("T5.1 пустой профиль → ошибка, result=None",
          d2.result is None and MSGBOX and MSGBOX[-1][0] == "error")
    d2.destroy()

    d3 = CO.ProfileDialog(app, {"name": "x", "port": "9999"})
    check("T5.2 нестандартный ADV → секция открыта сразу", d3._adv_open)
    d3.destroy()
    d.destroy()


# ---------- T6-T7. Маршруты/remote ----------------------------------------

def _mk_ovpn(text="client\nremote srv 1194\nca ca.crt\n"):
    p = os.path.join(TMP, "test.ovpn")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def t_ovpn(app):
    p = {"port": 1984, "ovpn": _mk_ovpn()}
    rt = app._runtime_ovpn(p)
    body = open(rt, encoding="utf-8").read()
    check("T6.1 runtime.ovpn: remote переписан на 127.0.0.1:1984 tcp",
          "remote 127.0.0.1 1984 tcp-client" in body and "remote srv" not in body)
    rt = app._runtime_ovpn(dict(p, udp=True))
    body = open(rt, encoding="utf-8").read()
    check("T6.2 UDP-профиль → remote … udp", "remote 127.0.0.1 1984 udp" in body)


def t_bypass(app):
    ck = os.path.join(TMP, "ck.json")
    with open(ck, "w", encoding="utf-8") as f:
        json.dump({"RemoteHost": "localhost"}, f)
    p = {"ck_config": ck}
    check("T7.1 ручной bypass_ip приоритетнее",
          app._bypass_ip(dict(p, bypass_ip="1.2.3.4")) == "1.2.3.4")
    check("T7.2 RemoteHost резолвится", app._bypass_ip(p) == "127.0.0.1")
    with open(ck, "w", encoding="utf-8") as f:
        json.dump({"RemoteHost": "no-such-host.invalid"}, f)
    check("T7.3 неразрешимый RemoteHost → None + предупреждение",
          app._bypass_ip(p) is None and "Не удалось резолвить" in logfile())
    with open(ck, "w", encoding="utf-8") as f:
        json.dump({}, f)
    check("T7.4 без RemoteHost → None", app._bypass_ip(p) is None)


# ---------- T8. PID / T9. external_ip / T10. say ---------------------------

def t_pids_ip_log(app):
    CO.save_pids({"ck_pid": os.getpid(), "vpn_pid": 99999999})
    d = CO.load_pids()
    check("T8.1 pids.json roundtrip", d.get("ck_pid") == os.getpid())
    exe = os.path.basename(sys.executable)
    check("T8.2 pid_running: свой PID жив", CO.pid_running(os.getpid(), exe))
    check("T8.3 pid_running: мёртвый PID — False", not CO.pid_running(99999999, exe))
    check("T8.4 pid_running: чужое имя exe — False", not CO.pid_running(os.getpid(), "notpython.exe"))

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    real = CO.urllib.request.urlopen
    CO.urllib.request.urlopen = lambda req, timeout=0: R(b"9.9.9.9")
    check("T9.1 external_ip парсит ответ", CO.external_ip() == "9.9.9.9")
    CO.urllib.request.urlopen = lambda req, timeout=0: R(b"<html>nope</html>")
    check("T9.2 мусор в ответе → None", CO.external_ip() is None)

    def boom(*a, **k):
        raise OSError("offline")

    CO.urllib.request.urlopen = boom
    check("T9.3 нет сети → None (не падает)", CO.external_ip() is None)

    def flaky(req, timeout=0):
        if "ipify" in req.full_url:
            raise OSError("ipify down")
        return R(b"8.8.4.4")

    CO.urllib.request.urlopen = flaky
    check("T9.4 фолбэк на второй сервис (ipify упал → ifconfig.me)",
          CO.external_ip() == "8.8.4.4")
    CO.urllib.request.urlopen = real

    app.say("тестовая-строка-xyz")
    check("T10.1 say() пишет в last.log сразу", "тестовая-строка-xyz" in logfile())


# ---------- T11. _connect с фейками (матрица TCP/UDP, full/split) ----------

class FakeProc:
    def __init__(self, args, **kw):
        self.args = args
        self.pid = 40000 + len(CALLS)
        self.stdout = iter(())
        CALLS.append(args)

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, t=None):
        return 0


CALLS = []


def t_connect(app):
    for exe_key in ("ck_client", "openvpn_exe"):
        if not os.path.isfile(app.data[exe_key]):
            app.data[exe_key] = sys.executable  # существующий exe — пройдёт isfile
    ck = os.path.join(TMP, "ck-udp.json")
    with open(ck, "w", encoding="utf-8") as f:
        json.dump({"RemoteHost": "localhost", "UDP": True}, f)
    ovpn = _mk_ovpn()

    # фейки окружения
    saved = {k: getattr(CO, k) for k in
             ("repair_loopback", "loopback_ok", "load_pids",
              "find_procs", "port_open", "probe", "external_ip")}
    saved_popen = CO.subprocess.Popen
    saved_mgmt = CO.App._mgmt_loop
    saved_mon = CO.App._start_monitor
    CO.repair_loopback = lambda: False
    CO.loopback_ok = lambda: True
    CO.load_pids = lambda: {}
    CO.find_procs = lambda name: []
    CO.port_open = lambda h, p: False
    CO.probe = lambda h, p, t: None
    ips = iter(["1.1.1.1", "2.2.2.2"])
    CO.external_ip = lambda timeout=4: next(ips, "3.3.3.3")
    CO.subprocess.Popen = FakeProc
    CO.App._mgmt_loop = lambda self, port, proc, p: (
        threading.Thread(target=lambda: (time.sleep(0.2), self.up.set()),
                         daemon=True).start())
    CO.App._start_monitor = lambda self: None
    try:
        CALLS.clear()
        before = logfile()
        p = {"name": "u1", "ck_config": ck, "ovpn": ovpn, "port": 1984,
             "udp": False, "full_tunnel": True}
        app._connect(p)
        ck_args = CALLS[0]
        ov_args = CALLS[1]
        new_log = logfile().replace(before, "")
        check("T11.1 ck: -c/-l 1984 и авто-UDP (-u) из конфига",
              "-l" in ck_args and "1984" in ck_args and "-u" in ck_args
              and "UDP-режим автоматически" in new_log)
        check("T11.2 openvpn: --route bypass + redirect-gateway local def1",
              "--route" in ov_args and "net_gateway" in ov_args
              and "local" in ov_args and "def1" in ov_args
              and "--disable-dco" in ov_args)
        check("T11.3 внешний IP до/после в логе",
              "Внешний IP до подключения: 1.1.1.1" in new_log
              and wait_log("Внешний IP через VPN: 2.2.2.2"))
        check("T11.4 pids сохранены",
              json.load(open(CO.PIDS_FILE, encoding="utf-8")).get("vpn_pid") == 40001,
              open(CO.PIDS_FILE, encoding="utf-8").read()[:120])
        app.active = p

        # split tunnel + нет RemoteHost → предупреждение про зацикливание
        CALLS.clear()
        with open(ck, "w", encoding="utf-8") as f:
            json.dump({}, f)
        p2 = dict(p, udp=False, full_tunnel=True)
        CO.external_ip = lambda timeout=4: None
        before = logfile()
        app._connect(p2)
        new_log = logfile().replace(before, "")
        check("T11.5 без bypass при full_tunnel → ВНИМАНИЕ в лог",
              "зациклится" in new_log)
        check("T11.6 внешний IP недоступен → «не определён»",
              wait_log("Внешний IP через VPN: не определён"))
        check("T11.7 без UDP в конфиге и профиле → нет -u",
              "-u" not in CALLS[0])

        # split tunnel: redirect-gateway отсутствует
        CALLS.clear()
        app._connect(dict(p, udp=False, full_tunnel=False, bypass_ip="8.8.8.8"))
        check("T11.8 split tunnel → нет redirect-gateway, bypass есть",
              "--redirect-gateway" not in CALLS[1] and "8.8.8.8" in CALLS[1])

        # статус активного соединения: имя · IP · аптайм · трафик
        app.ext_ip = "2.2.2.2"
        app._rate = (1500.0, 2500000.0)
        app.up_since = time.time() - 60
        app._conn_status()
        st = app._status_msg[0]
        lines = st.splitlines()
        cols = [lines[i].index(v) for i, v in
                enumerate(("u1", "2.2.2.2", "00:01", "↓1.5 KB/s ↑2.4 MB/s"))]
        check("T11.9 _conn_status: столбик, значения выровнены",
              len(lines) == 4 and len(set(cols)) == 1, st)
        check("T11.10 _fmt_bytes",
              CO._fmt_bytes(500) == "500 B" and CO._fmt_bytes(2500000) == "2.4 MB",
              CO._fmt_bytes(2500000))
        app._stop_all()
    finally:
        for k, v in saved.items():
            setattr(CO, k, v)
        CO.subprocess.Popen = saved_popen
        CO.App._mgmt_loop = saved_mgmt
        CO.App._start_monitor = saved_mon


# ---------- T13. import_profile_files --------------------------------------

def t_import_files():
    d = os.path.join(TMP, "src-prof")
    os.makedirs(d, exist_ok=True)
    ovpn = os.path.join(d, "p.ovpn")
    ca = os.path.join(d, "ca.crt")
    key = os.path.join(d, "cl.key")
    for pth, body in ((ovpn, "client\nremote x 1194\nca ca.crt\nkey cl.key\n"),
                      (ca, "CA"), (key, "KEY")):
        open(pth, "w").write(body)
    r = {"name": "imp1", "ovpn": ovpn}
    warns = CO.import_profile_files(r)
    dest = os.path.join(CO.PROFILES_DIR, "imp1")
    check("T13.1 .ovpn и внешние ключи скопированы в profiles\\imp1",
          os.path.isfile(os.path.join(dest, "p.ovpn"))
          and os.path.isfile(os.path.join(dest, "ca.crt"))
          and os.path.isfile(os.path.join(dest, "cl.key")), warns)
    check("T13.2 пути в r переписаны на копии",
          r["ovpn"].startswith(CO.PROFILES_DIR))
    # ссылка на несуществующий файл → пропуск без падения (isfile-guard)
    open(ovpn, "a").write("cert missing.crt\n")
    r2 = {"name": "imp2", "ovpn": ovpn}
    warns = CO.import_profile_files(r2)
    check("T13.3 несуществующий файл-ключ → пропущен, не упало",
          r2["ovpn"].startswith(CO.PROFILES_DIR) and not warns, warns)


# ---------- T14. _mgmt_loop на живом localhost-сокете ----------------------

def t_mgmt_loop(app):
    import socket as _s
    ls = _s.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    port = ls.getsockname()[1]

    def serve():
        c, _ = ls.accept()
        f = c.makefile("rw", encoding="utf-8", newline="\n")
        f.readline()                       # "state on all"
        f.write("1700000000,CONNECTED,SUCCESS,10.8.0.2,\n")
        f.write("SUCCESS: bytesin=1000,bytesout=2000\n")
        f.flush()
        time.sleep(1.0)
        f.write("SUCCESS: bytesin=4000,bytesout=8000\n")
        f.flush()
        time.sleep(0.4)
        try:
            f.close(); c.close()
        except OSError:
            pass
        ls.close()

    threading.Thread(target=serve, daemon=True).start()
    app.up.clear()
    app._stats_prev = None
    proc = FakeProc(["x"])
    th = threading.Thread(target=app._mgmt_loop,
                          args=(port, proc, {"name": "u1"}), daemon=True)
    th.start()
    th.join(6)
    check("T14.1 mgmt CONNECTED → up.is_set", app.up.is_set())
    check("T14.2 load-stats → traffic (4000,8000)",
          app.traffic == (4000, 8000), app.traffic)
    check("T14.3 rate посчитан (≈3 KB/s ↓, ≈6 KB/s ↑)",
          app._rate and app._rate[0] > 1500 and app._rate[1] > 4000,
          app._rate)
    check("T14.4 management-сокет сохранён", app.mgmt is not None)
    app.mgmt = None
    app.up.clear()


# ---------- T15. _stop_vpn: SIGTERM через mgmt ------------------------------

def t_stop_vpn(app):
    sent = []

    class FakeMgmt:
        def sendall(self, b):
            sent.append(b)

        def close(self):
            pass

    class Alive:
        def poll(self):
            return None

        def wait(self, t=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    app.mgmt = FakeMgmt()
    app.vpn = Alive()
    app._stop_vpn()
    check("T15.1 _stop_vpn шлёт 'signal SIGTERM' в mgmt",
          sent == [b"signal SIGTERM\n"], sent)
    check("T15.2 _stop_vpn обнуляет self.vpn", app.vpn is None)


# ---------- T16. _monitor: автореконнект ------------------------------------

def t_monitor(app):
    class Dead:
        returncode = 1

        def poll(self):
            return 1

        def wait(self, t=None):
            return 1

        def terminate(self):
            pass

        def kill(self):
            pass

    class Alive:
        def poll(self):
            return None

        def wait(self, t=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    called = []
    orig_run, orig_conn = app._run, app._connect
    app._run = lambda fn: called.append(fn)   # перехват, не исполняем
    try:
        # reconnect=True → _connect(p) уходит в _run
        app.active = {"name": "u1", "reconnect": True}
        app.up_since = time.time()
        app._rate = None
        app.ext_ip = None
        app.bypass_missing = False
        app.ck = Dead()
        app.vpn = Alive()
        app.mgmt = None
        app.busy = False
        stop = threading.Event()
        th = threading.Thread(target=app._monitor, args=(stop,), daemon=True)
        th.start()
        t_end = time.time() + 5
        while not called and time.time() < t_end:
            pump(app, 0.2)
        stop.set(); th.join(3)
        check("T16.1 обрыв + reconnect → _connect перезапущен",
              len(called) == 1, called)
        check("T16.2 в логе «переподключение (1/3)»",
              "переподключение (1/3)" in logfile())

        # reconnect=False → красный статус, без _connect
        called.clear()
        app.active = {"name": "u1", "reconnect": False}
        app.up_since = time.time()
        app.ck = Dead()
        app.vpn = Alive()
        app.mgmt = None
        stop = threading.Event()
        th = threading.Thread(target=app._monitor, args=(stop,), daemon=True)
        th.start()
        time.sleep(3.0)
        pump(app, 0.3)
        stop.set(); th.join(3)
        check("T16.3 reconnect=False → без перезапуска", not called)
        check("T16.4 статус красный 'Cloak остановлен'",
              app._status_msg[1] == "red"
              and "Cloak" in app._status_msg[0], app._status_msg[:2])
    finally:
        app._run = orig_run
        app._connect = orig_conn
        app.active = None
        app.up_since = None


# ---------- T17. port_open/probe на живом сокете ----------------------------

def t_ports():
    ls = CO.socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(8)
    p = ls.getsockname()[1]
    stop = threading.Event()

    def accepter():  # иначе backlog кончается после первого коннекта
        ls.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = ls.accept()
                c.close()
            except OSError:
                pass

    threading.Thread(target=accepter, daemon=True).start()
    check("T17.1 port_open: открытый порт → True", CO.port_open("127.0.0.1", p))
    check("T17.2 probe: открытый порт → None",
          CO.probe("127.0.0.1", p) is None)
    stop.set()
    ls.close()
    check("T17.3 port_open: закрытый порт → False",
          not CO.port_open("127.0.0.1", p))
    check("T17.4 probe: закрытый порт → текст ошибки",
          isinstance(CO.probe("127.0.0.1", p, 1), str))


# ---------- T18. _conn_status: нет обхода → оранжевый + warning -------------

def t_status_warn(app):
    app.active = {"name": "u1"}
    app.up_since = time.time() - 30
    app._rate = None
    app.ext_ip = "9.9.9.9"
    app.bypass_missing = True
    app._conn_status()
    text, color, _ = app._status_msg
    lines = text.splitlines()
    check("T18.1 bypass_missing → 5-я строка «НЕТ ОБХОДА»",
          len(lines) == 5 and "НЕТ ОБХОДА" in lines[4], lines[-1])
    check("T18.2 bypass_missing → оранжевый статус", color == "orange")
    app.ext_ip = None
    app._conn_status()
    text2 = app._status_msg[0]
    check("T18.3 ext_ip=None → строки «VPN IP» нет",
          "VPN IP" not in text2, text2)
    app.bypass_missing = False
    app.active = None


# ---------- T19. «Проверить»: занятый порт и битый хост ---------------------

def t_check_dlg(app):
    ls = CO.socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    busy_port = ls.getsockname()[1]
    d = CO.ProfileDialog(app)
    try:
        d.vars["port"].set(str(busy_port))
        d.vars["server"].set("nonexistent-host-xyz.invalid")
        d._check()
        t_end = time.time() + 6
        while "проверяю" in d._check_lbl["text"] and time.time() < t_end:
            pump(app, 0.15)
        txt = d._check_lbl["text"]
        check("T19.1 занятый порт → ЗАНЯТ", "ЗАНЯТ" in txt, txt)
        check("T19.2 неразрешимый хост → «не резолвится»",
              "не резолвится" in txt, txt)
    finally:
        ls.close()
        d.destroy()


# ---------- T20. data.json + free_port --------------------------------------

def t_data():
    # недеструктивный roundtrip: сейвим то же, что прочитали (файл читает T12)
    d0 = CO.load_data()
    CO.save_data(d0)
    d = CO.load_data()
    check("T20.1 save/load roundtrip", d == d0)
    p = CO.free_port()
    check("T20.2 free_port → свободный порт 1024-65535",
          1024 <= p <= 65535 and not CO.port_open("127.0.0.1", p), p)


# ---------- T12. миграция старой папки ------------------------------------

# ---------- T21. inbox: профили от админки --------------------------------

def t_inbox(app):
    os.makedirs(CO.INBOX_DIR, exist_ok=True)
    dgc = os.path.join(TMP, "adm.dgcloak")
    cloak = {"UID": "u", "RemoteHost": "h", "RemotePort": "443",
             "UDP": True}
    json.dump({"name": "u1", "cloak": cloak,
               "ovpn": "client\nproto udp\n"},
              open(dgc, "w", encoding="utf-8"))
    mark = os.path.join(CO.INBOX_DIR, "u1@srvA.dgcloak")
    shutil.copyfile(dgc, mark)
    app._process_inbox()
    names = [p["name"] for p in app.data["profiles"]]
    check("T21.1 inbox .dgcloak → профиль «u1@srvA» добавлен",
          "u1@srvA" in names and not os.path.exists(mark), names)
    p = next(q for q in app.data["profiles"] if q["name"] == "u1@srvA")
    check("T21.2 материализовано в profiles\\, udp=True из cloak",
          p["ovpn"].startswith(CO.PROFILES_DIR)
          and p["ck_config"].startswith(CO.PROFILES_DIR)
          and p["udp"] is True)
    json.dump({"name": "u1", "cloak": dict(cloak, UDP=False),
               "ovpn": "client\nproto tcp\n"},
              open(dgc, "w", encoding="utf-8"))
    shutil.copyfile(dgc, mark)
    app._process_inbox()
    names2 = [q["name"] for q in app.data["profiles"]]
    p2 = next(q for q in app.data["profiles"] if q["name"] == "u1@srvA")
    check("T21.3 повторный inbox → upsert без дубля",
          names2.count("u1@srvA") == 1 and p2["udp"] is False)
    with open(os.path.join(CO.INBOX_DIR, "u1@srvA.del"), "w") as f:
        f.write("u1@srvA")
    app._process_inbox()
    check("T21.4 inbox .del → профиль удалён",
          "u1@srvA" not in [q["name"] for q in app.data["profiles"]])


def t_shortcut():
    """T22: удаление ярлыка OpenVPN GUI — Public/личный/OneDrive десктопы."""
    env0 = {k: os.environ.get(k) for k in ("PUBLIC", "USERPROFILE")}
    pub = os.path.join(TMP, "pub"); home = os.path.join(TMP, "home")
    os.environ["PUBLIC"] = pub
    os.environ["USERPROFILE"] = home
    try:
        lnks = []
        for d in (os.path.join(pub, "Desktop"),
                  os.path.join(home, "Desktop"),
                  os.path.join(home, "OneDrive - X", "Desktop")):
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "OpenVPN GUI.lnk")
            open(p, "w").close()
            lnks.append(p)
        got = CO.remove_desktop_shortcut("OpenVPN GUI.lnk")
        check("T22.1 ярлык снят на всех трёх десктопах",
              sorted(got) == sorted(lnks)
              and all(not os.path.exists(p) for p in lnks))
        check("T22.2 повторный вызов без ярлыка → пусто, без ошибок",
              CO.remove_desktop_shortcut("OpenVPN GUI.lnk") == [])
    finally:
        for k, v in env0.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def t_rename(app):
    """T23: переименование профиля — файлы переезжают, дубли имён отклонены."""
    d_old = os.path.join(CO.PROFILES_DIR, "oldn")
    os.makedirs(d_old, exist_ok=True)
    ov = os.path.join(d_old, "oldn.ovpn")
    ck = os.path.join(d_old, "ck.json")
    open(ov, "w").write("client\nremote x 1194\nca ca.crt\n")
    open(os.path.join(d_old, "ca.crt"), "w").write("CA")
    open(ck, "w").write("{}")
    prof = {"name": "newn", "ovpn": ov, "ck_config": ck}
    CO.import_profile_files(prof)
    d_new = os.path.join(CO.PROFILES_DIR, "newn")
    check("T23.1 переименование: файлы и ключи переехали в profiles/<new>",
          os.path.dirname(prof["ovpn"]) == d_new
          and os.path.dirname(prof["ck_config"]) == d_new
          and os.path.isfile(os.path.join(d_new, "ca.crt")))
    # дубль и safe-коллизия имён — диалог отклоняет
    f1 = os.path.join(TMP, "c.json"); open(f1, "w").write("{}")
    f2 = os.path.join(TMP, "c.ovpn"); open(f2, "w").write("client\nremote x 1\n")
    old_prof = app.data["profiles"]
    app.data["profiles"] = [{"name": "e x"}]
    MSGBOX.clear()
    dd = CO.ProfileDialog(app)
    dd.vars["name"].set("e_x")
    dd.vars["ck_config"].set(f1)
    dd.vars["ovpn"].set(f2)
    dd._ok()
    check("T23.2 safe-коллизия («e x» vs «e_x») → отказ",
          dd.result is None and MSGBOX and MSGBOX[-1][0] == "error")
    dd.destroy()
    # своё же имя при правке — не коллизия (orig исключён по identity)
    dself = CO.ProfileDialog(app, app.data["profiles"][0])
    dself.vars["ck_config"].set(f1)
    dself.vars["ovpn"].set(f2)
    dself._ok()
    check("T23.3 правка без смены имени → ок",
          dself.result is not None)
    dself.destroy()
    # managed-профиль (от админки): имя в диалоге readonly
    dm = CO.ProfileDialog(app, {"name": "u1@srvA", "managed": True})
    w = dm.grid_slaves(row=0, column=1)[0]
    check("T23.4 managed: поле имени readonly",
          str(w.cget("state")) == "readonly",
          "%s state=%s" % (w.winfo_class(), w.cget("state")))
    dm.destroy()
    app.data["profiles"] = old_prof


def t_profile_logs(app):
    """T24: журналы профилей — per-profile файлы, ротация, виджет по выбору."""
    app._log_ctx = None
    old_profiles = app.data["profiles"]
    app.data["profiles"] = [{"name": "lga"}, {"name": "lgb"}]
    app._refresh_combo("lga")
    pump(app)
    app.say("маркер lga")
    ld_a = app._log_dir("lga")
    f_a = os.path.join(ld_a, "session.log")
    check("T24.1 say() пишет в logs/<профиль>/session.log",
          os.path.isfile(f_a) and "маркер lga" in open(f_a, encoding="utf-8").read())
    ln = open(f_a, encoding="utf-8").read().splitlines()[-1]
    check("T24.2 формат JSONL",
          json.loads(ln).get("l", "").endswith("маркер lga"), ln)
    f_b = os.path.join(app._log_dir("lgb"), "session.log")
    app.combo.set("lgb"); app._sync_current(); pump(app)
    app.say("маркер lgb")
    check("T24.3 у каждого профиля свой журнал",
          os.path.isfile(f_b)
          and "маркер lgb" in open(f_b, encoding="utf-8").read()
          and "маркер lga" not in open(f_b, encoding="utf-8").read())
    app.combo.set("lga"); app._sync_current(); pump(app)
    wtxt = app.log.get("1.0", "end")
    check("T24.4 виджет перечитан с диска при переключении",
          "маркер lga" in wtxt and "маркер lgb" not in wtxt)
    with open(f_a, "a", encoding="utf-8") as fh:
        fh.write("0" * CO.LOG_MAX)     # дописываем мимо API → файл > LOG_MAX
    app._log_write("lga", "после-ротации")
    check("T24.5 ротация: session.log → session.log.old",
          os.path.isfile(f_a + ".old"))
    check("T24.6 чтение .old+текущего: старый маркер виден",
          any("маркер lga" in l for l in app._log_read("lga")))
    app._clear_log(); pump(app)
    check("T24.7 «Очистить лог» — файлы снесены, осталась метка",
          not os.path.isfile(f_a + ".old")
          and "Лог очищен" in open(f_a, encoding="utf-8").read())
    app._log_ctx = "lgb"
    app.say("строка операции lgb")
    app._log_ctx = None
    check("T24.8 _log_ctx: строка ушла в журнал операции, не видимого профиля",
          "строка операции lgb" in open(f_b, encoding="utf-8").read()
          and "строка операции lgb" not in open(f_a, encoding="utf-8").read())
    app.combo.set("lga"); app._sync_current()
    app._delete(); pump(app)
    check("T24.9 удаление профиля → logs/<name> снесён",
          not os.path.isdir(ld_a))
    app.data["profiles"] = old_profiles
    CO.save_data(app.data)   # _delete пересохранил data.json — вернуть как было
    app._refresh_combo()
    pump(app)


def t_migrate():
    ok = os.path.isfile(os.path.join(CO.BIN_DIR, "ck-client.exe")) \
        and not os.path.isdir(CO.OLD_APP_DIR) and os.path.isfile(CO.DATA_FILE)
    d = json.load(open(CO.DATA_FILE, encoding="utf-8"))
    check("T12.1 migrate_dirs: папка переехала, ck → общий bin", ok
          and d["ck_client"] == os.path.join(CO.BIN_DIR, "ck-client.exe")
          and d["profiles"][0]["ovpn"].startswith(CO.APP_DIR),
          "ck=%s" % d.get("ck_client"))


def main():
    app = CO.App()
    app.withdraw()
    pump(app)
    t_strings()
    t_lang(app)
    t_profile_dlg(app)
    t_ovpn(app)
    t_bypass(app)
    t_pids_ip_log(app)
    t_connect(app)
    t_import_files()
    t_mgmt_loop(app)
    t_stop_vpn(app)
    t_monitor(app)
    t_ports()
    t_status_warn(app)
    t_check_dlg(app)
    t_data()
    t_inbox(app)
    t_shortcut()
    t_rename(app)
    t_profile_logs(app)
    t_migrate()
    try:
        app.destroy()
    except Exception:
        pass
    print("\n=== итог: %d ok, %d FAIL ===" % (_NPASS[0], _NFAIL[0]))
    sys.exit(1 if _NFAIL[0] else 0)


if __name__ == "__main__":
    main()
