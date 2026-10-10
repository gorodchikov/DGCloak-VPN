# -*- coding: utf-8 -*-
"""Офлайн-юниты админки DGCloakAdmin (cloak_admin.py) — без SSH и сети.

Покрытие:
  U1  аудит локализации: все кириллические литералы (не docstring) ∈ STRINGS_EN,
      паритет плейсхолдеров %s/%d и {kw} между ключом и значением
  U2  регрессии: self.t(title) в _run_step; DG_LANG в run_script/_stream и во
      всех вызовах ovpn-mgmt.py; ни одной команды со вторым `sudo` внутри;
      ни одного `| grep -q` в скриптах с `pipefail` (SIGPIPE → rc 141)
  U3  скрипты: кириллический вывод идёт через хелпер _(), хелпер определён
  U4  SSH._argv / sudo-префикс по режимам (pw/nopasswd/root), -n без пароля
  U5  run_script: команда содержит env DG_LANG= и один sudo-префикс (mock _spawn)
  U6  чистые функции: parse_section, _probe_note, _apply_probe, make_ckclient,
      make_ovpn, valid_cn, uid_to_b64url, _fmt_*
  U7  диалоги: ServerDialog.validate (.pub→priv, auth, порт), UserDialog
      (CN-регекс, числа, apply → FAR_FUTURE/INT64_MAX/sessions>=1)
  U8  labs.json: все distro ∈ supported-карте _step_audit

Запуск: python tests\\admintest.py   (выход 0 — всё зелёное)
"""
import sys, os, re, ast, json, inspect, tempfile, io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from lib import check, info, finish, pump, make_app, labs, supported_os
import cloak_admin as CA

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = make_app()

# ======================================================================
# U1. Аудит локализации cloak_admin.py
# ======================================================================

