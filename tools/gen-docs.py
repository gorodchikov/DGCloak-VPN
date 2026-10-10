# -*- coding: utf-8 -*-
"""Генератор трёх HTML-отчётов в docs/:
  changelog.html   — все изменения с первого билда (v1.0), по темам
  fixed-bugs.html  — все зафиксированные баги (test-plan «Найдено» + фикс-коммиты)
  tests.html       — инвентарь автотестов (4 файла, по секциям)
"""
import html
import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH = "https://github.com/gorodchikov/DGCloak-VPN/commit/"
FIRST_BUILD = "8b3c125"   # «DGCloak VPN 1.0»

# ---------------------------------------------------------------- git log
raw = subprocess.run(
    ["git", "-C", REPO, "log", "--reverse", "--format=%h%x09%s"],
    capture_output=True, text=True, encoding="utf-8").stdout
commits = []
seen_first = False
for ln in raw.splitlines():
    sha, _, msg = ln.partition("\t")
    if sha == FIRST_BUILD:
        seen_first = True
        continue
    if not seen_first or msg.startswith("Merge"):
        continue
    commits.append((sha, msg))

# -------------------------------------------- группировка по темам
RULES = [
    ("Тесты и тестовые стенды", r"тест|test|logtest|matrix|labs|харнес|"
     r"матрица|прогон|чистая windows"),
    ("Локализация RU/EN", r"язык|локализац|ru/en|dg_lang|strings|перевод|"
     r"русск|кириллиц"),
    ("Системный трей и иконки", r"трей|иконка|плащ|шестер"),
    ("Спиннер деплоя", r"спиннер|дуга"),
    ("Пользователи и конфиги", r"юзер|пользовател|бандл|конфиг|\.dgcloak|"
     r"квота|лимит|сесси|отзыв|импорт|cn |uid"),
    ("Деплой, SSH, фаервол, сервер", r"ssh|sudo|deploy|деплой|фаервол|fw|"
     r"nat\b|probe|ребут|перезагруз|purge|сброс|вернуть|nft|docker|сервер|"
     r"preflight|ключ|reboot|порт|port-owner|sysupd|pkgs|шаг|шаги|apt|"
     r"easyrsa|pki|mgmt|port"),
    ("Клиент VPN", r"клиент|openvpn|профил|reconnect|внешн|\bip\b|vpn|"
     r"туннел|remote|bypass|loopback|pids|статус|подключ|скорост|трафик|"
     r"аптайм"),
    ("UI / UX", r"ui\b|ux\b|окн|выравниван|кнопк|тултип|ширин|столб|диалог|"
     r"лог\b|журнал"),
    ("Сборка и файлы", r"build|gitignore|\bexe\b|version_info|appdata|"
     r"миграц|\bbin\b|скачива"),
    ("Документация", r"claude|docs|readme|backlog|план"),
]

def topic(msg):
    m = msg.lower()
    for name, pat in RULES:
        if re.search(pat, m):
            return name
    return "Прочее"

groups = {}
for sha, msg in commits:
    groups.setdefault(topic(msg), []).append((sha, msg))

ORDER = [n for n, _ in RULES] + ["Прочее"]

CSS = """
body{font:14px/1.5 'Segoe UI',system-ui,sans-serif;background:#14171c;
color:#d7dce2;max-width:960px;margin:0 auto;padding:32px 20px}
h1{color:#fff;font-size:24px;border-bottom:2px solid #3d4b5c;
padding-bottom:10px}
h2{color:#8fb8e8;margin-top:28px;font-size:17px}
.sub{color:#7d8a99;margin:-6px 0 24px}
table{width:100%;border-collapse:collapse;background:#1b2027;
border-radius:8px;overflow:hidden}
th{background:#232b35;text-align:left;padding:8px 12px;color:#9db4cd;
font-weight:600}
td{padding:7px 12px;border-top:1px solid #262d36;vertical-align:top}
tr:hover td{background:#212831}
code{background:#0f1318;padding:1px 6px;border-radius:4px;color:#e8b96a;
font-size:12.5px}
a{color:#6ea8fe;text-decoration:none}
a:hover{text-decoration:underline}
.tag{display:inline-block;background:#25384e;color:#9ec7f0;
border-radius:4px;padding:1px 8px;font-size:12px;margin-right:6px}
.fix{background:#3d2a2a;color:#f0a8a8}
.count{color:#7d8a99;font-weight:400;font-size:13px}
details{margin:10px 0}
summary{cursor:pointer;color:#8fb8e8;font-size:15px;padding:4px 0}
summary:hover{color:#fff}
.stat{display:inline-block;background:#1e3a2a;color:#8fe0a8;
border-radius:6px;padding:4px 14px;margin:4px 8px 4px 0;font-weight:600}
footer{margin-top:40px;color:#5c6875;font-size:12px;
border-top:1px solid #262d36;padding-top:12px}
"""

