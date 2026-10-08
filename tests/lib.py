# -*- coding: utf-8 -*-
"""Общий каркас авто-тестов DGCloakAdmin.

Правила песочницы:
- реальный %APPDATA%\\DGCloakAdmin\\data.json — read-only;
- тестовый App живёт в tempdir (APP_DIR/DATA_FILE подменены);
- депс-чек отключён — ничего не качаем и не ставим;
- окна tkinter реальные, но невидимые (withdraw);
- на prod-стендах (labs.json: prod=true) — только read-only действия.

Выходной код: 0 — все check() зелёные, 1 — были FAIL.
"""
import sys, os, json, time, tempfile

try:  # кириллица в именах чеков/серверов — не упасть на cp1252-консоли
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import cloak_admin as CA

TMP = tempfile.mkdtemp(prefix="dgtest-")
CA.APP_DIR = TMP
CA.DATA_FILE = os.path.join(TMP, "data.json")
CA.App._ensure_deps = lambda self: None   # ничего не качаем

_NPASS = [0]
_NFAIL = [0]


def check(name, cond, extra=""):
    if cond:
        _NPASS[0] += 1
    else:
        _NFAIL[0] += 1
    print(("PASS " if cond else "FAIL ") + name +
          (" | " + str(extra)[:200] if extra else ""))


def info(msg):
    print("    " + msg)


def finish(app=None):
    if app is not None:
        try:
            app.destroy()
        except Exception:
            pass
    print("\n=== итог: %d ok, %d FAIL ===" % (_NPASS[0], _NFAIL[0]))
    sys.exit(1 if _NFAIL[0] else 0)


# ---- tkinter-помощники -------------------------------------------------

def pump(app, t=0.4):
    """Прокрутить событийный цикл: uiq/_drain/after отрабатывают."""
    end = time.time() + t
    while time.time() < end:
        try:
            app.update()
        except Exception:
            pass
        time.sleep(0.03)


def wtxt(w):
    return w.get("1.0", "end")


def jrnl(app, name, tab=None):
    """Журнал сервера: [(строка)], фильтр по каналу deploy|users."""
    return [l for l, vb, t in app._logs.get(name, [])
            if tab is None or t == tab]


def wait_idle(app, t=60):
    end = time.time() + t
    while app.busy and time.time() < end:
        pump(app, 0.2)


def make_app():
    app = CA.App()
    app.withdraw()
    pump(app, 0.2)
    return app


def add_srv(app, srv):
    app.data["servers"].append(srv)
    app._refresh_servers()


def sel(app, i):
    app.srv_list.selection_clear(0, "end")
    app.srv_list.selection_set(i)
    app._on_srv_select()
    pump(app, 0.15)


def tab(app, i):
    app.nb.select(i)
    pump(app, 0.1)


# ---- данные --------------------------------------------------------------

def real_data():
    """Реальный реестр админки — read-only источник записей стендов."""
    for p in (os.path.join(CA.DGCLOAK_DIR, "Admin", "data.json"),
              os.path.join(os.environ["APPDATA"], "DGCloakAdmin", "data.json")):
        if os.path.isfile(p):
            return json.load(open(p, encoding="utf-8"))
    raise FileNotFoundError("реальный реестр админки не найден")


def real_srv(*names):
    """Первый сервер из реального реестра по одному из имён (копия dict)."""
    for s in real_data()["servers"]:
        if s["name"] in names:
            return dict(s)
    return None


def labs():
    """Матрица стендов из tests/labs.json."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labs.json")
    return json.load(open(p, encoding="utf-8"))


def supported_os():
    """Карта поддерживаемых ОС из _step_audit — единый источник правды."""
    import inspect, ast, re as _re
    src = inspect.getsource(CA.App._step_audit)
    m = _re.search(r"supported\s*=\s*(\{.*?\}\})", src, _re.S)
    return ast.literal_eval(m.group(1)) if m else {}
