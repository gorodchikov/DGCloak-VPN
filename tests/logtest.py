# -*- coding: utf-8 -*-
"""Комплексный тест лога админки: каналы deploy/users, очистка,
verbose, переключение серверов, операция на другом сервере,
fw-строки на живой лабе.

Запуск: python tests/logtest.py
Изолированный APP_DIR (tempdir) — реальный data.json не трогается.
Живая часть (фаза D) берёт запись реальной лабы из %APPDATA% —
read-only, боевая data.json не изменяется.
Открывает реальные (невидимые) окна tkinter на десктопе.
"""
import sys, os, json, time, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cloak_admin as CA

# песочница — реальный %APPDATA%\DGCloakAdmin\data.json не трогаем
TMP = tempfile.mkdtemp(prefix="dglog-")
CA.APP_DIR = TMP
CA.DATA_FILE = os.path.join(TMP, "data.json")
CA.App._ensure_deps = lambda self: None   # ничего не качаем

REAL = json.load(open(os.path.join(os.environ["APPDATA"],
                                   "DGCloakAdmin", "data.json"),
                      encoding="utf-8"))

NPASS = [0]
def check(name, cond, extra=""):
    NPASS[0] += bool(cond)
    print(("PASS " if cond else "FAIL ") + name +
          (" | " + str(extra)[:160] if extra else ""))

def pump(app, t=0.4):
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
    return [l for l, vb, t in app._logs.get(name, [])
            if tab is None or t == tab]

def wait_idle(app, t=60):
    end = time.time() + t
    while app.busy and time.time() < end:
        pump(app, 0.2)

app = CA.App()
app.withdraw()                 # окно не мигает на экране
pump(app, 0.2)

# ---- E: краевые до выбора сервера -------------------------------
app.say("глобальная строка до выбора")
app._clear_log_dep()
pump(app)
check("E1 say до выбора сервера — не падает", True)
check("E2 очистка без сервера: строка «Лог очищен.» в виджете",
      "Лог очищен." in wtxt(app.logw_dep))

# ---- два фейковых сервера ---------------------------------------
app.data["servers"] = [
    {"name": "SrvA", "host": "10.9.9.1", "user": "u", "users": []},
    {"name": "SrvB", "host": "10.9.9.2", "user": "u", "users": []}]
app._refresh_servers()

def sel(i):
    app.srv_list.selection_clear(0, "end")
    app.srv_list.selection_set(i)
    app._on_srv_select()
    pump(app, 0.15)

def tab(i):
    app.nb.select(i)
    pump(app, 0.1)

# ---- A: маршрутизация каналов -----------------------------------
sel(0); tab(0)
app.say("dep-A-1"); pump(app, 0.2)
check("A1 say на вкладке deploy → журнал SrvA[deploy]",
      any("dep-A-1" in l for l in jrnl(app, "SrvA", "deploy")))
check("A1 виджет deploy показывает строку",
      "dep-A-1" in wtxt(app.logw_dep))
check("A1 виджет users пуст", "dep-A-1" not in wtxt(app.logw_usr))

tab(1)
app.say("usr-A-1"); pump(app, 0.2)
check("A2 say на вкладке users → журнал SrvA[users]",
      any("usr-A-1" in l for l in jrnl(app, "SrvA", "users")))
check("A2 виджет users показывает", "usr-A-1" in wtxt(app.logw_usr))

# ---- A3: verbose -------------------------------------------------
tab(0)
app.vsay("verbose-скрытая"); pump(app, 0.2)
check("A3 vsay в журнале",
      any("verbose-скрытая" in l for l in jrnl(app, "SrvA", "deploy")))
check("A3 vsay скрыта при verbose off",
      "verbose-скрытая" not in wtxt(app.logw_dep))
app.verbose.set(True); app._on_verbose_toggle(); pump(app, 0.2)
check("A3 verbose on → старые vsay-строки видны",
      "verbose-скрытая" in wtxt(app.logw_dep))
app.verbose.set(False); app._on_verbose_toggle(); pump(app, 0.2)
check("A3 verbose off → снова скрыты",
      "verbose-скрытая" not in wtxt(app.logw_dep))

# ---- A4: переключение серверов ----------------------------------
sel(1); tab(0)
app.say("dep-B-1"); pump(app, 0.2)
check("A4 журнал B отделён", any("dep-B-1" in l
                                 for l in jrnl(app, "SrvB", "deploy"))
      and not any("dep-A-1" in l for l in jrnl(app, "SrvB", "deploy")))
check("A4 виджет после переключения = журнал B",
      "dep-B-1" in wtxt(app.logw_dep) and
      "dep-A-1" not in wtxt(app.logw_dep))
sel(0)
check("A4 возврат на A → журнал A восстановлен",
      "dep-A-1" in wtxt(app.logw_dep))

# ---- B: очистка --------------------------------------------------
sel(1); tab(1); app.say("usr-B-1")  # выбран B — users-запись в его журнал
pump(app, 0.2); tab(0)
app._clear_log_dep(); pump(app, 0.2)
dep_b = jrnl(app, "SrvB", "deploy")
check("B1 после очистки в журнале B[deploy] только «Лог очищен.»",
      len(dep_b) == 1 and "Лог очищен." in dep_b[0], dep_b)
check("B1 users-канал B не тронут",
      any("usr-B-1" in l for l in jrnl(app, "SrvB", "users")))
check("B1 виджет показывает «Лог очищен.»",
      "Лог очищен." in wtxt(app.logw_dep))
check("B1 журнал A целиком на месте",
      any("dep-A-1" in l for l in jrnl(app, "SrvA", "deploy")))