def page(title, body, sub=""):
    return ("<!DOCTYPE html><html lang=ru><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>"
            "<h1>%s</h1>%s%s<footer>DGCloak VPN · сгенерировано из "
            "git-истории и tests/</footer></body></html>"
            % (html.escape(title), CSS, html.escape(title),
               "<div class=sub>%s</div>" % html.escape(sub) if sub else "",
               body))

# ======================================================= changelog.html
rows = ""
tot = 0
for name in ORDER:
    items = groups.get(name)
    if not items:
        continue
    tot += len(items)
    lis = "".join(
        '<tr><td style="width:74px"><a href="%s%s"><code>%s</code></a></td>'
        "<td>%s</td></tr>" % (GH, sha, sha, html.escape(msg))
        for sha, msg in items)
    rows += ("<details open><summary>%s "
             "<span class=count>· %d</span></summary>"
             "<table>%s</table></details>"
             % (html.escape(name), len(items), lis))

cl_body = ("<span class=stat>%d коммитов после v1.0</span>" % tot) + rows
open(REPO + r"\docs\changelog.html", "w", encoding="utf-8").write(
    page("Изменения с первого билда (v1.0)", cl_body,
         "Все коммиты после релиза «DGCloak VPN 1.0», сгруппированные по темам."))

# ======================================================= fixed-bugs.html
# Курированный список: таблица «Найдено» из docs/test-plan.md + фикс-коммиты
BUGS = [
    # (компонент, описание, коммиты)
    ("Деплой", "probe падал на сервере без sudo, но deployed=True и ✓ в списке оставались — список врал до первого успешного probe", "«Найдено» #1"),
    ("Юзеры", "«Обновить»/«Создать» на неразвёрнутом сервере висели 30 с (таймаут ck-client): _api не проверял deployed", "#2 · 2670d5c"),
    ("Ключи", "Старый сгенерированный ключ *_ed25519 оставался сиротой в keys\\ после перевыпуска под новым именем", "#3"),
    ("Юзеры", "Отзыв офлайн-юзера писал «сессия сброшена»: ловили SUCCESS, а mgmt шлёт его и на пароль. Теперь 'SUCCESS: common name'", "#4 · 236f60e"),
    ("Деплой", "Тупик: реестр «все шаги ok» + откаченный сервер → «Развернуть всё» пропускало всё и рапортовало успех на пустом сервере. Добавлен probe перед проходом", "#5 · 43b3b33"),
    ("Импорт", "Импорт не тянул Cloak-порт из многострочного BindAddr → конфиги юзеров после переезда сервера получили бы 443 вместо 8443", "#6/#10 · 13f1b1d, ec46dfe"),
    ("Аудит", "ext_if сохранялся как «==» — регекс ловил маркер ===EXT_IF===", "#7 · 13f1b1d"),
    ("Отзыв", "Тупик: отзыв «призрака» (запись есть, сертификата нет) падал на easyrsa revoke — юзера нельзя было ни удалить, ни пересоздать", "#8 · c4aff71"),
    ("Отзыв", "Вариант тупика на чистом сервере: cd \"\" не ловился паттернами → призрак не удалялся", "#16 · 95df591"),
    ("Деплой", "probe ставил sysupd=ok по отсутствию флага reboot → шаг апгрейда пропускался на свежем снапшоте. Теперь считает pending-обновления", "#9 · ec46dfe"),
    ("Деплой", "«cloak latest: ?» на Debian minimal — версия бралась до установки curl", "#11 · ec46dfe"),
    ("Ребут", "Авто-ребут после деплоя слал reboot без sudo → denied глотался; «поднялся» считался по первому ответу SSH. Теперь sudo + сравнение uptime -s до/после", "#12 · 880a7a7"),
    ("Юзеры", "«Изменить» без изменений перевыпускал конфиг и дёргал API", "#13 · 24c3e2d"),
    ("Экспорт", "Каноническая копия бандла в %APPDATA% врала для юзеров, восстановленных импортом", "#14 · d0ae85f"),
    ("SSH", "Фолбэк на пароль маскировал ошибки команд: любой rc!=0 выглядел как «Permission denied», парольный режим не включался", "#15 · 95df591"),
    ("Юзеры", "mgmt kill на чистом сервере сыпал traceback — ovpn-mgmt.py не ловил отказ соединения", "#17"),
    ("Юзеры", "Создание юзера падало: залипший pki/lock.file с мёртвым PID (easyrsa убит обрывом SSH). Лок снимается, если PID мёртв", "#18 · 3654943"),
    ("Импорт", "После переезда сервера восстановленные юзеры оставались без bundles\\ — импорт тянул только записи реестра", "#19 · 12cfcca"),
    ("Логи", "«Копировать/Очистить лог» во время чужой операции писали отметку в журнал сервера-операции, а не показанного", "#20"),
    ("Логи/fw", "Диалог фаервола был привязан к серверу на момент открытия: действие шло на сервер диалога, лог — в журнал выбранного", "#21"),
    ("SSH", "sudo по паролю не работал — добавлен режим sudo -S с паролем через stdin", "d87d581"),
    ("Скрипты", "detect.sh: ipify отдаёт PUBIP без перевода строки — парсинг ломался", "6e11e91"),
    ("SSH", "Два sudo в одной команде при импорте ключей: второй получал EOF вместо пароля из stdin", "0b96af9"),
    ("Клиент", "Ctrl+C/V/X/A не работали на русской раскладке — кириллические keysym; ловим по keycode", "c2b170c, a770965"),
    ("Клиент", "Чужой ck-client в режиме admin-API (-a) считался конфликтом и блокировал запуск", "7376155"),
    ("Клиент", "Зависшие ck-client/openvpn после аварийного выхода блокировали переподключение — добиваем по pids.json с проверкой имени exe", "66ddb28"),
    ("Клиент", "После отключения VPN переставал отвечать 127.0.0.1 — OpenVPN удалял системный loopback-маршрут. Флаг local в redirect-gateway + repair_loopback", "архитектурный фикс"),
    ("Клиент", "Профиль: вопрос «добавить первый» выскакивал всегда; файлы не копировались → битые пути", "cedb131"),
    ("Клиент", "Порт из remote-строки .ovpn игнорировался в части сценариев", "dacd99a"),
    ("Клиент", "t() падал при kwarg 's' (heartbeat) — конфликт с позиционным параметром", "8d7d727"),
    ("Клиент", "Тултип трея: выравнивание съезжало на пропорциональном шрифте; заголовок резал RU-строки лимитом 127 символов", "4395017, 9673b10, 85a0fdd, d30fa28"),
    ("Клиент", "Внешний IP через VPN — одиночный запрос через ~1 с после CONNECTED сгорал по таймауту → «VPN IP: —» навсегда. Фоновые ретраи + обновление статуса", "550f09a"),
    ("Клиент", "external_ip зависел от одного сервиса; добавлены фолбэки + строка «VPN IP» скрывается при неудаче", "a31c3f2"),
    ("Клиент", "Строки состояния/статуса поехали по ширине — моноширинный шрифт и выравнивание столбцом", "7c2b4f9 и др."),
    ("Сборка", "utf-8 print падал на cp1252-консоли при сборке exe", "e8aa738"),
    ("Сборка", "download() падал, если целевой папки нет", "3de08d5"),
    ("Миграция", "Переезд в общий %APPDATA%\\DGCloak оставлял дубликат ck-client в bin\\", "c42ab4a"),
    ("Деплой", "apt update падал на битых сторонних репозиториях — шаг пакетов не шёл", "46f6f28"),
    ("Деплой", "Шаг «Маршрутизация и NAT» падал: второй sudo терял пароль + grep -q в pipefail давал SIGPIPE=141", "100dc34"),
    ("Деплой", "Заголовок шага в логе не переводился — _run_step брал title без self.t()", "7a7c109"),
    ("SSH", "Мёртвый ключ после отката VM: каждый шаг заново тыкал его → PerSourcePenalties на OpenSSH ≥9.8 ронял соединения («Connection reset by peer»). Метка host:port в _auth_pw_hosts", "de8fc9e"),
    ("Revert", "docker start контейнера с -p молча падал после flush ruleset'а (stop nftables смывает iptables-nft цепочки докера) — добавлен restart dockerd", "de8fc9e"),
    ("Revert", "Старый purge ломал чужое: хардкодил ip_forward=0, сносил пакеты до нас, не возвращал сервисы/docker, чужая nft-таблица воскресала из /etc/nftables.conf после ребута", "722e6e7"),
    ("Админка", "Анти-зависание при дохнущем SSH: не было TCP-чека порта и отмены", "2eea5b4"),
    ("Админка", "При выходе оставались сироты plink/pscp/ssh-процессы", "600b19a"),
    ("Админка", "Детект «своего VPN» не видел старую папку %APPDATA%\\DGCloakVPN", "2b4e9d7"),
    ("Админка", "NAT-аудит давал ложный ok: чужие docker/amnezia masq принимались за наш — теперь проверяем именно 10.8.0.0/24 + forward tun0", "98030fb"),
    ("Диалоги", "В поле ключа принимался .pub — автозамена на приватный или понятная ошибка", "8eef5b9"),
    ("Иконка", "Шестерёнка бейджа рендерилась со срезанными зубьями", "e7c686b"),
    ("Локализация", "probe-заметки не переводились; сырой вывод содержал кириллицу", "b0ea4ac"),
    ("Деплой", "firewalld: ставился --add-masquerade без --add-forward — на firewalld ≥0.9 форвардинг не открывался; пост-проверки тоже не было (общие nft/iptables-проверки дали бы ложный статус)", "c1cfa05"),
    ("Админка", "Метка сервера пустая при deployed+пропущенный шаг (отменённые обновления) и после «Импорта ключей» — выглядело «не развёрнут». Третий знак ✓-", "ab040d8"),
    ("Админка", "Онлайн-колонки: «ещё не пробовали» и «недоступно» выглядели одинаково пустыми; на «чёрной дыре» потоки проб плодились каждые 15 с — дедуп + явные статусы …/✓/✗/—", "c1425cc"),
    ("Клиент", "Переименование профиля оставляло файлы в profiles\\<старое имя> (проверка «уже наша копия» пропускала их) — папка плодила сироты; попутно: дубли и safe-коллизии имён («e x» vs «e_x») давали общую папку с взаимными rmtree", "c5afa01"),
    ("Клиент", "Переименованный managed-профиль (от админки) терял связь с inbox: .del/<cn>@<srv> его не находил → сирота навсегда. Имя managed-профиля теперь readonly", "c5afa01"),
    ("Админка", "SSH-пароль и admin_uid Cloak лежали в data.json открытым текстом — теперь DPAPI-блобы *_dp (CryptProtectData, читается только под этой учёткой на этом ПК)", "c5afa01"),
    ("Клиент", "Раскрытие «Дополнительно» меняло размер окна: жёсткий geometry(720x560) + set_status снапил ширину к reqwidth открытой панели. Теперь окно растёт ровно на высоту панели, ширина не трогается", "73bb4c0"),
    ("Клиент", "Подсказка при сворачивании в трей всегда писала «Отключить VPN и выйти из программы», даже в простое — текст брали из реального пункта меню («Выход» в простое)", "3568ffd"),
    ("Клиент", "Пауза не срабатывала: signal SUSPEND/RESUME — псевдо-сигнал только Android-сборки OpenVPN, десктоп отвечал «not a known signal type». Пауза перенесена на route delete/add def1-маршрутов + сторож от re-key", "a74eb6e"),
    ("Клиент", "Комбобокс профиля пропал совсем: рамке с pack_propagate(False) задали ширину, но не высоту — схлопнулась в 0", "2cea111"),
    ("Клиент", "Ширина окна росла на 127px при раскрытии «Дополнительно»: update_idletasks подгонял окно под reqsize панели до чтения winfo_width. Окно теперь сразу по ширине панели, ширина в geometry() берётся до pack", "2cea111"),
]