def u1_localization():
    src = open(os.path.join(REPO, "cloak_admin.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    en, docids = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) \
                and getattr(node.targets[0], "id", "") == "STRINGS_EN":
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                    en[k.value] = v.value
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            if node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant):
                docids.add(id(node.body[0].value))
    cyr = re.compile("[а-яА-ЯёЁ]")
    skip = {"Русский",
            "перезагруз",   # поисковый токен в _apply_probe, не выводится
            }
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docids and cyr.search(node.value):
            s = node.value.strip()
            # ключи STRINGS_EN сохраняют ведущие пробелы — сравниваем raw
            if len(s) > 3 and node.value not in en and s not in en \
                    and s not in skip:
                missing.append(node.value)
    check("U1.1 все RU-строки имеют EN-перевод", not missing,
          "; ".join(repr(m[:70]) for m in missing[:6]))

    ph_k = re.compile(r"\{(\w+)\}")
    ph_p = re.compile(r"%[ds]")
    bad = []
    for k, v in en.items():
        if set(ph_k.findall(k)) != set(ph_k.findall(v)) \
                or len(ph_p.findall(k)) != len(ph_p.findall(v)):
            bad.append(k)
    check("U1.2 плейсхолдеры {kw} и %s совпадают RU↔EN", not bad,
          "; ".join(repr(b[:70]) for b in bad[:6]))

# ======================================================================
# U2. Регрессии (баги, чиненные вручную — больше не должны вернуться)
# ======================================================================

def u2_regressions():
    # self.t(title) — заголовок шага переводится (коммит 7a7c109)
    body = inspect.getsource(CA.App._run_step)
    check("U2.1 _run_step переводит заголовок (self.t(title))",
          "self.t(title)" in body)
    # каждый STEPS-заголовок покрыт STRINGS_EN (en-значение без кириллицы;
    # заголовок из чисто-английских терминов может законно совпасть с ключом)
    app.lang = "en"
    raw = [t for _, t in CA.App.STEPS
           if re.search(r"[а-яА-ЯёЁ]", app.t(t))]
    check("U2.2 все заголовки STEPS переводятся в en", not raw, raw)
    app.lang = "ru"

    src = open(os.path.join(REPO, "cloak_admin.py"), encoding="utf-8").read()
    # DG_LANG: run_script и run_script_stream прокидывают env DG_LANG=
    check("U2.3 run_script/run_script_stream ставят env DG_LANG=",
          src.count("env DG_LANG=%s") >= 2)
    # все прямые вызовы ovpn-mgmt.py (python3 …) — с env DG_LANG=
    mgmt_lines = [l for l in src.splitlines()
                  if re.search(r'python3?\s+/?[\w/.-]*ovpn-mgmt\.py', l)
                  and "env DG_LANG=" not in l]
    check("U2.4 все вызовы ovpn-mgmt.py с env DG_LANG=",
          not mgmt_lines, mgmt_lines[:2])
    # ни одной remote-команды со вторым sudo (pw-режим: пароль читается раз)
    multisudo = [l.strip() for l in src.splitlines()
                 if re.search(r"\.run\(", l) and re.search(r"sudo", l)
                 and l.count("sudo") > 2]
    # отдельно: нет второго sudo ВНУТРИ командных строк (' sudo ' без self.sudo)
    inner = [m.group(0) for m in re.finditer(
        r'ssh\.run\((?:"[^"]*"|f"[^"]*")', src)
        if re.search(r'"[^"]*\bsudo\b[^"]*\bsudo\b', m.group(0))]
    check("U2.5 нет remote-команд с несколькими sudo в одной строке",
          not inner, inner[:2])

def u2_pipefail_scripts():
    """| grep -q в pipefail-скрипте → SIGPIPE (141) у производителя."""
    sdir = os.path.join(REPO, "scripts")
    bad = []
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith(".sh"):
            continue
        txt = open(os.path.join(sdir, fn), encoding="utf-8").read()
        if "pipefail" not in txt:
            continue
        for ln in txt.splitlines():
            if re.search(r"\|\s*grep\s+-q", ln):
                bad.append("%s: %s" % (fn, ln.strip()[:70]))
    check("U2.6 в pipefail-скриптах нет `| grep -q`", not bad, bad[:3])

# ======================================================================
# U3. Скрипты: локализация через _()
# ======================================================================

def u3_scripts():
    sdir = os.path.join(REPO, "scripts")
    cyr = re.compile("[а-яА-ЯёЁ]")
    no_helper, raw_echo = [], []
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith((".sh", ".py")):
            continue
        txt = open(os.path.join(sdir, fn), encoding="utf-8").read()
        # хелпер нужен только если кириллица реально выводится
        # (echo/printf/print), а не в комментариях
        has_cyr_out = any(
            cyr.search(l) and re.match(
                r"\s*(echo|printf|print|_|_say)\b", l)
            for l in txt.splitlines())
        if not has_cyr_out:
            continue
        if fn.endswith(".sh") and "_() {" not in txt:
            no_helper.append(fn)
        # голый echo/printf с кириллицей — не через _()
        for ln in txt.splitlines():
            ls = ln.strip()
            if re.match(r'^(echo|printf)\s+["\']', ls) and cyr.search(ls):
                raw_echo.append("%s: %s" % (fn, ls[:60]))
    check("U3.1 скрипты с кириллицей имеют хелпер _()", not no_helper, no_helper)
    check("U3.2 нет голого echo/printf с кириллицей", not raw_echo, raw_echo[:3])

# ======================================================================
# U4/U5. SSH: argv, sudo-префикс, DG_LANG в построенной команде
# ======================================================================

def _fake_ssh(srv):
    """SSH-объект без коннекта: __new__ + ручные поля."""
    ssh = CA.SSH.__new__(CA.SSH)
    ssh.srv = srv
    ssh.log = lambda m: None
    ssh._probed = True
    ssh._auth_pw = False
    ssh.plink = ssh.pscp = "plink-x"
    ssh.ssh_exe = ssh.scp_exe = "ssh-x"
    ssh.backend = "openssh" if srv.get("key") else "putty"
    ssh.sudo = "" if srv.get("user") == "root" else "sudo -n "
    ssh.sudo_pw = None
    if srv.get("sudo_mode") == "pw" and srv.get("password"):
        ssh.sudo = "sudo -S -p '' "
        ssh.sudo_pw = srv["password"]
    return ssh

def u4_argv():
    s = {"user": "labadmin", "host": "h", "ssh_port": 22,
         "sudo_mode": "pw", "password": "pw1"}
    ssh = _fake_ssh(s)
    check("U4.1 sudo_mode=pw → 'sudo -S -p ''' + пароль в sudo_pw",
          ssh.sudo == "sudo -S -p '' " and ssh.sudo_pw == "pw1")
    s2 = {"user": "root", "host": "h"}
    check("U4.2 root → без sudo-префикса", _fake_ssh(s2).sudo == "")
    s3 = {"user": "u", "host": "h", "sudo_mode": "nopasswd"}
    check("U4.3 nopasswd → sudo -n", _fake_ssh(s3).sudo == "sudo -n ")

    # openssh: -n только когда НЕТ пароля (иначе sudo -S не получит stdin)
    sk = {"user": "u", "host": "h", "key": "k"}
    a = _fake_ssh(sk)._argv("id")
    check("U4.4 openssh без пароля → -n и -i k", "-n" in a and "k" in a)
    skp = dict(sk, password="pw")
    a = _fake_ssh(skp)._argv("id")
    check("U4.5 openssh с паролем → без -n (нужен stdin для sudo -S)",
          "-n" not in a)
    # putty: -pw при пароле
    sp = {"user": "u", "host": "h", "password": "pw"}
    a = _fake_ssh(sp)._argv("id")
    check("U4.6 putty с паролем → -pw", "-pw" in a and "pw" in a)
    spk = {"user": "u", "host": "h", "ppk": "k.ppk"}
    a = _fake_ssh(spk)._argv("id")
    check("U4.7 putty с ppk → -i k.ppk", "-i" in a and "k.ppk" in a)

    # PerSourcePenalties-защита: host:port помечен «ключ отвергнут» →
    # openssh-ветка с -i пропускается, сразу plink -pw (и для upload тоже)
    skd = {"user": "u", "host": "deadhost", "ssh_port": 22,
           "key": "k", "password": "pw"}
    sshd_ = _fake_ssh(skd)
    CA.SSH._auth_pw_hosts.add("deadhost:22")
    try:
        a = sshd_._argv("id")
        check("U4.8 ключ отвергнут → argv через plink -pw, без -i",
              a[0] == "plink-x" and "-pw" in a and "-i" not in a)
        a2 = sshd_._argv_upload("l", "r")
        check("U4.9 upload тоже → pscp -pw", a2[0] == "plink-x"
              and "-pw" in a2 and "-i" not in a2)
    finally:
        CA.SSH._auth_pw_hosts.discard("deadhost:22")

def u5_run_script():
    s = {"user": "labadmin", "host": "h", "sudo_mode": "pw", "password": "pw"}
    ssh = _fake_ssh(s)
    captured = []
    ssh.upload = lambda *a, **k: None
    ssh._spawn = lambda args, input_text=None, timeout=0: (
        captured.append((args, input_text)) or (0, "ok"))
    ssh.run_script("nat-enable.sh", "443", timeout=30)
    cmd = captured[-1][0][-1]
    check("U5.1 run_script: env DG_LANG= в команде",
          "env DG_LANG=" in cmd, cmd[-80:])
    check("U5.2 run_script: один sudo-префикс (pw-режим)",
          cmd.count("sudo") == 1 and "sudo -S -p ''" in cmd, cmd[-80:])
    check("U5.3 пароль sudo уходит в stdin один раз",
          captured[-1][1] == "pw\n")
    check("U5.4 скрипт уходит в /tmp/dgadm-…",
          "dgadm-nat-enable.sh" in cmd)

# ======================================================================
# U6. Чистые функции
# ======================================================================

def u6_pure():
    out = "===A===\n1\n2\n===B===\n3\n"
    check("U6.1 parse_section средний блок", CA.parse_section(out, "A") == "1\n2")
    check("U6.2 parse_section последний блок", CA.parse_section(out, "B") == "3")
    check("U6.3 parse_section нет маркера", CA.parse_section(out, "C") == "")

    app.lang = "ru"
    n = app._probe_note("nat_ok")
    check("U6.4 _probe_note: известный токен → текст", "masquerade" in n or "маск" in n)
    check("U6.5 _probe_note: неизвестный токен → as-is",
          app._probe_note("strange:thing") == "strange:thing")
    # args count mismatch → голый шаблон, без TypeError
    m = app._probe_note("cloak_busy:443")
    check("U6.6 _probe_note: неполные args → шаблон без подстановки",
          "%s" in m or "занят" in m)
    app.lang = "en"
    n2 = app._probe_note("cloak_down")
    check("U6.7 _probe_note: en → ASCII", not re.search(r"[а-яА-Я]", n2), n2)
    app.lang = "ru"

    check("U6.8 valid_cn", CA.valid_cn("user_1-x")
          and not CA.valid_cn("юзер") and not CA.valid_cn("a b"))
    check("U6.9 uid_to_b64url", CA.uid_to_b64url("a+b/c=") == "a-b_c=")

    check("U6.10 _fmt_rate: ∞ для безлимита",
          CA._fmt_rate(CA.INT64_MAX) == "∞" and CA._fmt_rate(None) == "∞")
    check("U6.11 _fmt_rate значения", CA._fmt_rate(125000) == "1М",
          CA._fmt_rate(125000))
    check("U6.12 _fmt_quota: ∞ если оба безлимитны",
          CA._fmt_quota(CA.INT64_MAX, CA.INT64_MAX) == "∞")
    check("U6.13 _fmt_bytes G/M",
          CA._fmt_bytes(2147483648) == "2G" and CA._fmt_bytes(5242880) == "5M")

def u6_apply_probe():
    fake = """
===STEP_ssh===
ok|
===STEP_audit===
ok|
===STEP_fw===
ok|
===STEP_sysupd===
warn|reboot_required:apt
===STEP_pkgs===
ok|
===STEP_ovpn===
ok|
===STEP_nat===
ok|
===STEP_cloak===
ok|
===DONE===
"""
    s = {"name": "probe-test", "steps": {}}
    CA.save_data = lambda d: None     # не писать data.json
    ok = app._apply_probe(s, fake)
    check("U6.14 _apply_probe разобрал 8 шагов",
          ok and len(s["steps"]) == 8, s.get("steps"))
    check("U6.15 deployed=True по cloak=ok", s.get("deployed") is True)
    check("U6.16 reboot_required по токену",
          s.get("reboot_required") is True)
    check("U6.17 note sysupd локализована",
          "reboot" in s["steps"]["sysupd"]["note"]
          or "перезагруз" in s["steps"]["sysupd"]["note"],
          s["steps"]["sysupd"]["note"])
    # пустой вывод → False
    s2 = {"name": "p2", "steps": {}}
    check("U6.18 _apply_probe без STEP_* → False",
          app._apply_probe(s2, "garbage") is False)

def u6_configs():
    srv = {"host": "1.2.3.4", "pubkey": "PK", "ck_port": "8443",
           "mask_domain": "www.bing.com", "proto": "udp"}
    c = CA.make_ckclient(srv, "UID==")
    check("U6.19 ckclient: UDP при proto=udp", c["UDP"] is True)
    check("U6.20 ckclient: RemotePort из ck_port",
          c["RemotePort"] == "8443" and c["RemoteHost"] == "1.2.3.4")
    c2 = CA.make_ckclient(srv, "UID==", mask="custom.example")
    check("U6.21 ckclient: per-user mask → ServerName",
          c2["ServerName"] == "custom.example")
    srv2 = dict(srv, proto="tcp")
    check("U6.22 ckclient: proto=tcp → UDP False",
          CA.make_ckclient(srv2, "U")["UDP"] is False)
    ov = CA.make_ovpn("tcp-client", "CAX", "CERTX", "KEYX", "TAX")
    check("U6.23 make_ovpn: proto и PEM-блоки на месте",
          "proto tcp-client" in ov and "CAX" in ov and "TAX" in ov
          and "{" not in ov)

# ======================================================================
# U7. Диалоги без модальности (body/validate/apply вручную)
# ======================================================================

def _dlg(cls, parent, **kw):
    d = cls.__new__(cls)
    d.t = parent.t
    for k, v in kw.items():
        setattr(d, k, v)
    import tkinter.ttk as ttk
    f = ttk.Frame(parent)
    cls.body(d, f)
    return d

def u7_dialogs():
    errs = []
    CA.messagebox.showerror = lambda *a, **k: errs.append(a[1] if len(a) > 1 else a[0])

    d = _dlg(CA.ServerDialog, app, srv={})
    d.vars["host"].set("1.1.1.1")
    check("U7.1 ServerDialog: без имени → отказ", not CA.ServerDialog.validate(d))
    d.vars["name"].set("srv1")
    check("U7.2 ServerDialog: нет ключа/пароля → отказ",
          not CA.ServerDialog.validate(d))
    d.vars["password"].set("x")
    check("U7.3 ServerDialog: пароль → ок", CA.ServerDialog.validate(d))
    d.vars["ssh_port"].set("99999")
    check("U7.4 ServerDialog: порт 99999 → отказ",
          not CA.ServerDialog.validate(d))
    d.vars["ssh_port"].set("22")
    # .pub → priv: создаём пару файлов
    tmp = tempfile.mkdtemp()
    pub = os.path.join(tmp, "id_x.pub")
    priv = os.path.join(tmp, "id_x")
    open(pub, "w").write("x"); open(priv, "w").write("x")
    d.vars["key"].set(pub)
    check("U7.5 ServerDialog: .pub → подмена на приватный",
          CA.ServerDialog.validate(d) and d.vars["key"].get() == priv)
    CA.ServerDialog.apply(d)
    check("U7.6 ServerDialog.apply → dict с host/user",
          d.result["host"] == "1.1.1.1" and d.result["user"] == "ubuntu")

    u = _dlg(CA.UserDialog, app, srv_mask="", rec=None)
    u.vars["name"].set("bad name!")
    check("U7.7 UserDialog: пробел в CN → отказ",
          not CA.UserDialog.validate(u))
    u.vars["name"].set("ok-user_1")
    u.vars["expiry_days"].set("abc")
    check("U7.8 UserDialog: не-число в сроке → отказ",
          not CA.UserDialog.validate(u))
    u.vars["expiry_days"].set("0")
    u.vars["sessions"].set("0")
    u.vars["up_mbits"].set("0"); u.vars["down_mbits"].set("0")
    u.vars["up_mb"].set("0"); u.vars["down_mb"].set("0")
    check("U7.9 UserDialog: валидный минимум → ок",
          CA.UserDialog.validate(u))
    CA.UserDialog.apply(u)
    r = u.result
    check("U7.10 UserDialog.apply: 0 дней → FAR_FUTURE, 0 Мбит → INT64_MAX",
          r["expiry"] == CA.FAR_FUTURE and r["up_rate"] == CA.INT64_MAX)
    check("U7.11 UserDialog.apply: sessions clamp ≥1", r["sessions"] >= 1)

    u2 = _dlg(CA.UserDialog, app, srv_mask="", rec=None)
    u2.vars["name"].set("u2")
    u2.vars["expiry_days"].set("7")
    u2.vars["sessions"].set("3")
    u2.vars["up_mbits"].set("10"); u2.vars["down_mbits"].set("20")
    u2.vars["up_mb"].set("1024"); u2.vars["down_mb"].set("2048")
    CA.UserDialog.validate(u2); CA.UserDialog.apply(u2)
    r = u2.result
    check("U7.12 apply: rate Мбит/с → байт/с (10M → 1250000)",
          r["up_rate"] == 1250000, r["up_rate"])
    check("U7.13 apply: expiry ≈ now+7d",
          abs(r["expiry"] - (int(__import__("time").time()) + 7 * 86400)) < 60)

# ======================================================================
# U8. labs.json ↔ supported
# ======================================================================

def u8_matrix():
    sup = supported_os()
    uncovered = []
    for lb in labs()["labs"]:
        d = lb.get("distro")
        if lb.get("auto") and d:
            if d[0] not in sup or d[1] not in sup.get(d[0], set()):
                uncovered.append("%s %s" % tuple(d))
    check("U8.1 все auto-лабы в supported-карте _step_audit",
          not uncovered, uncovered)

# ======================================================================

# ======================================================================
# U9. Revert server: снимок predeploy.env + восстановление в purge
# ======================================================================

def u9_revert():
    pd = open(os.path.join(REPO, "scripts", "predeploy-save.sh"),
              encoding="utf-8").read()
    check("U9.1 predeploy-save: пишет /etc/dgcloak/predeploy.env",
          "/etc/dgcloak/predeploy.env" in pd)
    check("U9.2 predeploy-save: baseline не перезаписывается",
          "baseline kept" in pd)
    for k in ("IP_FORWARD", "OPENVPN_PKG", "EASYRSA_PKG", "NFT_PKG",
              "FIREWALLD_MASQ", "UFW_FWD_POLICY", "UFW_IPFWD"):
        check("U9.3 снимок содержит %s" % k, '"%s=' % k in pd or "%s=" % k in pd)

    pg = open(os.path.join(REPO, "scripts", "purge-dgcloak.sh"),
              encoding="utf-8").read()
    check("U9.4 purge читает predeploy.env", "predeploy.env" in pg)
    check("U9.5 purge: STOPPED_SVC → systemctl enable --now",
          "STOPPED_SVC" in pg and "systemctl enable --now" in pg)
    check("U9.6 purge: DOCKER_POLICY_* → docker update --restart",
          "DOCKER_POLICY_" in pg and "docker update --restart" in pg)
    check("U9.7 purge: DOCKER_START → docker start",
          "DOCKER_START" in pg and "docker start" in pg)
    check("U9.8 purge: ip_forward из снимка, не хардкод 0",
          "pdget IP_FORWARD" in pg)
    check("U9.9 purge: FW=none → снос пакета nftables",
          '"$(pdget FW)" = "none"' in pg and "purge -y -qq nftables" in pg)
    check("U9.10 purge: чужой nft → re-dump ruleset (fix воскрешения таблицы)",
          "nft list ruleset > /etc/nftables.conf" in pg)
    check("U9.11 purge: ufw-оригиналы (UFW_FWD_POLICY/UFW_IPFWD)",
          "UFW_FWD_POLICY" in pg and "UFW_IPFWD" in pg)
    check("U9.12 purge: firewalld masq оставляем если был",
          "FIREWALLD_MASQ" in pg)
    check("U9.13 purge: pre-existing openvpn/easy-rsa не сносятся",
          "OPENVPN_PKG" in pg and "EASYRSA_PKG" in pg)

    src = open(os.path.join(REPO, "cloak_admin.py"), encoding="utf-8").read()
    check("U9.14 audit шаг вызывает predeploy-save.sh",
          '"predeploy-save.sh"' in src)
    check("U9.15 _step_fw записывает FW= в снимок",
          '_predeploy_note(ssh, "FW="' in src)
    check("U9.16 _step_cloak записывает STOPPED_SVC",
          '"STOPPED_SVC="' in src)
    check("U9.17 _step_cloak записывает docker-политики и DOCKER_START",
          "_predeploy_docker_pols" in src and '"DOCKER_START="' in src)
    check("U9.18 кнопка переименована в «Вернуть сервер»",
          '"Вернуть сервер"' in src and '"Сбросить сервер"' not in src)
    # dockerd надо рестартануть перед docker start: stop nftables флашит
    # его iptables-nft цепочки → start контейнера с -p падает (живой кейс)
    check("U9.19 purge: restart dockerd перед docker start + варн при сбое",
          "systemctl restart docker" in pg and "не стартовал" in pg)
    check("U9.20 ключ-фолбэк помечает host:port (PerSourcePenalties)",
          "_auth_pw_hosts" in src and "_pw_forced" in src)
    # firewalld-ветка (backlog п.1): forward ставится и проверяется своей
    # веткой; на <0.9 опции нет — masq открывал forward сам
    check("U9.21 _step_nat firewalld: --add-forward в зоне",
          'add-forward --zone=public' in src)
    check("U9.22 _step_nat firewalld: пост-проверка "
          "query-masquerade/query-forward",
          "--query-masquerade" in src and "--query-forward" in src)
    check("U9.23 снимок: FIREWALLD_FWD (forward до нас)",
          "FIREWALLD_FWD" in pd)
    check("U9.24 purge: FWD=no → --remove-forward",
          'FIREWALLD_FWD)" = "no"' in pg and "--remove-forward" in pg)
    pb = open(os.path.join(REPO, "scripts", "probe.sh"),
              encoding="utf-8").read()
    check("U9.25 probe: firewalld-ветка NAT заведена по FW=firewalld",
          'FW" = "firewalld"' in pb and "--query-masquerade" in pb
          and "--query-forward" in pb)


# ======================================================================
# U10. Метка сервера в списке (_srv_mark): ✓ / ✓- / ⚠ / пусто
# ======================================================================

def u10_srv_mark():
    m = app._srv_mark
    st = lambda v: {"st": v}
    all_ok = {k: st("ok") for k, _ in CA.App.STEPS}
    check("U10.1 метка: deployed + все шаги ok → ✓",
          m({"deployed": True, "steps": all_ok}).strip() == "✓")
    sk = dict(all_ok); sk["sysupd"] = st("skip")
    check("U10.2 метка: deployed + пропущенный шаг → ✓-",
          m({"deployed": True, "steps": sk}).strip() == "✓-")
    w = dict(all_ok); w["sysupd"] = st("warn")
    check("U10.3 метка: warn приоритетнее deployed → ⚠",
          m({"deployed": True, "steps": w}).strip() == "⚠")
    fl = dict(all_ok); fl["ovpn"] = st("fail")
    check("U10.4 метка: fail на недодеплое → ⚠",
          m({"steps": fl}).strip() == "⚠")
    check("U10.5 метка: deployed без steps → ✓-",
          m({"deployed": True}).strip() == "✓-")
    check("U10.6 метка: не развёрнут, нет steps → пусто",
          m({}) == "")
    check("U10.7 метка: все ok, но deployed снят → пусто",
          m({"steps": all_ok}) == "")

    # онлайн-колонки SSH/Cloak: ✓ ✗ … — (три состояния + н/д)
    ot = app._srv_online_txt
    dep = {"name": "sD", "deployed": True}
    und = {"name": "sU", "deployed": False}
    app._online.clear()
    check("U10.8 онлайн: до первой пробы → …",
          ot(dep, "ssh") == "…")
    app._online[("sD", "ssh")] = True
    app._online[("sD", "cloak")] = False
    check("U10.9 онлайн: доступен ✓ / недоступен ✗",
          ot(dep, "ssh") == "✓" and ot(dep, "cloak") == "✗")
    check("U10.10 cloak на неразвёрнутом → — (н/д)",
          ot(und, "cloak") == "—")
    check("U10.11 ssh на неразвёрнутом всё равно пробуется → …",
          ot(und, "ssh") == "…")


# ======================================================================
# U11. provision_client: профиль в клиентский inbox
# ======================================================================

def u11_provision():
    old_ap = os.environ.get("APPDATA", "")
    tmp = tempfile.mkdtemp(prefix="dgprov-")
    os.environ["APPDATA"] = tmp
    try:
        dgc = os.path.join(tmp, "u1.dgcloak")
        with open(dgc, "w") as f:
            f.write("{}")
        check("U11.1 нет VPN\\ → клиент не найден",
              CA.provision_client("srvA", "u1", dgc) is False)
        vdir = os.path.join(tmp, "DGCloak", "VPN")
        os.makedirs(vdir)
        inbox = os.path.join(vdir, "inbox")
        ok = CA.provision_client("srvA", "u1", dgc)
        check("U11.2 provision: inbox .dgcloak атомарно",
              ok and os.path.isfile(os.path.join(inbox, "u1@srvA.dgcloak"))
              and not os.path.exists(os.path.join(inbox, "u1@srvA.tmp")))
        CA.provision_client("srvA", "u1", delete=True)
        check("U11.3 delete → .del маркер, .dgcloak снят",
              os.path.isfile(os.path.join(inbox, "u1@srvA.del"))
              and not os.path.exists(
                  os.path.join(inbox, "u1@srvA.dgcloak")))
        # отзыв после отзыва/создание после удаления — маркеры друг друга
        # затирают, залипших пар .dgcloak+.del не бывает
        CA.provision_client("srvA", "u1", dgc)
        CA.provision_client("srvA", "u1", delete=True)
        CA.provision_client("srvA", "u1", dgc)
        check("U11.4 серия маркеров → остался только .dgcloak",
              os.path.isfile(os.path.join(inbox, "u1@srvA.dgcloak"))
              and not os.path.exists(os.path.join(inbox, "u1@srvA.del")))
    finally:
        os.environ["APPDATA"] = old_ap


# ======================================================================
# U12. Журналы на диске: logs\<srv>\<tab>.log, JSONL, ротация
# ======================================================================

def u12_journal_files():
    srv = {"name": "u12 srv [x]", "host": "1.2.3.4"}
    lib.add_srv(app, srv)
    name = srv["name"]
    ldir = app._log_dir(name)
    try:
        # запись say → файл канала deploy
        app._log_name = name
        app._log_ctx = None
        app._op_tab = None
        app.nb.select(0)
        app.say("u12 обычная строка")
        app.vsay("u12 verbose строка")
        f = os.path.join(ldir, "deploy.log")
        ok = os.path.isfile(f)
        check("U12.1 say/vsay → deploy.log создан под sanitize(имя)", ok)
        if ok:
            lines = [json.loads(l) for l in open(f, encoding="utf-8")]
            check("U12.2 JSONL: verbose-флаг и текст строки",
                  lines[0]["v"] is False and "обычная" in lines[0]["l"]
                  and lines[1]["v"] is True and "verbose" in lines[1]["l"])
        # каналы разделены: _mark_log("users") → другой файл
        app._mark_log("users", "u12 отметка users")
        check("U12.3 users-канал → users.log отдельным файлом",
              os.path.isfile(os.path.join(ldir, "users.log")))
        # перезагрузка: память чистим, читаем с диска
        app._logs.clear()
        app._logs_load()
        got = lib.jrnl(app, name, "deploy")
        check("U12.4 после рестарта журнал поднят с диска",
              len(got) == 2 and "обычная" in got[0], len(got))
        # ротация: заниженный лимит → текущий файл уходит в .old
        old_max = CA.LOG_MAX
        CA.LOG_MAX = 10
        try:
            app._log_write(name, "deploy", "[00:00:01] rot", False)
        finally:
            CA.LOG_MAX = old_max
        check("U12.5 ротация по размеру: .log → .log.old",
              os.path.isfile(os.path.join(ldir, "deploy.log.old")))
        # очистка канала: история с диска уходит, остаётся только
        # свежая отметка «Лог очищен» (файл пересоздаётся mark'ом)
        app._clear_log("deploy")
        f2 = os.path.join(ldir, "deploy.log")
        left = [json.loads(l)["l"] for l in open(f2, encoding="utf-8")] \
            if os.path.isfile(f2) else []
        check("U12.6 «очистить лог»: .old снят, в .log только отметка",
              not os.path.exists(os.path.join(ldir, "deploy.log.old"))
              and len(left) == 1 and "очищен" in left[0], left)
        # удаление сервера сносит папку журналов
        CA.messagebox.askyesno = lambda *a, **k: True
        app.data["servers"] = [srv]
        app._refresh_servers()
        app.srv_tv.selection_set(name)
        app._on_srv_select()
        lib.pump(app, 0.15)
        app._srv_del()
        lib.pump(app, 0.2)
        check("U12.7 удаление сервера → папка журналов снесена",
              not os.path.exists(ldir))
    finally:
        app.data["servers"] = [s for s in app.data["servers"]
                               if s.get("name") != name]
        app._logs.pop(name, None)
        app._refresh_servers()


# ======================================================================

u1_localization()
u2_regressions()
u2_pipefail_scripts()
u3_scripts()
u4_argv()
u5_run_script()
u6_pure()
u6_apply_probe()
u6_configs()
u7_dialogs()
u8_matrix()
u9_revert()
u10_srv_mark()
u11_provision()
u12_journal_files()

finish(app)