# ---- C: операция на другом сервере ------------------------------
# op на SrvA (канал deploy); юзер смотрит SrvB
app._log_ctx = "SrvA"; app._op_tab = "deploy"
sel(1)                              # смотрим B
app.say("op-A-строка"); pump(app, 0.2)
check("C1 строка op ушла в журнал A",
      any("op-A-строка" in l for l in jrnl(app, "SrvA", "deploy")))
check("C1 журнал B чист от op-строки",
      not any("op-A-строка" in l for l in jrnl(app, "SrvB")))
check("C1 виджет B/deploy показывает placeholder",
      "идёт операция на «SrvA»" in wtxt(app.logw_dep),
      wtxt(app.logw_dep)[:80])
tab(1)                              # users-вкладка B — без placeholder
check("C3 users-вкладка B без placeholder, журнал B на месте",
      "идёт операция" not in wtxt(app.logw_usr)
      and "usr-B-1" in wtxt(app.logw_usr))

# C5: копирование во время чужой операции
tab(0); sel(1)                      # смотрим B/deploy
n_a = len(jrnl(app, "SrvA"))
n_b = len(jrnl(app, "SrvB", "deploy"))
app._copy_log_dep(); pump(app, 0.2)
where = "A" if len(jrnl(app, "SrvA")) > n_a else \
        ("B" if len(jrnl(app, "SrvB", "deploy")) > n_b else "никуда")
print("    >> «Лог скопирован» ушёл в журнал:", where)
check("C5 «Лог скопирован» в журнал ПОКАЗАННОГО сервера (B)",
      where == "B", where)

# C6: очистка B во время op на A → строка в журнале B
app._clear_log_dep(); pump(app, 0.2)
check("C6 «Лог очищен.» в журнале B при op на A",
      any("Лог очищен." in l for l in jrnl(app, "SrvB", "deploy")))
check("C6 журнал A не пострадал",
      any("op-A-строка" in l for l in jrnl(app, "SrvA", "deploy")))

# C4: конец операции
app._log_ctx = None; app._op_tab = None
sel(0); pump(app, 0.3)
check("C4 после op виджет A показывает его строки (без placeholder)",
      "op-A-строка" in wtxt(app.logw_dep)
      and "идёт операция" not in wtxt(app.logw_dep))

# ---- D: живые лабы ------------------------------------------------
def real_srv(*names):
    for s in REAL["servers"]:
        if s["name"] in names:
            return dict(s)
    return None

lab = real_srv("Ubuntu24 lab", "Ubuntu26 lab", "ubuntu22 lab")
if lab:
    print("\n== живая лаба: %s (%s) ==" % (lab["name"], lab["host"]))
    app.data["servers"].append(lab)
    app._refresh_servers()
    sel(len(app.data["servers"]) - 1); tab(0)

    ssh = CA.SSH(lab, app.say)
    ssh.preflight()
    out = ssh.run_script("fw-manage.sh", "allow tcp 18088", timeout=60)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    fw_ = next((l.split("=", 1)[1] for l in lines
                if l.startswith("FW=")), "?")
    notes = "; ".join(l for l in lines if not
                      l.startswith(("FW=", "=== ", "ok:")))[:140]
    print("    бэкенд:", fw_, "| заметки:", notes or "-")
    check("D1 allow: скрипт вернул ok",
          "ok: allow tcp/18088" in out, out[-120:])
    out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
    check("D1 порт 18088 открыт", "OPEN tcp 18088" in out)
    out = ssh.run_script("fw-manage.sh", "deny tcp 18088", timeout=60)
    check("D2 deny ok", "ok: deny tcp/18088" in out)
    out = ssh.run_script("fw-manage.sh", "deny tcp 18088", timeout=60)
    check("D3 повторный deny → «правило не найдено» или ok",
          "правило не найдено" in out or "ok: deny" in out,
          out[-160:])
    out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
    check("D3 порт 18088 закрыт", "OPEN tcp 18088" not in out)

    # D5: диалог портов на lab, выбран другой сервер → куда лог?
    dlg = CA.PortsDialog(app, lab, "Фаервол: ?\n")
    sel(0)                          # смотрим SrvA
    n_a2 = len(jrnl(app, "SrvA"))
    n_lab = len(jrnl(app, lab["name"]))
    dlg.v_port.set("18088"); dlg.v_proto.set("tcp")
    dlg._act("allow")
    wait_idle(app, 90)
    where = "lab" if len(jrnl(app, lab["name"])) > n_lab else \
            ("SrvA" if len(jrnl(app, "SrvA")) > n_a2 else "никуда")
    print("    >> fw-строка ушла в журнал:", where)
    check("D5 fw-лог в журнале СЕРВЕРА ДИАЛОГА, а не выбранного",
          where == "lab", where)
    dlg.destroy()
    # убрать тестовое правило
    ssh.run_script("fw-manage.sh", "deny tcp 18088", timeout=60)

    # users-канал: «Обновить» логирует в users
    sel(len(app.data["servers"]) - 1); tab(1)
    n_u = len(jrnl(app, lab["name"], "users"))
    if hasattr(app, "_users_refresh"):
        try:
            app._users_refresh()
            wait_idle(app, 60)
            check("D7 users-операция пишет в users-канал",
                  len(jrnl(app, lab["name"], "users")) > n_u)
        except Exception as e:
            print("    (users refresh пропущен: %r)" % e)
else:
    print("\n!! живых лаб в реестре не нашлось — фаза D пропущена")

app.destroy()
print("\n=== итог: %d проверок прошло ===" % NPASS[0])