bug_rows = "".join(
    '<tr><td style="width:120px"><span class=tag>%s</span></td>'
    "<td>%s</td><td style='width:190px;color:#7d8a99;font-size:12px'>%s</td></tr>"
    % (html.escape(comp), html.escape(what), html.escape(src))
    for comp, what, src in BUGS)
bugs_body = (
    "<span class=stat>%d зафиксированных багов</span>"
    "<p>Источники: таблица «Найдено» из <code>docs/test-plan.md</code> "
    "(ручной прогон по 7 дистрибутивам и продам) и fix-коммиты истории.</p>"
    "<table><tr><th>Компонент</th><th>Что было</th>"
    "<th>Источник</th></tr>%s</table>" % (len(BUGS), bug_rows))
open(REPO + r"\docs\fixed-bugs.html", "w", encoding="utf-8").write(
    page("Зафиксированные баги", bugs_body,
         "Все баги, найденные и исправленные за время разработки."))

# ======================================================= tests.html
def harvest(fname):
    """(check-id, текст) из check(\"ID ...\") вызовов файла."""
    src = open(REPO + "\\tests\\" + fname, encoding="utf-8").read()
    out = []
    for m in re.finditer(r'check\("([^"]{4,})', src):
        out.append(html.escape(m.group(1)))
    return out

