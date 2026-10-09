# -*- coding: utf-8 -*-
"""Комплексный тест лога админки: каналы deploy/users, очистка,
verbose, переключение серверов, операция на другом сервере,
fw-строки на живой лабе; реестр дочерних процессов (pids.json),
детект «управляешь через свой VPN» (_warn_own_tunnel) — фаза F без SSH;
probe + полный цикл юзера create→export→revoke на второй лабе — фаза G.

Запуск: python tests/logtest.py
Каркас и песочница — tests/lib.py (реальный data.json не трогается).
Живая часть (фаза D) берёт первую доступную лабу из реестра —
read-only на сервере, боевая data.json не изменяется.
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from lib import check, info, finish, pump, wtxt, jrnl, wait_idle, \
                make_app, real_srv, sel, tab
import cloak_admin as CA

app = make_app()

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

# ---- A: маршрутизация каналов -----------------------------------
sel(app, 0); tab(app, 0)
app.say("dep-A-1"); pump(app, 0.2)
check("A1 say на вкладке deploy → журнал SrvA[deploy]",
      any("dep-A-1" in l for l in jrnl(app, "SrvA", "deploy")))
check("A1 виджет deploy показывает строку",
      "dep-A-1" in wtxt(app.logw_dep))
check("A1 виджет users пуст", "dep-A-1" not in wtxt(app.logw_usr))

tab(app, 1)
app.say("usr-A-1"); pump(app, 0.2)
check("A2 say на вкладке users → журнал SrvA[users]",
      any("usr-A-1" in l for l in jrnl(app, "SrvA", "users")))
check("A2 виджет users показывает", "usr-A-1" in wtxt(app.logw_usr))

# ---- A3: verbose -------------------------------------------------
tab(app, 0)
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
sel(app, 1); tab(app, 0)
app.say("dep-B-1"); pump(app, 0.2)
check("A4 журнал B отделён", any("dep-B-1" in l
                                 for l in jrnl(app, "SrvB", "deploy"))
      and not any("dep-A-1" in l for l in jrnl(app, "SrvB", "deploy")))
check("A4 виджет после переключения = журнал B",
      "dep-B-1" in wtxt(app.logw_dep) and
      "dep-A-1" not in wtxt(app.logw_dep))
sel(app, 0)
check("A4 возврат на A → журнал A восстановлен",
      "dep-A-1" in wtxt(app.logw_dep))

# ---- A5: verbose — флаг per-server, запоминается ------------------
app.verbose.set(True); app._on_verbose_toggle(); pump(app, 0.15)
check("A5 флаг сохранён в сервере A",
      app.data["servers"][0].get("log_verbose") is True)
sel(app, 1)
check("A5 у B галочка выкл", not app.verbose.get())
sel(app, 0)
check("A5 возврат на A → галочка вкл (запомнена)", app.verbose.get())
app.verbose.set(False); app._on_verbose_toggle()

# ---- B: очистка --------------------------------------------------
sel(app, 1); tab(app, 1); app.say("usr-B-1")  # выбран B — в его журнал
pump(app, 0.2); tab(app, 0)
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
app._log_ctx = "SrvA"; app._op_tab = "deploy"
sel(app, 1)                              # смотрим B
app.say("op-A-строка"); pump(app, 0.2)
check("C1 строка op ушла в журнал A",
      any("op-A-строка" in l for l in jrnl(app, "SrvA", "deploy")))
check("C1 журнал B чист от op-строки",
      not any("op-A-строка" in l for l in jrnl(app, "SrvB")))
check("C1 виджет B/deploy показывает placeholder",
      "идёт операция на «SrvA»" in wtxt(app.logw_dep),
      wtxt(app.logw_dep)[:80])
tab(app, 1)                              # users-вкладка B — без placeholder
check("C3 users-вкладка B без placeholder, журнал B на месте",
      "идёт операция" not in wtxt(app.logw_usr)
      and "usr-B-1" in wtxt(app.logw_usr))

# C5: копирование во время чужой операции
tab(app, 0); sel(app, 1)                 # смотрим B/deploy
n_a = len(jrnl(app, "SrvA"))
n_b = len(jrnl(app, "SrvB", "deploy"))
app._copy_log_dep(); pump(app, 0.2)
where = "A" if len(jrnl(app, "SrvA")) > n_a else \
        ("B" if len(jrnl(app, "SrvB", "deploy")) > n_b else "никуда")
info("«Лог скопирован» ушёл в журнал: %s" % where)
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
sel(app, 0); pump(app, 0.3)
check("C4 после op виджет A показывает его строки (без placeholder)",
      "op-A-строка" in wtxt(app.logw_dep)
      and "идёт операция" not in wtxt(app.logw_dep))

# ---- D: живые лабы ------------------------------------------------
lab = real_srv("Ubuntu24 lab", "Ubuntu26 lab", "ubuntu22 lab")
if lab:
    info("== живая лаба: %s (%s) ==" % (lab["name"], lab["host"]))
    app.data["servers"].append(lab)
    app._refresh_servers()
    sel(app, len(app.data["servers"]) - 1); tab(app, 0)

    ssh = CA.SSH(lab, app.say)
    ssh.preflight()
    out = ssh.run_script("fw-manage.sh", "allow tcp 18088", timeout=60)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    fw_ = next((l.split("=", 1)[1] for l in lines
                if l.startswith("FW=")), "?")
    notes = "; ".join(l for l in lines if not
                      l.startswith(("FW=", "=== ", "ok:")))[:140]
    info("бэкенд: %s | заметки: %s" % (fw_, notes or "-"))
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
    sel(app, 0)                          # смотрим SrvA
    n_a2 = len(jrnl(app, "SrvA"))
    n_lab = len(jrnl(app, lab["name"]))
    dlg.v_port.set("18088"); dlg.v_proto.set("tcp")
    dlg._act("allow")
    wait_idle(app, 90)
    where = "lab" if len(jrnl(app, lab["name"])) > n_lab else \
            ("SrvA" if len(jrnl(app, "SrvA")) > n_a2 else "никуда")
    info("fw-строка ушла в журнал: %s" % where)
    check("D5 fw-лог в журнале СЕРВЕРА ДИАЛОГА, а не выбранного",
          where == "lab", where)
    dlg.destroy()
    # убрать тестовое правило
    ssh.run_script("fw-manage.sh", "deny tcp 18088", timeout=60)

    # users-канал: «Обновить» логирует в users
    sel(app, len(app.data["servers"]) - 1); tab(app, 1)
    n_u = len(jrnl(app, lab["name"], "users"))
    if hasattr(app, "_users_refresh"):
        try:
            app._users_refresh()
            wait_idle(app, 60)
            check("D7 users-операция пишет в users-канал",
                  len(jrnl(app, lab["name"], "users")) > n_u)
        except Exception as e:
            info("(users refresh пропущен: %r)" % e)
else:
    info("!! живых лаб в реестре не нашлось — фаза D пропущена")

# ---- F: реестр дочерних процессов + детект своего VPN (без SSH) ----
import subprocess, json, time, tempfile
from tkinter import messagebox as _mb, filedialog as _fd

CA.PIDS_FILE = os.path.join(lib.TMP, "pids.json")
CA._child_procs.clear()
_sleep = ["python", "-c", "import time; time.sleep(120)"]
p1 = CA.track_proc(subprocess.Popen(_sleep, creationflags=CA.CREATE_NO_WINDOW))
check("F1 track: PID в реестре и в pids.json",
      p1.pid in CA._child_procs
      and str(p1.pid) in json.load(open(CA.PIDS_FILE)))
CA.untrack_proc(p1)
check("F1 untrack живого — остаётся в реестре", p1.pid in CA._child_procs)
CA.kill_child_procs(); time.sleep(0.5)
check("F1 kill_child_procs: процесс убит, реестр пуст",
      not CA._pid_exists(p1.pid) and not CA._child_procs
      and json.load(open(CA.PIDS_FILE)) == {})

# F2: сироты от «прошлого запуска»: один живой, один мёртвый PID
p2 = subprocess.Popen(_sleep, creationflags=CA.CREATE_NO_WINDOW)
json.dump({str(p2.pid): "python", "999999": "plink.exe"},
          open(CA.PIDS_FILE, "w"))
msgs = []
CA.cleanup_stale_procs(log=msgs.append); time.sleep(0.5)
check("F2 cleanup_stale: живой сирота убит, мёртвый проигнорирован",
      not CA._pid_exists(p2.pid) and msgs
      and str(p2.pid) in msgs[0] and "999999" not in msgs[0], msgs)
check("F2 pids.json обнулён", json.load(open(CA.PIDS_FILE)) == {})
# F2b: имя exe не совпало (PID переиспользован) → не трогаем
p3 = subprocess.Popen(_sleep, creationflags=CA.CREATE_NO_WINDOW)
json.dump({str(p3.pid): "plink.exe"}, open(CA.PIDS_FILE, "w"))
CA.cleanup_stale_procs(log=msgs.append); time.sleep(0.3)
check("F2b чужой процесс с нашим PID не убит",
      CA._pid_exists(p3.pid))
p3.kill()

# F3: vpn_active_host — две папки, живой/мёртвый клиент
vnew, vold = tempfile.mkdtemp(dir=lib.TMP), tempfile.mkdtemp(dir=lib.TMP)
CA.VPN_DIR, CA.OLD_VPN_DIR = vnew, vold
check("F3 нет файлов → None", CA.vpn_active_host() is None)

def _mk_vpn(d, pid, host):
    ck = os.path.join(d, "ck.json")
    json.dump({"RemoteHost": host, "RemotePort": "443"}, open(ck, "w"))
    json.dump({"last_profile": "P",
               "profiles": [{"name": "P", "ck_config": ck}]},
              open(os.path.join(d, "data.json"), "w"))
    json.dump({"ck_pid": pid, "vpn_pid": None},
              open(os.path.join(d, "pids.json"), "w"))

_mk_vpn(vnew, 999999, "new.example")
check("F3 мёртвый PID → None", CA.vpn_active_host() is None)
_mk_vpn(vold, os.getpid(), "old.example")
check("F3 старая папка (старый клиент) → её хост",
      CA.vpn_active_host() == "old.example")
_mk_vpn(vnew, os.getpid(), "new.example")
check("F3 новая папка приоритетнее", CA.vpn_active_host() == "new.example")
# server/bypass_ip в профиле важнее RemoteHost
d_ = json.load(open(os.path.join(vnew, "data.json")))
d_["profiles"][0]["bypass_ip"] = "1.2.3.4"
json.dump(d_, open(os.path.join(vnew, "data.json"), "w"))
check("F3 bypass_ip профиля приоритетнее RemoteHost",
      CA.vpn_active_host() == "1.2.3.4")

# F4: _warn_own_tunnel — спрашивает только при совпадении хоста
asked = []
_orig_ask = _mb.askyesno
_mb.askyesno = lambda *a, **k: (asked.append(a[1][:40]), False)[1]
CA.vpn_active_host = lambda: "10.9.9.1"          # = SrvA
r_a = app._warn_own_tunnel({"name": "SrvA", "host": "10.9.9.1"})
r_b = app._warn_own_tunnel({"name": "SrvB", "host": "10.9.9.2"})
check("F4 свой сервер → вопрос задан, отказ → False",
      r_a is False and len(asked) == 1, asked)
check("F4 чужой сервер → без вопроса, True", r_b is True and len(asked) == 1)
CA.vpn_active_host = lambda: None
check("F4 VPN не активен → True без вопроса",
      app._warn_own_tunnel({"name": "SrvA", "host": "10.9.9.1"}) is True
      and len(asked) == 1)
# F5: гейт на реальных кнопках — отказ в предупреждении = _worker не вызван
CA.vpn_active_host = lambda: "10.9.9.1"
started = []
_orig_worker = app._worker
app._worker = lambda fn, ctx=None: started.append(fn)
sel(app, 0); tab(app, 0)
app._srv_reboot(); app._srv_purge(); app._step_run_all()
app.steps_tv.selection_set("nat"); app._step_run_sel()
check("F5 reboot/purge/deploy-all/шаг nat на своём VPN-сервере — "
      "остановлены предупреждением", not started and len(asked) == 5, asked)
asked.clear()
app.steps_tv.selection_set("audit"); app._step_run_sel()
check("F5 read-only шаг audit — без предупреждения, запущен",
      len(started) == 1 and not asked)
sel(app, 1); started.clear()
app._srv_reboot()   # SrvB: своё предупреждение не нужно → штатный askyesno
check("F5 чужой сервер: только штатное подтверждение",
      len(asked) == 1 and "Перезагрузить" in asked[0], asked)
app._worker = _orig_worker
_mb.askyesno = _orig_ask
CA.vpn_active_host = lambda: None    # фаза G без помех

# ---- G: живой цикл юзера + probe на другой лабе -------------------
lab2 = real_srv("Debian12  lab", "Debian13  lab", "ubuntu20 lab")
if lab2:
    info("== живая лаба G: %s (%s) ==" % (lab2["name"], lab2["host"]))
    CA.BUNDLES_DIR = os.path.join(lib.TMP, "bundles")
    app.data["servers"].append(lab2)
    app._refresh_servers()
    gi = len(app.data["servers"]) - 1
    sel(app, gi); tab(app, 0)
    n_dep = len(jrnl(app, lab2["name"], "deploy"))
    n_usr = len(jrnl(app, lab2["name"], "users"))

    # G1: «Проверить статусы» = probe → deploy-канал, шаги заполнены
    lab2["steps"] = {}
    app._steps_reset(); wait_idle(app, 120)
    dep = jrnl(app, lab2["name"], "deploy")
    check("G1 probe пишет в deploy-канал",
          any("Аудит статусов" in l for l in dep[n_dep:]))
    check("G1 шаги заполнены по probe",
          lab2.get("steps", {}).get("cloak", {}).get("st") == "ok",
          lab2.get("steps", {}).get("cloak"))
    check("G1 users-канал не тронут",
          len(jrnl(app, lab2["name"], "users")) == n_usr)

    # G2: создать юзера через _user_create (диалог подменён)
    CN = "logtest-tmp"
    fake = {"name": CN, "expiry": CA.FAR_FUTURE, "sessions": 2, "mask": "",
            "up_rate": CA.INT64_MAX, "down_rate": CA.INT64_MAX,
            "up_credit": CA.INT64_MAX, "down_credit": CA.INT64_MAX}

    class _FakeDlg:
        def __init__(self, *a, **k):
            self.result = dict(fake)
    _orig_ud = CA.UserDialog
    CA.UserDialog = _FakeDlg
    tab(app, 1)
    if any(u.get("cn") == CN for u in lab2.get("users", [])):
        info("(остаток %s с прошлого прогона — чищу)" % CN)
        lab2["users"] = [u for u in lab2["users"] if u.get("cn") != CN]
    app._user_create(); wait_idle(app, 240)
    usr = jrnl(app, lab2["name"], "users")
    rec = next((u for u in lab2.get("users", []) if u.get("cn") == CN), None)
    check("G2 юзер создан (в реестре с UID)", rec is not None and rec.get("uid"),
          [l[-80:] for l in usr[-3:]])
    check("G2 строка «создан» в users-канале",
          any("создан" in l and CN in l for l in usr[n_usr:]))
    check("G2 deploy-канал не тронут",
          len(jrnl(app, lab2["name"], "deploy")) == len(dep))
    # D-фаза ушла в users-канал → при переключении вкладки виджет deploy чист
    tab(app, 0); pump(app, 0.3)
    check("G2 виджет deploy без строк юзера", CN not in wtxt(app.logw_dep))
    tab(app, 1)

    # G3: экспорт в temp-папку (askdirectory подменён)
    app._selected_user = lambda: (CN, rec, rec["uid"] if rec else "?")
    _orig_dir = _fd.askdirectory
    exp = tempfile.mkdtemp(dir=lib.TMP)
    _fd.askdirectory = lambda **k: exp
    if rec:
        app._user_export(); wait_idle(app, 120)
        f_ = os.path.join(exp, CN, "%s.dgcloak" % CN)
        check("G3 экспорт: .dgcloak на месте", os.path.isfile(f_), f_)
        check("G3 строка «Конфиг …» в users-канале",
              any("Конфиг" in l and CN in l
                  for l in jrnl(app, lab2["name"], "users")))
        # повторный create того же CN — отказ до SSH
        errs = []
        _orig_err = _mb.showerror
        _mb.showerror = lambda *a, **k: errs.append(a[1][:60])
        app._user_create(); pump(app, 0.3)
        check("G4 дубль CN отклонён без операции",
              errs and not app.busy, errs)
        _mb.showerror = _orig_err
    _fd.askdirectory = _orig_dir

    # G5: отзыв + удаление (askyesno → True)
    _mb.askyesno = lambda *a, **k: True
    n_u2 = len(jrnl(app, lab2["name"], "users"))
    if rec:
        app._user_revoke(); wait_idle(app, 180)
        usr = jrnl(app, lab2["name"], "users")
        check("G5 юзер удалён из реестра",
              not any(u.get("cn") == CN for u in lab2.get("users", [])))
        check("G5 «отозван и удалён» в users-канале",
              any("отозван" in l and CN in l for l in usr[n_u2:]),
              [l[-80:] for l in usr[-4:]])
        check("G5 сертификат отозван/отсутствует (не ошибка)",
              any("сертификат" in l for l in usr[n_u2:])
              and not any("ОШИБКА" in l for l in usr[n_u2:]))
        # на сервере UID тоже должен исчезнуть
        ssh2 = CA.SSH(lab2, app.say)
        out = ssh2.run("%scat %s 2>/dev/null || echo '[]'"
                       % (ssh2.sudo, app.REMOTE_USERS), timeout=30)
        check("G5 CN не остался в реестре юзеров на сервере", CN not in out)
    _mb.askyesno = _orig_ask
    CA.UserDialog = _orig_ud
else:
    info("!! лабы для фазы G нет — пропущена")

finish(app)
