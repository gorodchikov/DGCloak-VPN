# -*- coding: utf-8 -*-
"""Авто-тесты клиента DGCloak VPN (cloak_ovpn.py).

Песочница:
- весь App живёт в tempdir: APP_DIR/DATA_FILE/PROFILES_DIR/PIDS_FILE/BIN_DIR подменены;
- трей, поиск бинарников и первый запуск отключены; сеть/процессы — фейки;
- реальный %APPDATA%\\DGCloak не трогаем; ничего не подключаем и не ставим.

Запуск: python tests\\clienttest.py   (выход 0 — всё зелёное)
"""
import sys, os, io, re, ast, json, time, tempfile, threading

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
    check("T3.1 при смене языка — предупреждение про лог",
          bool(MSGBOX) and MSGBOX[-1][0] == "info"
          and "remain" in str(MSGBOX[-1][1]) or "previous language" in str(MSGBOX[-1][1]),
          MSGBOX[-1][1] if MSGBOX else "no msgbox")
    check("T3.2 язык сохранён в data.json",
          json.load(open(CO.DATA_FILE, encoding="utf-8"))["language"] == "en")
    app.data["language"] = "ru"
    app.lang_combo.set("Русский")


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
    d._ok()
    r = d.result
    ok = r and r["ck_config"].startswith(CO.PROFILES_DIR) and os.path.isfile(r["ovpn"])
    check("T4.3 .dgcloak материализован в profiles\\", bool(ok), r)
    check("T4.4 UDP поднят из cloak.UDP", r and r["udp"] is True)

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
              and "Внешний IP через VPN: 2.2.2.2" in new_log)
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
              "не определён" in new_log)
        check("T11.7 без UDP в конфиге и профиле → нет -u",
              "-u" not in CALLS[0])

        # split tunnel: redirect-gateway отсутствует
        CALLS.clear()
        app._connect(dict(p, udp=False, full_tunnel=False, bypass_ip="8.8.8.8"))
        check("T11.8 split tunnel → нет redirect-gateway, bypass есть",
              "--redirect-gateway" not in CALLS[1] and "8.8.8.8" in CALLS[1])
        app._stop_all()
    finally:
        for k, v in saved.items():
            setattr(CO, k, v)
        CO.subprocess.Popen = saved_popen
        CO.App._mgmt_loop = saved_mgmt
        CO.App._start_monitor = saved_mon


# ---------- T12. миграция старой папки ------------------------------------

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
    t_migrate()
    try:
        app.destroy()
    except Exception:
        pass
    print("\n=== итог: %d ok, %d FAIL ===" % (_NPASS[0], _NFAIL[0]))
    sys.exit(1 if _NFAIL[0] else 0)


if __name__ == "__main__":
    main()