def test_table(title, fname, desc, items):
    lis = "".join("<tr><td>%s</td></tr>" % i for i in items)
    return ("<h2>%s <span class=count>%d проверок</span></h2>"
            "<p>%s</p><table>%s</table>"
            % (html.escape(title), len(items), desc, lis))

cl = harvest("clienttest.py")
ad = harvest("admintest.py")
lg = harvest("logtest.py")
mx_items = [
    "M1 запись стенда есть в реестре админки",
    "M2 SSH + sudo (preflight)",
    "M3 distro/version по detect.sh = ожиданию матрицы",
    "M4 fw-бэкенд по fw-manage.sh = ожиданию",
    "M5 STEP_cloak из probe.sh = ожиданию deployed",
    "M6 флаг deployed в реестре = реальности по probe",
    "M7 (id,version) дистрибутива покрыты картой supported в _step_audit",
    "M8 локализация probe: токены ASCII; заметки — на языке UI",
]
stands = ("Ubuntu 20.04 · Ubuntu 22.04 · Ubuntu 24.04 · Ubuntu 26.04 · "
          "Debian 11 · Debian 12 · Debian 13 · прод dlmedia (read-only) · "
          "прод london-aws (read-only)")

tests_body = (
    "<span class=stat>Всего: %d проверок</span>" % (len(cl)+len(ad)+len(lg)+79)
    + test_table("tests/clienttest.py — клиент (офлайн)", "clienttest.py",
                 "Локализация, диалог профиля и .dgcloak-импорт, runtime.ovpn, "
                 "bypass-маршрут, pids-реестр, внешний IP, поток подключения "
                 "на фейках, management-сокет на живом localhost, "
                 "автореконнект, порт-пробы, статус-строки, save/load, "
                 "inbox-провижининг из админки.", cl)
    + test_table("tests/admintest.py — админка (офлайн)", "admintest.py",
                 "Аудит локализации STRINGS_EN; регрессии DG_LANG/один-sudo/"
                 "pipefail-grep/self.t(title); SSH-argv по режимам sudo и "
                 "ключ↔пароль (PerSourcePenalties); parse_section/_probe_note/"
                 "_apply_probe; make_ckclient/make_ovpn; диалоги "
                 "ServerDialog/UserDialog; labs.json↔supported; "
                 "predeploy-снимок и ветки revert; метки и онлайн-статусы "
                 "серверов; provision_client в inbox клиента; "
                 "firewalld forward; журналы на диске (JSONL, ротация). "
                 "В рантайме 113 PASS — часть проверок параметризована.", ad)
    + test_table("tests/logtest.py — журналы + живой юзер-цикл", "logtest.py",
                 "Раздельные журналы deploy/users по серверам, vsay/verbose, "
                 "очистка/копирование во время чужой операции, трекинг и "
                 "добивание plink/pscp-сириот, детект своего VPN и "
                 "предупреждения, fw allow/deny на лабе, полный цикл юзера "
                 "на лабе (создать→экспорт→дубль→удалить).", lg)
    + test_table("tests/matrix.py — живые стенды", "matrix.py",
                 "Read-only смоук по каждому стенду с auto=true "
                 "(79 проверок за прогон; 1 штатный FAIL — запись "
                 "london-aws auto=false ждёт ручной чистки). Стенды: "
                 + stands + ".",
                 mx_items))
open(REPO + r"\docs\tests.html", "w", encoding="utf-8").write(
    page("Автотесты", tests_body,
         "Четыре прогона: офлайн-юниты клиента и админки, харнес журналов "
         "с живым юзер-циклом, матрица живых стендов."))

print("OK: docs/changelog.html (%d commits), docs/fixed-bugs.html (%d), "
      "docs/tests.html (%d checks)" % (tot, len(BUGS), len(cl)+len(ad)+len(lg)+79))
