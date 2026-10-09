# -*- coding: utf-8 -*-
"""Матричный смоук по стендам из tests/labs.json.

Для каждого стенда с auto=true (read-only, ничего не меняет):
  M1  запись есть в реестре админки
  M2  SSH + sudo (preflight)
  M3  distro/version по detect.sh = ожиданию из матрицы
  M4  fw-бэкенд по fw-manage.sh ports = ожиданию (если задан)
  M5  STEP_cloak из probe.sh = ожиданию deployed (если задано)
  M6  флаг deployed в реестре = реальности по probe
  M7  (id, version) покрыты картой supported в _step_audit —
      то есть дистрибутив стенда вообще поддерживается деплоем
  M8  локализация probe: сырой вывод без кириллицы (токены), а заметки
      после _apply_probe — на языке UI (en → ASCII, ru → кириллица)

Запуск: python tests/matrix.py
Новый дистрибутив: VM + запись в labs.json + supported в _step_audit →
этот файл — первый прогон, дальше ручной блок B из docs/test-plan.md.
"""
import sys, os, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from lib import check, info, finish, labs, real_srv, supported_os
import cloak_admin as CA

sup = supported_os()
info("supported в _step_audit: %s" % sup)

app = lib.make_app()  # для M8 (разбор probe-вывода через _apply_probe)

for lb in labs()["labs"]:
    if not lb.get("auto"):
        info("-- %s: auto=false, пропуск (%s)"
             % (lb["match"], lb.get("note", "-")))
        continue
    name = lb["match"]
    tag = "%s%s" % (name, " [PROD]" if lb.get("prod") else "")
    print("\n== %s ==" % tag)

    # M1 запись в реестре
    s = real_srv(name)
    check("%s: M1 запись в реестре" % tag, s is not None)
    if not s:
        continue

    # M2 SSH + sudo
    try:
        ssh = CA.SSH(s, lambda m: None)
        ssh.preflight()
        check("%s: M2 SSH+sudo (%s)" % (tag, s["host"]), True)
    except Exception as e:
        check("%s: M2 SSH+sudo (%s)" % (tag, s["host"]), False, e)
        continue

    # M3 дистрибутив
    if lb.get("distro"):
        try:
            out = ssh.run_script("detect.sh", timeout=60)
            ossec = CA.parse_section(out, "OS")
            mi = re.search(r"ID=(\S+)", ossec)
            mv = re.search(r"VERSION=(\S+)", ossec)
            got = (mi.group(1) if mi else "?",
                   (mv.group(1) if mv else "?").strip('"'))
            check("%s: M3 distro %s/%s" % (tag, *lb["distro"]),
                  got == tuple(lb["distro"]), got)
            # M7 поддерживаемость
            check("%s: M7 в карте supported" % tag,
                  got[0] in sup and got[1] in sup.get(got[0], set()), got)
        except Exception as e:
            check("%s: M3 distro" % tag, False, e)

    # M4 fw-бэкенд
    if lb.get("fw") is not None:
        try:
            out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
            fw = next((l[3:] for l in out.splitlines()
                       if l.startswith("FW=")), "?")
            check("%s: M4 fw=%s" % (tag, lb["fw"]), fw == lb["fw"], fw)
        except Exception as e:
            check("%s: M4 fw" % tag, False, e)
    else:
        try:
            out = ssh.run_script("fw-manage.sh", "ports", timeout=60)
            fw = next((l[3:] for l in out.splitlines()
                       if l.startswith("FW=")), "?")
            info("%s: M4 фактический fw=%s (ожидание не задано)" % (tag, fw))
        except Exception as e:
            info("%s: M4 fw не определён: %s" % (tag, e))

    # M5/M6 deployed по probe
    if lb.get("deployed") is not None:
        try:
            out = ssh.run_script("probe.sh", timeout=120)
            m = re.search(r"===STEP_cloak===\s*\n(\w+)\|", out)
            real_dep = (m.group(1) == "ok") if m else None
            check("%s: M5 deployed=%s" % (tag, lb["deployed"]),
                  real_dep == lb["deployed"], m.group(1) if m else "нет STEP")
            check("%s: M6 реестр=реальность" % tag,
                  bool(s.get("deployed")) == bool(real_dep),
                  "реестр=%s, probe=%s" % (s.get("deployed"), real_dep))

            # M8 локализация: probe.sh отдаёт машинные токены (ASCII),
            # текст заметки рождается в _apply_probe на языке UI —
            # регрессия «EN-интерфейс + RU-заметки» (Montreal).
            cyr_raw = [l for l in out.splitlines()
                       if re.search(r"[а-яА-ЯёЁ]", l)]
            check("%s: M8 сырой probe без кириллицы" % tag,
                  not cyr_raw, cyr_raw[:2] or "ascii-only")
            for lang, want_cyr in (("en", False), ("ru", True)):
                app.lang = lang
                s2 = {"name": "%s-m8-%s" % (name, lang), "steps": {}}
                app._apply_probe(s2, out)
                notes = [r["note"] for r in s2["steps"].values()]
                cyr = [n for n in notes if re.search(r"[а-яА-ЯёЁ]", n)]
                if lang == "en":
                    check("%s: M8 en-заметки без кириллицы" % tag,
                          not cyr, cyr[:2] or "ok")
                else:  # ru — хотя бы одна заметка с кириллицей
                    check("%s: M8 ru-заметки по-русски" % tag,
                          bool(cyr), notes[:2])
            app.lang = "ru"
        except Exception as e:
            check("%s: M5/M6 probe" % tag, False, e)

finish(app)
