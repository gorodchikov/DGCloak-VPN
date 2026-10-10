# -*- coding: utf-8 -*-
"""EN-версии трёх HTML-отчётов в docs/ (*-en.html)."""
import html
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH = "https://github.com/gorodchikov/DGCloak-VPN/commit/"
FIRST_BUILD = "8b3c125"

# ------------------------------------------------------------ commits EN
CEN = {
 "dacd99a": "UI/tray/log/port: aligned buttons, \"Clear log\", renames, remote-port fix, log rotation",
 "fbb95c9": "First run: auto-install OpenVPN (winget) + latest Cloak (GitHub API), path checks, first-profile prompt",
 "aa979cd": "RU/EN UI: STRINGS_EN dict, language picker in advanced panel, data.json['language']",
 "5d8a250": "exe version resource (version_info.txt: description, v1.0, © 2026 Dan Gorodchikov, ru-RU/en-US) + --version-file in build.bat",
 "89f85a1": "CLAUDE.md: synced with code (runtime.ovpn, log rotation, first run, RU/EN, version resource, new tray)",
 "cedb131": "Fix: \"add profile\" prompt only when list is empty; profile files copied to %APPDATA%\\DGCloakVPN\\profiles",
 "460d46d": "Tray: \"Connect\" submenu without current profile; available while VPN active (selection = switch)",
 "d0de861": "Tray: \"Disconnect \u00abname\u00bb and connect \u25b8\" while VPN active; language picker moved to main window bottom-right",
 "4ee2dab": "Alignment: language combo right edge flush with \"Exit\" button",
 "28c0e17": "Top rows on shared grid: language list right edge aligned to \"Exit\"",
 "66ddb28": "stale ck-client killed by saved PIDs; rare profile fields under \"Advanced\"",
 "16a0be4": "CLAUDE.md: pids.json and profile-dialog expander",
 "d6a4753": "DGCloakAdmin: server admin app + reliable OpenVPN+Cloak deploy",
 "e06b07d": "Deploy module: 7-distro lab, firewalls, parametrized Cloak port",
 "d87d581": "SSH: sudo with password (sudo -S + stdin), NOPASSWD no longer required",
 "6e11e91": "detect.sh: newline after PUBIP (ipify returns no \\n)",
 "05fa287": "UX: spinner in step status + heartbeat + live apt/easyrsa output",
 "982ade7": "SSH: key\u2192password fallback + dead-key reinstall",
 "6d9da4c": "Spinner: dot square rotating clockwise (bottom/left/top/right)",
 "781332e": "Spinner: 3-dot edge rolling around square perimeter clockwise",
 "9dcad5c": "Spinner: slowed to 1 s/rev (250 ms/frame)",
 "ce2099f": "Spinner: uniform dot spacing (positions 1,3,5 — no cell-edge gap)",
 "15234c2": "Spinner: rotating half-circle \u25d0\u25d3\u25d1\u25d2 clockwise",
 "304f92a": "Spinner: active step in larger font (spin tag, 20pt)",
 "0f13fa6": "build-admin.bat: staging build into build-out + move to dist, lock checks",
 "167d245": "gitignore: build-out/",
 "50d085f": "Spinner: canvas arc over status cell (size independent of font)",
 "0f28f01": "gitignore: dist3/, build3/",
 "def2fd9": "UX: single button — routine confirmations removed; reboot offer after deploy",
 "24a6441": "After reboot consent the update step is marked ok",
 "fe80b0e": "sysupd: on upgrade refusal the \"reboot needed\" status isn't lost",
 "e5a6bc7": "Server list: deployed checkmark updates right after each step",
 "3244793": "\"Reset statuses\" \u2192 \"Check statuses\": real probe.sh audit of steps",
 "151ead5": "Per-server logs: switch on selection, each op writes to its own journal",
 "e01667f": "Spinner: arc bound to its server — doesn't leak into other servers' tables",
 "42320f7": "_refresh_servers: keep selection — spinner doesn't vanish between steps",
 "1957133": "\"Reset server\" button: purge-dgcloak.sh over SSH with confirmation",
 "123a539": "user bundle: RemotePort from ck_port, ck-client.exe into build",
 "c423c37": "admin app self-downloads ck-client.exe on first admin-API call",
 "c2b170c": "Ctrl+C/V/X/A on Russian layout: Cyrillic keysym -> virtual events",
 "a770965": "Ctrl+V/C/X/A on Russian layout: match by keycode, keysym unreliable",
 "2f189d2": "preflight: separate 'sudo not installed' diagnostics (Debian minimal)",
 "7e694f2": "preflight: sudo hint gains su - for root login",
 "cf649b6": "preflight: /usr/sbin/usermod — su without - lacks /usr/sbin in PATH",
 "271733b": "preflight: username in sudo hint — from server settings",
 "fbcbc0b": "\"Fetch keys\": warning when server not deployed per registry",
 "adba61f": "\"Fetch keys\": on deployed=False run probe.sh first, decide by facts",
 "7376155": "client: foreign ck-client in admin-API mode (-a) is not a conflict",
 "1312a02": "single .dgcloak profile file + single-instance lock for client",
 "cf45a38": "user bundle — .dgcloak only, raw files removed",
 "5d30493": "mask/proto/port now per-server fields; per-user mask in user dialog",
 "bacf5c5": "user: Edit button (sessions/expiry/mask via PUT admin-API + bundle rebuild)",
 "0ecd1e2": "log: \"Verbose output\" checkbox — ck-client info/debug hidden by default",
 "0ed2927": "log: user-creation service lines behind \"Verbose output\" checkbox",
 "1857fc5": "terminology: Bundle \u2192 Config; log prints full .dgcloak path",
 "de28dbc": "Verbose output: filter on display — checkbox reveals old lines too",
 "e77e110": "Verbose checkbox moved to Users tab",
 "d65736a": "per-tab logs: deploy and users have own journals",
 "6ea5a2b": "UI: fixed 1040 window, Reboot server button, logs higher",
 "3649a99": "Deploy UI: content-sized steps, grid buttons, Copy log",
 "b586a6f": "Reboot server: clears \"reboot needed\" status once up",
 "7844f16": "Mask domain field wider (18 -> 36)",
 "df6a4d9": "users: mask-domain column, equal-width buttons, registry caption",
 "02a1991": "users: Up/Down rate limit (KB/s, 0=unlimited) + Limit column",
 "08b0f6d": "users: Up/Down traffic quota MB, UID column removed, dup CN banned",
 "df98a06": "rate limit in Mbit/s, quota in MB/GB — units in dialog and table",
 "d80820c": "fix: _fill_users_local for new columns (was shifted by UID)",
 "6974908": "kill user: online check + clear messages instead of raw mgmt",
 "74cad72": "UI: Proto/Cloak port right-aligned, user captions, slim steps header",
 "e783fa9": "deploy: slim steps header (16px vs 24), window +40 height",
 "613fb20": "users: Sessions/Online columns centered",
 "1e32296": "deploy: Step/Status columns narrowed, Comment wider",
 "3a21796": "deploy: tooltip on Deploy-all, language combo (stub), Cloak port w/o colon",
 "62c351c": "server dialog: Name, Hostname/IP, OpenSSH key",
 "8e2d50b": "server/user deletion cleans local bundles and admin key in AppData",
 "0c48a12": "server deletion: SSH key kept — can still log in later",
 "0673fd5": "user registry mirrored on server (/etc/dgcloak/users.json) — import restores CN/UID/mask",
 "0b96af9": "fix: key import under sudo-with-password: one sudo not two (second lost stdin password)",
 "792bd09": "Import-keys button + updated tooltip",
 "8eef5b9": "server dialog: .pub instead of private key — auto-swap or clear error",
 "e3b2c66": "docs: test plan for admin app and client",
 "2670d5c": "_api: fail fast when server not deployed (was 30 s ck-client timeout); findings in test-plan",
 "236f60e": "kill/revoke: match 'SUCCESS: common name' not generic SUCCESS from mgmt password",
 "43b3b33": "Deploy-all: probe before pass — stale-ok registry no longer skips steps on empty server",
 "13f1b1d": "import pulls Cloak port from BindAddr; EXT_IF regex fix",
 "c4aff71": "revoke: missing cert/UID on server — not an error, registry ghost removed",
 "ec46dfe": "probe: sysupd via pending updates, Cloak port from multiline BindAddr; latest after install",
 "880a7a7": "reboot: sudo + uptime -s before/after check (auto-reboot after deploy never worked under non-root)",
 "24c3e2d": "Edit user without changes: don't reissue config or touch API",
 "ea4f67c": "test-plan: finding 13 — edit-without-changes",
 "2a67944": "test-plan: steps 1 (E) and 4 (G) confirmed manually",
 "d0ae85f": "Config export also updates canonical %APPDATA% copy",
 "95df591": "H1: password fallback no longer masks command errors; ghost revoke on clean server",
 "3654948": "Clear stale easyrsa lock.file when its PID is dead",
 "12cfcca": "Key import rebuilds .dgcloak for restored users",
 "4885fc3": "test-plan: manual pass done (E, D, C3, G, H1)",
 "5976bb7": "Check and deliver dependencies at startup (clean machine)",
 "d476f8d": "ck-client into bin\\ too — all downloaded tools in one place",
 "dfcf580": "ck-client from foreign folder copied to bin\\ with source logged",
 "2b91ec0": "Cosmetics #1/#3: failed probe drops \u2713, old keys cleaned",
 "081b31f": "test-plan: clean Windows verified (auto-deps ok)",
 "3d0892b": "Port and protocol locked on deployed server",
 "e1c7be0": "Server reset: wipe local user bundles too + honest dialog",
 "181672d": "\"Clear log\" instead of language in button row; language — bottom by legend",
 "80392ff": "Removed \"Clear log\" from Users tab — it broke layout",
 "0004741": "Deploy buttons aligned to steps-table edges",
 "07dc0d1": "\"Log cleared.\" on clear — like the client",
 "3c4ab6e": "Firewall log: meaningful line instead of \"=== OK fw-manage ===\"",
 "ae01191": "Log test: tests/logtest.py harness + routing fixes",
 "3f959c8": "User-data source label — clearer wording",
 "b326adc": "UI text audit: 15 wording and accuracy fixes",
 "1d3a0ff": "Test system: labs.json + lib.py + matrix.py, README",
 "87ea6ea": "Shared %APPDATA%\\DGCloak: bin\\Admin\\VPN + auto-migration",
 "03ef309": "gitignore: dist-vpn/ — client build to side folder",
 "c42ab4a": "Migration: bin dedup — duplicate removed, not left as residue",
 "3de08d5": "Client: download() creates destination folder",
 "b7d7a61": "\"Verbose output\" is a per-server flag, remembered across servers",
 "50d0c02": "Tooltips on all Users-tab buttons",
 "c30b70d": "\"Edit\" tooltip: mask \u2192 mask domain",
 "98dcfae": "Tooltips on Deploy-tab buttons",
 "4054a90": "Admin icon: cloak + gear badge",
 "3e7a1d6": "Admin icon: gear w/o badge disc (dark outline)",
 "f6b779d": "Admin log: clean Russian labels in service errors",
 "376a33c": "Admin localization: RU/EN, Windows-locale default, live switching",
 "0773747": "Localization methodology in CLAUDE.md + AST codemod in tools/",
 "46f6f28": "Deploy: apt update tolerates broken third-party repos",
 "b0ea4ac": "Bugs: Cloak unlimited-rate and probe-note language",
 "0cb173a": "Log: entry on UI language change",
 "f8d2021": "Server panel — table with online column",
 "86126c3": "Matrix: M8 test — probe-note localization",
 "af11dd1": "Server panel: third column Cloak",
 "18927e0": "Cloak-online: probe only deployed servers",
 "acf881f": "Cloak deploy on occupied port: owner \u2192 quench/alt port",
 "3805712": "Server panel: \u25b2/\u25bc reorder buttons",
 "14ce307": "probe: cloak-fail note names the port holder",
 "98030fb": "NAT audit: masq only for 10.8.0.0/24 + tun0 forward rules",
 "956ee99": "Backlog: firewalld verification, own-VPN management warning",
 "2eea5b4": "Anti-hang on dying SSH: TCP check, \"Stop\", title pulse",
 "b9f9304": "Backlog: kill child plink/pscp on admin exit",
 "600b19a": "Admin: kill child plink/pscp/ssh on exit + own-VPN warning",
 "28b9497": "Backlog: items 2 and 3 closed (600b19a)",
 "2b4e9d7": "Own-VPN detect: also check old %APPDATA%\\DGCloakVPN folder",
 "f613526": "logtest: phases F (pids registry, _warn_own_tunnel) and G (probe + user lifecycle on lab)",
 "7d7fb53": "Reboot: clear server \u26a0 in list after it's up",
 "026a122": "Language switch: hint that log and step statuses stay untranslated",
 "54faabf": "Admin icon: badge gear 50% bigger",
 "dd94f93": "Admin icon: gear inside patch (352,352)",
 "0edd04c": "build-admin: build straight into dist\\, no build-out staging",
 "960548b": "Admin data: flat layout + filters in dialogs",
 "e410775": "Admin onefile: single distributable exe + version resource",
 "3f5e83b": "Client: full log localization, IP before/after, lang hint, ADV help, tests",
 "8d7d727": "Client: t() fix — param s clashed with heartbeat kwarg",
 "e8aa738": "cloak_icon: utf-8 print — don't crash on cp1252 console during build",
 "a86e4be": "Client: UX — IP/uptime/traffic in status, profile check, auto-reconnect",
 "2edce9c": "Client: status as column (name/VPN IP/uptime/speed), same in tray",
 "f191b20": "Client: status block on own line, \"Connected for\", down-first",
 "7c2b4f9": "Client: monospace status, values aligned in column",
 "4395017": "Client: tray tooltip w/o monospace padding (proportional font shifted)",
 "9673b10": "Client: pixel-aligned tray tooltip (tkfont, Segoe UI 9)",
 "85a0fdd": "Client: APP_NAME title dropped from tooltip (127-char limit cut RU strings)",
 "d30fa28": "Client: tray tooltip — no alignment, no title",
 "e7c686b": "admin: auto window width per language + proper gear teeth",
 "4878e24": "admin: dual-language output of server scripts (DG_LANG)",
 "100dc34": "fix: \"Routing and NAT\" step failed on sudo-pw and SIGPIPE",
 "7a7c109": "fix: step title in log not translated (title w/o self.t)",
 "5e81eac": "tests: admin offline units (admintest.py, 58) + 23 new client checks",
 "d0ffb11": "ui: tooltip on \"Language:\" instead of dialog on switch; Disconnect separated in tray menu",
 "550f09a": "fix: external IP via VPN wasn't detected — single request ~1s after CONNECTED",
 "a31c3f2": "fix: fallback services for external IP; \"VPN IP\" hidden when undetected",
 "722e6e7": "admin: \"Reset\" \u2192 \"Revert server\" — restore pre-deploy state",
 "de8fc9e": "fix: live revert test on Ubuntu26 — two bugs",
 "966d6c9": "docs: HTML reports — changelog since v1.0, fixed bugs, test inventory",
 "b9db430": "docs: reports and guide updated (firewalld/✓-/inbox/online states)",
 "b2b58b0": "docs: client OS requirements — Windows 10/11 x64",
 "f0ff0a7": "docs: user-guide.html renamed to user-guide-ru.html",
 "e956db0": "ui: \"?\" button opens the user guide in browser (releases-repo Pages)",
 "4e7aa92": "admin: per-server journals on disk — logs\\<srv>\\<deploy|users>.log",
 "8eec5c9": "tools+docs: report generators into repo, auto-regen convention in CLAUDE.md",
 "a300004": "client: remove \"OpenVPN GUI\" desktop shortcut after our install",
 "cddcd60": "client: SHChangeNotify after shortcut removal — icon gone without F5",
 "f4e4c0f": "docs: reports for the shortcut fix + translations of new commits",
 "1ab458d": "admin: server rename moves journals/keys/bundles/client profiles",
 "04c3f96": "docs: SHChangeNotify in reports + translations",
 "b83ec36": "admin: external SSH keys copied into keys\\<srv>\\ on save",
 "8a6d8af": "docs+tests: U13 translations, check name without quotes, reports",
 "65fffb7": "docs: reports for key stashing + translations",
 "c5afa01": "client: profile rename fixes + admin: DPAPI for secrets",
 "a887a89": "docs+tests: check names without backslash (harvester truncated)",
 "199f9bd": "docs: reports for rename fixes and DPAPI + translations",
 "73bb4c0": "client: per-profile disk logs (JSONL+rotation), framed log w/ scrollbar, buttons in a row, window-resize fix on Advanced",
 "bd00eb0": "client tray: guide below separator, exit item by state (active/busy→with stop, idle→Exit+abort prompt)",
 "3a34615": "docs: reports for profile journals + window-resize fix",
 "3568ffd": "client: autoconnect selected profile on startup + tray hint shows the real exit item",
 "86438fb": "client: start minimized to tray option (Advanced, tray-only, no first-profile prompt when hidden)",
 "866906d": "docs: reports for autoconnect + tray hint bug",
 "2c2a341": "docs: reports and guides for start minimized to tray",
 "5556c6a": "client: checkbox order in Advanced — startup options first, debug log last",
 "0cd8012": "client: log action buttons moved next to the log frame in Advanced",
 "8c31553": "docs: commit translations (Advanced checkbox order)",
 "bfae016": "docs: commit translation (buttons next to log frame)",
 "7cdff67": "client: OpenVPN debug log checkbox now persists across runs",
 "df71ca7": "docs: commit translations (verbose persist + buttons near log)",
 "22494c2": "client: Start with Windows checkbox (HKCU Run key autorun, path self-heal)",
 "ef280f3": "docs: commit translations (Windows autorun)",
 "68cea55": "client: Real IP line in status + click an IP line to copy it",
 "99b4496": "docs: commit translations (Real IP + click-to-copy)",
 "e3ba988": "client: network info strip at the bottom of Advanced (LAN + tun client/server)",
 "7896206": "docs: commit translations (network info strip)",
 "209df56": "client: VPN pause — signal SUSPEND/RESUME, direct traffic without disconnect",
 "aaa16a5": "docs: commit translations (VPN pause)",
 "a74eb6e": "client: pause via route delete/add of def1 instead of signal SUSPEND (Android-only)",
 "f17458b": "docs: bug #59 (SUSPEND is Android-only), translations, route-based pause in guides",
 "9b13922": "client: UX batch — pause icon/menu/tooltip, combobox width, stable window height",
 "2cea111": "client: fix invisible combobox and window width jump on Advanced",
 "99c99e1": "docs: translations (pause UX batch)",
 "3a7d9d2": "docs: reports for tray menu and journals + guide tagline",
 "3c79e0c": "docs: EN versions of the three reports",
 "a6211f6": "docs: user guide RU+EN (features, controls, deploy walkthrough, FAQ)",
 "c1cfa05": "firewalld: zone forward + own verification and audit branch (backlog #1)",
 "ab040d8": "ui: third server mark \"✓-\" — deployed with skipped steps",
 "4c8fb25": "feature: user config auto-provisioning to the client via inbox",
 "c1425cc": "ui: explicit SSH/Cloak online-column states + probe dedup",
}

RULES = [
    ("Тесты и тестовые стенды", "Tests and test labs"),
    ("Локализация RU/EN", "RU/EN localization"),
    ("Системный трей и иконки", "System tray and icons"),
    ("Спиннер деплоя", "Deploy spinner"),
    ("Пользователи и конфиги", "Users and configs"),
    ("Деплой, SSH, фаервол, сервер", "Deploy, SSH, firewall, server"),
    ("Клиент VPN", "VPN client"),
    ("UI / UX", "UI / UX"),
    ("Сборка и файлы", "Build and files"),
    ("Документация", "Documentation"),
    ("Прочее", "Misc"),
]
RULE_PAT = [
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
TOPIC_EN = dict(RULES)

def topic(msg):
    m = msg.lower()
    for name, pat in RULE_PAT:
        if re.search(pat, m):
            return name
    return "Прочее"

CSS = open(REPO + r"\docs\changelog.html", encoding="utf-8").read()
CSS = re.search(r"<style>(.*?)</style>", CSS, re.S).group(1)

def page(title, body, sub=""):
    return ("<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>"
            "<h1>%s</h1>%s%s<footer>DGCloak VPN · generated from "
            "git history and tests/</footer></body></html>"
            % (html.escape(title), CSS, html.escape(title),
               "<div class=sub>%s</div>" % html.escape(sub) if sub else "",
               body))

# ------------------------------------------------------------ changelog-en
raw = subprocess.run(
    ["git", "-C", REPO, "log", "--reverse", "--format=%h%x09%s",
     "--encoding=utf-8"], capture_output=True, text=True,
    encoding="utf-8", errors="replace").stdout
seen = False
groups = {}
missing = []
for ln in raw.splitlines():
    sha, _, msg = ln.partition("\t")
    if sha == FIRST_BUILD:
        seen = True
        continue
    if not seen or msg.startswith("Merge"):
        continue
    en = CEN.get(sha)
    if en is None:
        missing.append(sha)
        en = msg
    groups.setdefault(topic(msg), []).append((sha, en))

rows = ""
tot = 0
for ru, en_name in RULES:
    items = groups.get(ru)
    if not items:
        continue
    tot += len(items)
    lis = "".join(
        '<tr><td style="width:74px"><a href="%s%s"><code>%s</code></a></td>'
        "<td>%s</td></tr>" % (GH, sha, sha, html.escape(EN))
        for sha, EN in items)
    rows += ("<details open><summary>%s "
             "<span class=count>\u00b7 %d</span></summary>"
             "<table>%s</table></details>" % (en_name, len(items), lis))
cl_body = ("<span class=stat>%d commits after v1.0</span>" % tot) + rows
open(REPO + r"\docs\changelog-en.html", "w", encoding="utf-8").write(
    page("Changes since the first build (v1.0)", cl_body,
         "All commits after the \u00abDGCloak VPN 1.0\u00bb release, "
         "grouped by topic."))
if missing:
    print("WARN no translation:", missing)

# ------------------------------------------------------------ fixed-bugs-en
BUGS = [
    ("Deploy", "probe failed on a server without sudo, yet deployed=True and \u2713 stayed in the list — it lied until the first successful probe", "test-plan #1"),
    ("Users", "\u201cRefresh\u201d/\u201cCreate\u201d on an undeployed server hung 30 s (ck-client timeout): _api didn't check deployed", "#2 \u00b7 2670d5c"),
    ("Keys", "Old generated *_ed25519 key stayed orphaned in keys\\ after reissue under a new name", "#3"),
    ("Users", "Revoking an offline user logged \u201csession dropped\u201d: we matched SUCCESS, but mgmt sends it for the password too. Now exact 'SUCCESS: common name'", "#4 \u00b7 236f60e"),
    ("Deploy", "Dead end: registry \u201call steps ok\u201d + reverted server \u2192 Deploy-all skipped everything and reported success on an empty server. Added a probe pass first", "#5 \u00b7 43b3b33"),
    ("Import", "Import missed the Cloak port in multiline BindAddr \u2192 user configs after server migration would get 443 instead of 8443", "#6/#10 \u00b7 13f1b1d, ec46dfe"),
    ("Audit", "ext_if was saved as \u201c==\u201d — the regex caught the ===EXT_IF=== marker", "#7 \u00b7 13f1b1d"),
    ("Revoke", "Dead end: revoking a \u201cghost\u201d (registry entry, no cert on server) died on easyrsa revoke — user could neither be deleted nor recreated", "#8 \u00b7 c4aff71"),
    ("Revoke", "Same dead end on a clean server: cd \"\" didn't match error patterns \u2192 ghost couldn't be removed", "#16 \u00b7 95df591"),
    ("Deploy", "probe set sysupd=ok on missing reboot flag \u2192 the upgrade step was skipped on a fresh snapshot. Now counts pending updates", "#9 \u00b7 ec46dfe"),
    ("Deploy", "\u201ccloak latest: ?\u201d on Debian minimal — version was read before curl was installed", "#11 \u00b7 ec46dfe"),
    ("Reboot", "Auto-reboot after deploy sent reboot without sudo \u2192 denied silently; \u201cup\u201d was judged by the first SSH reply. Now sudo + uptime -s before/after", "#12 \u00b7 880a7a7"),
    ("Users", "\u201cEdit\u201d without changes reissued the config and hit the API", "#13 \u00b7 24c3e2d"),
    ("Export", "Canonical bundle copy in %APPDATA% was wrong for users restored via import", "#14 \u00b7 d0ae85f"),
    ("SSH", "Password fallback masked command errors: any rc!=0 looked like \u201cPermission denied\u201d, password mode never engaged", "#15 \u00b7 95df591"),
    ("Users", "mgmt kill on a clean server dumped a traceback — ovpn-mgmt.py didn't catch refused connections", "#17"),
    ("Users", "User creation failed on a stale pki/lock.file with a dead PID (easyrsa killed by an SSH drop). Lock is cleared when the PID is dead", "#18 \u00b7 3654943"),
    ("Import", "After server migration restored users had no bundles\\ — import only pulled registry entries", "#19 \u00b7 12cfcca"),
    ("Logs", "\u201cCopy/Clear log\u201d during another server's op wrote the mark into the op server's journal, not the shown one", "#20"),
    ("Logs/fw", "Firewall dialog was bound to the server selected at open time: action went to dialog's server, log — to the selected one", "#21"),
    ("SSH", "sudo with password didn't work — added sudo -S mode with password via stdin", "d87d581"),
    ("Scripts", "detect.sh: ipify returns PUBIP without a newline — parsing broke", "6e11e91"),
    ("SSH", "Two sudos in one key-import command: the second got EOF instead of the stdin password", "0b96af9"),
    ("Client", "Ctrl+C/V/X/A didn't work on the Russian layout — Cyrillic keysyms; now matched by keycode", "c2b170c, a770965"),
    ("Client", "A foreign ck-client in admin-API mode (-a) counted as a conflict and blocked startup", "7376155"),
    ("Client", "Stale ck-client/openvpn after a crash blocked reconnect — killed via pids.json with exe-name check", "66ddb28"),
    ("Client", "After VPN disconnect, 127.0.0.1 stopped answering — OpenVPN removed the system loopback route. local flag in redirect-gateway + repair_loopback", "architectural fix"),
    ("Client", "Profile: \u201cadd first\u201d prompt popped always; files weren't copied \u2192 broken paths", "cedb131"),
    ("Client", "The remote-line port in .ovpn was ignored in some scenarios", "dacd99a"),
    ("Client", "t() crashed on kwarg 's' (heartbeat) — clash with positional param", "8d7d727"),
    ("Client", "Tray tooltip: alignment drifted on proportional font; title cut RU strings at the 127-char limit", "4395017, 9673b10, 85a0fdd, d30fa28"),
    ("Client", "External IP via VPN — single request ~1 s after CONNECTED timed out \u2192 \u201cVPN IP: —\u201d forever. Background retries + status refresh", "550f09a"),
    ("Client", "external_ip depended on one service; fallbacks added + \u201cVPN IP\u201d line hides on failure", "a31c3f2"),
    ("Client", "Status lines drifted in width — monospace font and column alignment", "7c2b4f9 et al."),
    ("Build", "utf-8 print crashed on cp1252 console while building the exe", "e8aa738"),
    ("Build", "download() failed when the destination folder didn't exist", "3de08d5"),
    ("Migration", "Moving to shared %APPDATA%\\DGCloak left a duplicate ck-client in bin\\", "c42ab4a"),
    ("Deploy", "apt update failed on broken third-party repos — the packages step never ran", "46f6f28"),
    ("Deploy", "\u201cRouting and NAT\u201d step failed: second sudo lost the password + grep -q under pipefail gave SIGPIPE=141", "100dc34"),
    ("Deploy", "Step title in log wasn't translated — _run_step used title without self.t()", "7a7c109"),
    ("SSH", "Dead key after VM revert: every step poked it again \u2192 PerSourcePenalties on OpenSSH \u22659.8 dropped connections (\u201cConnection reset by peer\u201d). host:port mark in _auth_pw_hosts", "de8fc9e"),
    ("Revert", "docker start of a -p container silently failed after the ruleset flush (nftables stop wipes docker's iptables-nft chains) — added dockerd restart", "de8fc9e"),
    ("Revert", "Old purge broke foreign state: hardcoded ip_forward=0, removed pre-existing packages, never restored services/docker, foreign nft table resurrected from /etc/nftables.conf after reboot", "722e6e7"),
    ("Admin", "Hang on dying SSH: no TCP port check and no cancel", "2eea5b4"),
    ("Admin", "Orphaned plink/pscp/ssh processes left behind on exit", "600b19a"),
    ("Admin", "Own-VPN detection missed the old %APPDATA%\\DGCloakVPN folder", "2b4e9d7"),
    ("Admin", "NAT audit gave false ok: foreign docker/amnezia masq counted as ours — now checks 10.8.0.0/24 + tun0 forward specifically", "98030fb"),
    ("Dialogs", "Key field accepted a .pub file — auto-swap to private key or a clear error", "8eef5b9"),
    ("Icon", "Badge gear rendered with clipped teeth", "e7c686b"),
    ("Localization", "probe notes weren't translated; raw output contained Cyrillic", "b0ea4ac"),
    ("Deploy", "firewalld: --add-masquerade was set without --add-forward — forwarding never opened on firewalld \u22650.9; there was no post-check either (generic nft/iptables checks would have lied)", "c1cfa05"),
    ("Admin", "Server mark was blank when deployed with a skipped step (cancelled updates) or after key import \u2014 it looked undeployed. Third mark \u2713-", "ab040d8"),
    ("Admin", "Online columns: \u201cnot probed yet\u201d and \u201cunreachable\u201d looked identically blank; on a black-holed host probe threads piled up every 15 s \u2014 in-flight dedup + explicit \u2026/\u2713/\u2717/\u2014 states", "c1425cc"),
    ("Client", "Profile rename left files in profiles\\<old name> (the \u201calready our copy\u201d check skipped them) \u2014 orphan folders piled up; also: duplicate and safe-name collisions (\u00abe x\u00bb vs \u00abe_x\u00bb) shared one folder with mutual rmtree", "c5afa01"),
    ("Client", "Renamed managed profile (from admin) lost inbox sync: .del/<cn>@<srv> missed it \u2192 permanent orphan. Managed profile name is now readonly", "c5afa01"),
    ("Admin", "SSH password and Cloak admin_uid were stored in data.json as plaintext \u2014 now DPAPI *_dp blobs (CryptProtectData, readable only under this user on this PC)", "c5afa01"),
    ("Client", "Expanding \u201cAdvanced\u201d resized the window: hardcoded geometry(720x560) + set_status snapped width to the open panel's reqwidth. Now the window grows exactly by the panel height; width untouched", "73bb4c0"),
    ("Client", "Tray-hide hint always said \u201cDisconnect VPN and exit\u201d even when idle \u2014 now uses the real menu item text (\u201cExit\u201d when idle)", "3568ffd"),
]
bug_rows = "".join(
    '<tr><td style="width:120px"><span class=tag>%s</span></td>'
    "<td>%s</td><td style='width:190px;color:#7d8a99;font-size:12px'>%s</td></tr>"
    % (html.escape(c), html.escape(w), html.escape(s))
    for c, w, s in BUGS)
bugs_body = (
    "<span class=stat>%d fixed bugs</span>"
    "<p>Sources: the \u201cFound\u201d table of <code>docs/test-plan.md</code> "
    "(manual pass over 7 distros and both prod servers) and the fix commits "
    "of the git history.</p>"
    "<table><tr><th>Component</th><th>What happened</th>"
    "<th>Source</th></tr>%s</table>" % (len(BUGS), bug_rows))
open(REPO + r"\docs\fixed-bugs-en.html", "w", encoding="utf-8").write(
    page("Fixed bugs", bugs_body,
         "All bugs found and fixed during development."))

# ------------------------------------------------------------ tests-en
def harvest(fn):
    src = open(REPO + "\\tests\\" + fn, encoding="utf-8").read()
    return [m.group(1) for m in re.finditer(r'check\("([^"\\]{4,})', src)]

def tr_list(fn, mapping):
    out = []
    for t in harvest(fn):
        out.append(html.escape(mapping.get(t, t)))
    return out

CL = {
"T1.1 все RU-строки UI/лога имеют EN-перевод": "T1.1 every RU UI/log string has an EN translation",
"T1.2 плейсхолдеры {..} совпадают RU↔EN": "T1.2 placeholders {..} match RU\u2194EN",
"T2.1 t() переводит при en": "T2.1 t() translates under en",
"T2.2 t() оставляет RU при ru": "T2.2 t() keeps RU under ru",
"T2.3 t()/say() не падают на kwarg 's' (heartbeat)": "T2.3 t()/say() don't crash on kwarg 's' (heartbeat)",
"T3.1 при смене языка диалог НЕ показывается (тултип на «Язык:»)": "T3.1 no dialog on language switch (tooltip on \u201cLanguage:\u201d)",
"T3.1b тултип перепереведён на en": "T3.1b tooltip re-translated to en",
"T3.2 язык сохранён в data.json": "T3.2 language saved to data.json",
"T3.3 обратно на ru — тултип по-русски": "T3.3 back to ru — tooltip in Russian",
"T3.4 меню трея при active: «Отключить» вынесен, подменю — только профили": "T3.4 tray menu when active: standalone \u201cDisconnect\u201d, submenu — only other profiles",
"T4.1 порт по умолчанию 1984": "T4.1 default port 1984",
"T4.2 ADV по умолчанию свёрнут": "T4.2 ADV collapsed by default",
"T4.6 «Проверить»: порт + резолв сервера": "T4.6 \u201cCheck\u201d: port + server resolution",
"T4.3 .dgcloak материализован в profiles": "T4.3 .dgcloak materialized into profiles\\",
"T4.4 UDP поднят из cloak.UDP": "T4.4 UDP lifted from cloak.UDP",
"T4.5 reconnect=True по умолчанию": "T4.5 reconnect=True by default",
"T5.1 пустой профиль → ошибка, result=None": "T5.1 empty profile \u2192 error, result=None",
"T5.2 нестандартный ADV → секция открыта сразу": "T5.2 non-default ADV \u2192 section open immediately",
"T6.1 runtime.ovpn: remote переписан на 127.0.0.1:1984 tcp": "T6.1 runtime.ovpn: remote rewritten to 127.0.0.1:1984 tcp",
"T6.2 UDP-профиль → remote … udp": "T6.2 UDP profile \u2192 remote \u2026 udp",
"T7.1 ручной bypass_ip приоритетнее": "T7.1 manual bypass_ip takes priority",
"T7.2 RemoteHost резолвится": "T7.2 RemoteHost resolves",
"T7.3 неразрешимый RemoteHost → None + предупреждение": "T7.3 unresolvable RemoteHost \u2192 None + warning",
"T7.4 без RemoteHost → None": "T7.4 no RemoteHost \u2192 None",
"T8.1 pids.json roundtrip": "T8.1 pids.json roundtrip",
"T8.2 pid_running: свой PID жив": "T8.2 pid_running: own PID alive",
"T8.3 pid_running: мёртвый PID — False": "T8.3 pid_running: dead PID \u2192 False",
"T8.4 pid_running: чужое имя exe — False": "T8.4 pid_running: foreign exe name \u2192 False",
"T9.1 external_ip парсит ответ": "T9.1 external_ip parses response",
"T9.2 мусор в ответе → None": "T9.2 garbage response \u2192 None",
"T9.3 нет сети → None (не падает)": "T9.3 no network \u2192 None (no crash)",
"T9.4 фолбэк на второй сервис (ipify упал → ifconfig.me)": "T9.4 fallback to second service (ipify down \u2192 ifconfig.me)",
"T10.1 say() пишет в last.log сразу": "T10.1 say() writes to last.log immediately",
"T11.1 ck: -c/-l 1984 и авто-UDP (-u) из конфига": "T11.1 ck: -c/-l 1984 and auto-UDP (-u) from config",
"T11.2 openvpn: --route bypass + redirect-gateway local def1": "T11.2 openvpn: --route bypass + redirect-gateway local def1",
"T11.3 внешний IP до/после в логе": "T11.3 external IP before/after in log",
"T11.4 pids сохранены": "T11.4 pids saved",
"T11.5 без bypass при full_tunnel → ВНИМАНИЕ в лог": "T11.5 no bypass on full_tunnel \u2192 WARNING in log",
"T11.6 внешний IP недоступен → «не определён»": "T11.6 external IP unavailable \u2192 \u201cunknown\u201d",
"T11.7 без UDP в конфиге и профиле → нет -u": "T11.7 no UDP in config and profile \u2192 no -u flag",
"T11.8 split tunnel → нет redirect-gateway, bypass есть": "T11.8 split tunnel \u2192 no redirect-gateway, bypass present",
"T11.9 _conn_status: столбик, значения выровнены": "T11.9 _conn_status: columnar, values aligned",
"T11.10 _fmt_bytes": "T11.10 _fmt_bytes",
"T13.1 .ovpn и внешние ключи скопированы в profiles": "T13.1 .ovpn and external keys copied into profiles\\",
"T13.2 пути в r переписаны на копии": "T13.2 paths in r rewritten to the copies",
"T13.3 несуществующий файл-ключ → пропущен, не упало": "T13.3 missing key file \u2192 skipped, no crash",
"T14.1 mgmt CONNECTED → up.is_set": "T14.1 mgmt CONNECTED \u2192 up.is_set",
"T14.2 load-stats → traffic (4000,8000)": "T14.2 load-stats \u2192 traffic (4000,8000)",
"T14.3 rate посчитан (≈3 KB/s ↓, ≈6 KB/s ↑)": "T14.3 rate computed (\u22483 KB/s \u2193, \u22486 KB/s \u2191)",
"T14.4 management-сокет сохранён": "T14.4 management socket kept",
"T15.1 _stop_vpn шлёт 'signal SIGTERM' в mgmt": "T15.1 _stop_vpn sends 'signal SIGTERM' to mgmt",
"T15.2 _stop_vpn обнуляет self.vpn": "T15.2 _stop_vpn clears self.vpn",
"T16.1 обрыв + reconnect → _connect перезапущен": "T16.1 drop + reconnect \u2192 _connect restarted",
"T16.2 в логе «переподключение (1/3)»": "T16.2 log contains \u201creconnecting (1/3)\u201d",
"T16.3 reconnect=False → без перезапуска": "T16.3 reconnect=False \u2192 no restart",
"T16.4 статус красный 'Cloak остановлен'": "T16.4 red status 'Cloak stopped'",
"T17.1 port_open: открытый порт → True": "T17.1 port_open: open port \u2192 True",
"T17.2 probe: открытый порт → None": "T17.2 probe: open port \u2192 None",
"T17.3 port_open: закрытый порт → False": "T17.3 port_open: closed port \u2192 False",
"T17.4 probe: закрытый порт → текст ошибки": "T17.4 probe: closed port \u2192 error text",
"T18.1 bypass_missing → 5-я строка «НЕТ ОБХОДА»": "T18.1 bypass_missing \u2192 5th line \u201cNO BYPASS\u201d",
"T18.2 bypass_missing → оранжевый статус": "T18.2 bypass_missing \u2192 orange status",
"T18.3 ext_ip=None → строки «VPN IP» нет": "T18.3 ext_ip=None \u2192 no \u201cVPN IP\u201d line",
"T19.1 занятый порт → ЗАНЯТ": "T19.1 busy port \u2192 BUSY",
"T19.2 неразрешимый хост → «не резолвится»": "T19.2 unresolvable host \u2192 \u201cdoesn't resolve\u201d",
"T20.1 save/load roundtrip": "T20.1 save/load roundtrip",
"T20.2 free_port → свободный порт 1024-65535": "T20.2 free_port \u2192 free port in 1024-65535",
"T12.1 migrate_dirs: папка переехала, ck → общий bin": "T12.1 migrate_dirs: folder moved, ck \u2192 shared bin\\",
"T21.1 inbox .dgcloak → профиль «u1@srvA» добавлен": "T21.1 inbox .dgcloak \u2192 profile \u201cu1@srvA\u201d added",
"T21.2 материализовано в profiles": "T21.2 materialized into profiles\\, udp=True from cloak",
"T21.3 повторный inbox → upsert без дубля": "T21.3 second inbox \u2192 upsert, no duplicate",
"T21.4 inbox .del → профиль удалён": "T21.4 inbox .del \u2192 profile removed",
"T22.1 ярлык снят на всех трёх десктопах": "T22.1 shortcut removed on all three desktops",
"T22.2 повторный вызов без ярлыка → пусто, без ошибок": "T22.2 second call without shortcut \u2192 empty, no errors",
"T23.1 переименование: файлы и ключи переехали в profiles/<new>": "T23.1 rename: files and keys moved to profiles/<new>",
"T23.2 safe-коллизия («e x» vs «e_x») → отказ": "T23.2 safe-name collision (\u00abe x\u00bb vs \u00abe_x\u00bb) \u2192 rejected",
"T23.3 правка без смены имени → ок": "T23.3 edit without rename \u2192 ok",
"T23.4 managed: поле имени readonly": "T23.4 managed: name field readonly",
"T24.1 say() пишет в logs/<профиль>/session.log": "T24.1 say() writes to logs/<profile>/session.log",
"T24.2 формат JSONL": "T24.2 JSONL format",
"T24.3 у каждого профиля свой журнал": "T24.3 each profile has its own log",
"T24.4 виджет перечитан с диска при переключении": "T24.4 widget reloaded from disk on profile switch",
"T24.5 ротация: session.log → session.log.old": "T24.5 rotation: session.log → session.log.old",
"T24.6 чтение .old+текущего: старый маркер виден": "T24.6 reading .old+current: old marker visible",
"T24.7 «Очистить лог» — файлы снесены, осталась метка": "T24.7 Clear log — files removed, marker line remains",
"T24.8 _log_ctx: строка ушла в журнал операции, не видимого профиля": "T24.8 _log_ctx: line went to the operation's log, not the viewed profile",
"T24.9 удаление профиля → logs/<name> снесён": "T24.9 profile delete → logs/<name> removed",
"T25.1 «Руководство» под разделителем, перед пунктом выхода": "T25.1 User guide below separator, before the exit item",
"T25.2 active → «Отключить VPN и выйти из программы»": "T25.2 active → «Disconnect VPN and exit»",
"T25.3 busy (идёт операция) → «Отключить VPN и выйти…»": "T25.3 busy (op in progress) → «Disconnect VPN and exit…»",
"T25.4 busy+exit → подтверждение «прервать операцию», выход": "T25.4 busy+exit → «abort operation» prompt, then exit",
"T25.5 idle+exit → выход без вопроса": "T25.5 idle+exit → exits without prompt",
"T26.1 «Подключаться при запуске» → data.json": "T26.1 Connect on startup checkbox → data.json",
"T26.2 снятая галочка → False": "T26.2 unchecked → False",
"T26.3 autoconnect+профиль → _first_run подключает выбранный": "T26.3 autoconnect+profile → _first_run connects the selected one",
"T26.4 autoconnect без профилей → нет подключения": "T26.4 autoconnect w/o profiles → no connection",
"T27.1 «Сворачивать в трей при запуске» → data.json": "T27.1 Start minimized to tray checkbox → data.json",
"T27.2 снятая галочка → False": "T27.2 unchecked → False",
"T27.3 флаг+трей → _first_run сворачивает в трей": "T27.3 flag+tray → _first_run minimizes to tray",
"T27.4 без трея → не сворачивает": "T27.4 no tray → does not minimize",
"T27.5 флаг off → не сворачивает": "T27.5 flag off → does not minimize",
"T28.1 «Отладочный лог OpenVPN» → data.json": "T28.1 OpenVPN debug log checkbox → data.json",
"T28.2 снятая галочка → False": "T28.2 unchecked → False",
"T29.1 «Запускаться вместе с Windows» → значение в Run": "T29.1 Start with Windows checkbox → value in Run key",
"T29.2 значение — команда запуска (exe/скрипт)": "T29.2 value is a launch command (exe/script)",
"T29.3 снятая галочка → значения нет": "T29.3 unchecked → value removed",
"T30.1 строка «Реальный IP:» под «VPN IP:»": "T30.1 Real IP line below VPN IP",
"T30.2 _copy_rows = {1: vpn, 2: isp}": "T30.2 _copy_rows = {1: vpn, 2: isp}",
"T30.3 клик по строке VPN IP → буфер = 5.6.7.8": "T30.3 click VPN IP line → clipboard = 5.6.7.8",
"T30.4 клик по «Подключено:» → буфер не менялся": "T30.4 click non-IP line → clipboard unchanged",
"T30.5 isp_ip=None → строки «Реальный IP» нет": "T30.5 isp_ip=None → no Real IP line",
"T31.1 tun_server_ip: 10.8.0.2 → 10.8.0.1": "T31.1 tun_server_ip: 10.8.0.2 → 10.8.0.1",
"T31.2 lan_ip() → IPv4 или None": "T31.2 lan_ip() → IPv4 or None",
"T31.3 строки заполнены: lan/cli/srv": "T31.3 fields filled: lan/cli/srv",
"T31.4 клик по tun-адресу → буфер": "T31.4 click tun address → clipboard",
"T31.5 tun_ip=None → «—»": "T31.5 tun_ip=None → «—»",
"T32.0 def1_routes_present парсит таблицу": "T32.0 def1_routes_present parses the table",
"T32.1 «Пауза» → route delete def1 через tun-gw": "T32.1 Pause → route delete def1 via tun-gw",
"T32.2 def1 сняты → paused=True": "T32.2 def1 removed → paused=True",
"T32.3 статус «Пауза — трафик идёт напрямую»": "T32.3 status «Paused — traffic goes directly»",
"T32.4 кнопка → «Возобновить»": "T32.4 button → «Resume»",
"T32.5 пункт трея «Возобновить VPN» сразу после «Открыть»": "T32.5 tray item «Resume VPN» right after «Open»",
"T32.5b тултип кнопки → «Возобновить VPN»": "T32.5b button tooltip → «Resume VPN»",
"T32.6 «Возобновить» → route add, paused сброшен": "T32.6 Resume → route add, paused cleared",
"T32.7 сторож: вернувшиеся def1 сняты повторно": "T32.7 watchdog: re-appeared def1 removed again",
"T32.8 без tun_ip → молчим, route не трогаем": "T32.8 no tun_ip → stay silent, no route calls",
"T32.9 paused-иконка рисуется (64px)": "T32.9 paused icon renders (64px)",
"T32.10 тултип обратно → «Приостановить VPN»": "T32.10 tooltip back → «Pause VPN»",
"T33.1 комбобокс по ширине «Подключить+Пауза»": "T33.1 combobox width matches «Connect+Pause» pair",
"T33.2 высота окна не прыгает от многострочного статуса": "T33.2 window height stable across multiline status",
"T33.3 geometry при «Дополнительно» — одна и та же ширина": "T33.3 geometry calls keep the same width on Advanced toggle",
}

AD = {
"U1.1 все RU-строки имеют EN-перевод": "U1.1 every RU string has an EN translation",
"U1.2 плейсхолдеры {kw} и %s совпадают RU↔EN": "U1.2 placeholders {kw} and %s match RU\u2194EN",
"U2.1 _run_step переводит заголовок (self.t(title))": "U2.1 _run_step translates the title (self.t(title))",
"U2.2 все заголовки STEPS переводятся в en": "U2.2 all STEPS titles translate to en",
"U2.3 run_script/run_script_stream ставят env DG_LANG=": "U2.3 run_script/run_script_stream set env DG_LANG=",
"U2.4 все вызовы ovpn-mgmt.py с env DG_LANG=": "U2.4 every ovpn-mgmt.py call with env DG_LANG=",
"U2.5 нет remote-команд с несколькими sudo в одной строке": "U2.5 no remote command has multiple sudo in one line",
"U2.6 в pipefail-скриптах нет `| grep -q`": "U2.6 no `| grep -q` in pipefail scripts",
"U3.1 скрипты с кириллицей имеют хелпер _()": "U3.1 scripts with Cyrillic have the _() helper",
"U3.2 нет голого echo/printf с кириллицей": "U3.2 no bare echo/printf with Cyrillic",
"U4.1 sudo_mode=pw → 'sudo -S -p ''' + пароль в sudo_pw": "U4.1 sudo_mode=pw \u2192 'sudo -S -p ''' + password in sudo_pw",
"U4.2 root → без sudo-префикса": "U4.2 root \u2192 no sudo prefix",
"U4.3 nopasswd → sudo -n": "U4.3 nopasswd \u2192 sudo -n",
"U4.4 openssh без пароля → -n и -i k": "U4.4 openssh w/o password \u2192 -n and -i k",
"U4.5 openssh с паролем → без -n (нужен stdin для sudo -S)": "U4.5 openssh with password \u2192 no -n (stdin needed for sudo -S)",
"U4.6 putty с паролем → -pw": "U4.6 putty with password \u2192 -pw",
"U4.7 putty с ppk → -i k.ppk": "U4.7 putty with ppk \u2192 -i k.ppk",
"U4.8 ключ отвергнут → argv через plink -pw, без -i": "U4.8 key rejected \u2192 argv via plink -pw, no -i",
"U4.9 upload тоже → pscp -pw": "U4.9 upload likewise \u2192 pscp -pw",
"U5.1 run_script: env DG_LANG= в команде": "U5.1 run_script: env DG_LANG= in command",
"U5.2 run_script: один sudo-префикс (pw-режим)": "U5.2 run_script: single sudo prefix (pw mode)",
"U5.3 пароль sudo уходит в stdin один раз": "U5.3 sudo password goes to stdin once",
"U5.4 скрипт уходит в /tmp/dgadm-…": "U5.4 script lands in /tmp/dgadm-\u2026",
"U6.1 parse_section средний блок": "U6.1 parse_section middle block",
"U6.2 parse_section последний блок": "U6.2 parse_section last block",
"U6.3 parse_section нет маркера": "U6.3 parse_section missing marker",
"U6.4 _probe_note: известный токен → текст": "U6.4 _probe_note: known token \u2192 text",
"U6.5 _probe_note: неизвестный токен → as-is": "U6.5 _probe_note: unknown token \u2192 as-is",
"U6.6 _probe_note: неполные args → шаблон без подстановки": "U6.6 _probe_note: incomplete args \u2192 template unsubstituted",
"U6.7 _probe_note: en → ASCII": "U6.7 _probe_note: en \u2192 ASCII",
"U6.8 valid_cn": "U6.8 valid_cn",
"U6.9 uid_to_b64url": "U6.9 uid_to_b64url",
"U6.10 _fmt_rate: ∞ для безлимита": "U6.10 _fmt_rate: \u221e for unlimited",
"U6.11 _fmt_rate значения": "U6.11 _fmt_rate values",
"U6.12 _fmt_quota: ∞ если оба безлимитны": "U6.12 _fmt_quota: \u221e when both unlimited",
"U6.13 _fmt_bytes G/M": "U6.13 _fmt_bytes G/M",
"U6.14 _apply_probe разобрал 8 шагов": "U6.14 _apply_probe parsed 8 steps",
"U6.15 deployed=True по cloak=ok": "U6.15 deployed=True on cloak=ok",
"U6.16 reboot_required по токену": "U6.16 reboot_required by token",
"U6.17 note sysupd локализована": "U6.17 sysupd note localized",
"U6.18 _apply_probe без STEP_* → False": "U6.18 _apply_probe w/o STEP_* \u2192 False",
"U6.19 ckclient: UDP при proto=udp": "U6.19 ckclient: UDP when proto=udp",
"U6.20 ckclient: RemotePort из ck_port": "U6.20 ckclient: RemotePort from ck_port",
"U6.21 ckclient: per-user mask → ServerName": "U6.21 ckclient: per-user mask \u2192 ServerName",
"U6.22 ckclient: proto=tcp → UDP False": "U6.22 ckclient: proto=tcp \u2192 UDP False",
"U6.23 make_ovpn: proto и PEM-блоки на месте": "U6.23 make_ovpn: proto and PEM blocks present",
"U7.1 ServerDialog: без имени → отказ": "U7.1 ServerDialog: no name \u2192 reject",
"U7.2 ServerDialog: нет ключа/пароля → отказ": "U7.2 ServerDialog: no key/password \u2192 reject",
"U7.3 ServerDialog: пароль → ок": "U7.3 ServerDialog: password \u2192 ok",
"U7.4 ServerDialog: порт 99999 → отказ": "U7.4 ServerDialog: port 99999 \u2192 reject",
"U7.5 ServerDialog: .pub → подмена на приватный": "U7.5 ServerDialog: .pub \u2192 swapped to private key",
"U7.6 ServerDialog.apply → dict с host/user": "U7.6 ServerDialog.apply \u2192 dict with host/user",
"U7.7 UserDialog: пробел в CN → отказ": "U7.7 UserDialog: space in CN \u2192 reject",
"U7.8 UserDialog: не-число в сроке → отказ": "U7.8 UserDialog: non-number expiry \u2192 reject",
"U7.9 UserDialog: валидный минимум → ок": "U7.9 UserDialog: valid minimum \u2192 ok",
"U7.10 UserDialog.apply: 0 дней → FAR_FUTURE, 0 Мбит → INT64_MAX": "U7.10 UserDialog.apply: 0 days \u2192 FAR_FUTURE, 0 Mbit \u2192 INT64_MAX",
"U7.11 UserDialog.apply: sessions clamp ≥1": "U7.11 UserDialog.apply: sessions clamped \u22651",
"U7.12 apply: rate Мбит/с → байт/с (10M → 1250000)": "U7.12 apply: rate Mbit/s \u2192 bytes/s (10M \u2192 1250000)",
"U7.13 apply: expiry ≈ now+7d": "U7.13 apply: expiry \u2248 now+7d",
"U8.1 все auto-лабы в supported-карте _step_audit": "U8.1 all auto labs covered by _step_audit's supported map",
"U9.1 predeploy-save: пишет /etc/dgcloak/predeploy.env": "U9.1 predeploy-save: writes /etc/dgcloak/predeploy.env",
"U9.2 predeploy-save: baseline не перезаписывается": "U9.2 predeploy-save: baseline never overwritten",
"U9.3 снимок содержит %s": "U9.3 snapshot contains %s",
"U9.4 purge читает predeploy.env": "U9.4 purge reads predeploy.env",
"U9.5 purge: STOPPED_SVC → systemctl enable --now": "U9.5 purge: STOPPED_SVC \u2192 systemctl enable --now",
"U9.6 purge: DOCKER_POLICY_* → docker update --restart": "U9.6 purge: DOCKER_POLICY_* \u2192 docker update --restart",
"U9.7 purge: DOCKER_START → docker start": "U9.7 purge: DOCKER_START \u2192 docker start",
"U9.8 purge: ip_forward из снимка, не хардкод 0": "U9.8 purge: ip_forward from snapshot, not hardcoded 0",
"U9.9 purge: FW=none → снос пакета nftables": "U9.9 purge: FW=none \u2192 nftables package removed",
"U9.10 purge: чужой nft → re-dump ruleset (fix воскрешения таблицы)": "U9.10 purge: foreign nft \u2192 ruleset re-dump (table-resurrection fix)",
"U9.11 purge: ufw-оригиналы (UFW_FWD_POLICY/UFW_IPFWD)": "U9.11 purge: ufw originals (UFW_FWD_POLICY/UFW_IPFWD)",
"U9.12 purge: firewalld masq оставляем если был": "U9.12 purge: firewalld masq kept if pre-existing",
"U9.13 purge: pre-existing openvpn/easy-rsa не сносятся": "U9.13 purge: pre-existing openvpn/easy-rsa kept",
"U9.14 audit шаг вызывает predeploy-save.sh": "U9.14 audit step calls predeploy-save.sh",
"U9.15 _step_fw записывает FW= в снимок": "U9.15 _step_fw writes FW= into snapshot",
"U9.16 _step_cloak записывает STOPPED_SVC": "U9.16 _step_cloak writes STOPPED_SVC",
"U9.17 _step_cloak записывает docker-политики и DOCKER_START": "U9.17 _step_cloak writes docker policies and DOCKER_START",
"U9.18 кнопка переименована в «Вернуть сервер»": "U9.18 button renamed to \u201cRevert server\u201d",
"U9.19 purge: restart dockerd перед docker start + варн при сбое": "U9.19 purge: dockerd restart before docker start + warning on failure",
"U9.20 ключ-фолбэк помечает host:port (PerSourcePenalties)": "U9.20 key fallback marks host:port (PerSourcePenalties)",
"U9.21 _step_nat firewalld: --add-forward в зоне": "U9.21 _step_nat firewalld: --add-forward in zone",
"U9.22 _step_nat firewalld: пост-проверка ": "U9.22 _step_nat firewalld: query-masquerade/query-forward post-check",
"U9.23 снимок: FIREWALLD_FWD (forward до нас)": "U9.23 snapshot: FIREWALLD_FWD (forward state before us)",
"U9.24 purge: FWD=no → --remove-forward": "U9.24 purge: FWD=no \u2192 --remove-forward",
"U9.25 probe: firewalld-ветка NAT заведена по FW=firewalld": "U9.25 probe: firewalld NAT branch gated by FW=firewalld",
"U10.1 метка: deployed + все шаги ok → ✓": "U10.1 mark: deployed + all steps ok \u2192 \u2713",
"U10.2 метка: deployed + пропущенный шаг → ✓-": "U10.2 mark: deployed + skipped step \u2192 \u2713-",
"U10.3 метка: warn приоритетнее deployed → ⚠": "U10.3 mark: warn beats deployed \u2192 \u26a0",
"U10.4 метка: fail на недодеплое → ⚠": "U10.4 mark: fail on partial deploy \u2192 \u26a0",
"U10.5 метка: deployed без steps → ✓-": "U10.5 mark: deployed without steps \u2192 \u2713-",
"U10.6 метка: не развёрнут, нет steps → пусто": "U10.6 mark: not deployed, no steps \u2192 blank",
"U10.7 метка: все ok, но deployed снят → пусто": "U10.7 mark: all ok but deployed cleared \u2192 blank",
"U10.8 онлайн: до первой пробы → …": "U10.8 online: before first probe \u2192 \u2026",
"U10.9 онлайн: доступен ✓ / недоступен ✗": "U10.9 online: reachable \u2713 / unreachable \u2717",
"U10.10 cloak на неразвёрнутом → — (н/д)": "U10.10 cloak on undeployed \u2192 \u2014 (n/a)",
"U10.11 ssh на неразвёрнутом всё равно пробуется → …": "U10.11 ssh on undeployed still probed \u2192 \u2026",
"U11.1 нет VPN": "U11.1 no VPN\\ dir \u2192 client not found",
"U11.2 provision: inbox .dgcloak атомарно": "U11.2 provision: inbox .dgcloak placed atomically",
"U11.3 delete → .del маркер, .dgcloak снят": "U11.3 delete \u2192 .del marker, .dgcloak lifted",
"U11.4 серия маркеров → остался только .dgcloak": "U11.4 marker series \u2192 only .dgcloak left",
"U12.1 say/vsay → deploy.log создан под sanitize(имя)": "U12.1 say/vsay \u2192 deploy.log created under sanitize(name)",
"U12.2 JSONL: verbose-флаг и текст строки": "U12.2 JSONL: verbose flag and line text",
"U12.3 users-канал → users.log отдельным файлом": "U12.3 users channel \u2192 separate users.log file",
"U12.4 после рестарта журнал поднят с диска": "U12.4 journal restored from disk after restart",
"U12.5 ротация по размеру: .log → .log.old": "U12.5 size rotation: .log \u2192 .log.old",
"U12.6 «очистить лог»: .old снят, в .log только отметка": "U12.6 \u201cclear log\u201d: .old gone, .log keeps only the mark",
"U12.7 удаление сервера → папка журналов снесена": "U12.7 server deletion \u2192 journal folder removed",
"U13.1 журналы: память и папка за новым именем": "U13.1 journals: memory and folder under new name",
"U13.2 показанный журнал → новое имя": "U13.2 shown journal \u2192 new name",
"U13.3 ключи переехали, s«key» переписан": "U13.3 keys moved, s\u00abkey\u00bb rewritten",
"U13.4 user_bundles/<new> на месте, <old> снесён": "U13.4 user_bundles/<new> in place, <old> removed",
"U13.5 u1: .del старого + .dgcloak нового, pname обновлён": "U13.5 u1: old .del + new .dgcloak, pname updated",
"U13.6 u2 без бандла: pname=<old>, маркеров нет": "U13.6 u2 without bundle: pname=<old>, no markers",
"U13.7 delete по pname → .del бьёт в старое имя": "U13.7 delete by pname \u2192 .del hits old name",
"U14.1 add: внешние ключи скопированы в keys/<srv>": "U14.1 add: external keys copied into keys/<srv>",
"U14.2 повторный stash: пути не меняются": "U14.2 re-stash: paths unchanged",
"U14.3 edit: новый внешний ключ стешен": "U14.3 edit: new external key stashed",
"U14.4 rename: key+ppk переехали за новым именем": "U14.4 rename: key+ppk moved with new name",
"U14.5 несуществующий файл → путь как был": "U14.5 missing file \u2192 path kept as-is",
"U15.1 в файле нет открытых password/admin_uid": "U15.1 no plaintext password/admin_uid in file",
"U15.2 в памяти секреты остались открытыми": "U15.2 secrets stay plaintext in memory",
"U15.3 load_data → расшифровано обратно": "U15.3 load_data \u2192 decrypted back",
"U15.4 пустой секрет → без *_dp и без поля": "U15.4 empty secret \u2192 no *_dp, no field",
}

LG = {
"E1 say до выбора сервера — не падает": "E1 say before server selection — doesn't crash",
"E2 очистка без сервера: строка «Лог очищен.» в виджете": "E2 clear without server: \u201cLog cleared.\u201d in widget",
"A1 say на вкладке deploy → журнал SrvA[deploy]": "A1 say on deploy tab \u2192 SrvA[deploy] journal",
"A1 виджет deploy показывает строку": "A1 deploy widget shows the line",
"A1 виджет users пуст": "A1 users widget empty",
"A2 say на вкладке users → журнал SrvA[users]": "A2 say on users tab \u2192 SrvA[users] journal",
"A2 виджет users показывает": "A2 users widget shows it",
"A3 vsay в журнале": "A3 vsay lands in journal",
"A3 vsay скрыта при verbose off": "A3 vsay hidden with verbose off",
"A3 verbose on → старые vsay-строки видны": "A3 verbose on \u2192 old vsay lines visible",
"A3 verbose off → снова скрыты": "A3 verbose off \u2192 hidden again",
"A4 журнал B отделён": "A4 journal B separate",
"A4 виджет после переключения = журнал B": "A4 widget after switch = journal B",
"A4 возврат на A → журнал A восстановлен": "A4 back to A \u2192 journal A restored",
"A5 флаг сохранён в сервере A": "A5 flag saved on server A",
"A5 у B галочка выкл": "A5 checkbox off on B",
"A5 возврат на A → галочка вкл (запомнена)": "A5 back to A \u2192 checkbox on (remembered)",
"B1 после очистки в журнале B[deploy] только «Лог очищен.»": "B1 after clear, B[deploy] journal has only \u201cLog cleared.\u201d",
"B1 users-канал B не тронут": "B1 users channel of B untouched",
"B1 виджет показывает «Лог очищен.»": "B1 widget shows \u201cLog cleared.\u201d",
"B1 журнал A целиком на месте": "B1 journal A fully intact",
"C1 строка op ушла в журнал A": "C1 op line went to journal A",
"C1 журнал B чист от op-строки": "C1 journal B free of op line",
"C1 виджет B/deploy показывает placeholder": "C1 B/deploy widget shows placeholder",
"C3 users-вкладка B без placeholder, журнал B на месте": "C3 B users tab without placeholder, journal B intact",
"C5 «Лог скопирован» в журнал ПОКАЗАННОГО сервера (B)": "C5 \u201cLog copied\u201d lands in the SHOWN server's journal (B)",
"C6 «Лог очищен.» в журнале B при op на A": "C6 \u201cLog cleared.\u201d in B's journal while op runs on A",
"C6 журнал A не пострадал": "C6 journal A untouched",
"C4 после op виджет A показывает его строки (без placeholder)": "C4 after op, A's widget shows its lines (no placeholder)",
"D1 allow: скрипт вернул ok": "D1 allow: script returned ok",
"D1 порт 18088 открыт": "D1 port 18088 open",
"D2 deny ok": "D2 deny ok",
"D3 повторный deny → «правило не найдено» или ok": "D3 repeated deny \u2192 \u201crule not found\u201d or ok",
"D3 порт 18088 закрыт": "D3 port 18088 closed",
"D5 fw-лог в журнале СЕРВЕРА ДИАЛОГА, а не выбранного": "D5 fw log lands in the DIALOG server's journal, not the selected one",
"D7 users-операция пишет в users-канал": "D7 users op writes to the users channel",
"F1 track: PID в реестре и в pids.json": "F1 track: PID in registry and pids.json",
"F1 untrack живого — остаётся в реестре": "F1 untrack of live proc — stays in registry",
"F1 kill_child_procs: процесс убит, реестр пуст": "F1 kill_child_procs: process killed, registry empty",
"F2 cleanup_stale: живой сирота убит, мёртвый проигнорирован": "F2 cleanup_stale: live orphan killed, dead one ignored",
"F2 pids.json обнулён": "F2 pids.json emptied",
"F2b чужой процесс с нашим PID не убит": "F2b foreign process with our PID not killed",
"F3 нет файлов → None": "F3 no files \u2192 None",
"F3 мёртвый PID → None": "F3 dead PID \u2192 None",
"F3 старая папка (старый клиент) → её хост": "F3 old folder (old client) \u2192 its host",
"F3 новая папка приоритетнее": "F3 new folder wins",
"F3 bypass_ip профиля приоритетнее RemoteHost": "F3 profile's bypass_ip beats RemoteHost",
"F4 свой сервер → вопрос задан, отказ → False": "F4 own server \u2192 prompt shown, refusal \u2192 False",
"F4 чужой сервер → без вопроса, True": "F4 foreign server \u2192 no prompt, True",
"F4 VPN не активен → True без вопроса": "F4 VPN inactive \u2192 True, no prompt",
"F5 reboot/purge/deploy-all/шаг nat на своём VPN-сервере — ": "F5 reboot/purge/deploy-all/nat step on own VPN server — warned",
"F5 read-only шаг audit — без предупреждения, запущен": "F5 read-only audit step — no warning, runs",
"F5 чужой сервер: только штатное подтверждение": "F5 foreign server: only the standard confirmation",
"G1 probe пишет в deploy-канал": "G1 probe writes to deploy channel",
"G1 шаги заполнены по probe": "G1 steps populated from probe",
"G1 users-канал не тронут": "G1 users channel untouched",
"G2 юзер создан (в реестре с UID)": "G2 user created (in registry with UID)",
"G2 строка «создан» в users-канале": "G2 \u201ccreated\u201d line in users channel",
"G2 deploy-канал не тронут": "G2 deploy channel untouched",
"G2 виджет deploy без строк юзера": "G2 deploy widget free of user lines",
"G3 экспорт: .dgcloak на месте": "G3 export: .dgcloak produced",
"G3 строка «Конфиг …» в users-канале": "G3 \u201cConfig \u2026\u201d line in users channel",
"G4 дубль CN отклонён без операции": "G4 duplicate CN rejected without an op",
"G5 юзер удалён из реестра": "G5 user removed from registry",
"G5 «отозван и удалён» в users-канале": "G5 \u201crevoked and removed\u201d in users channel",
"G5 сертификат отозван/отсутствует (не ошибка)": "G5 cert revoked/absent (not an error)",
"G5 CN не остался в реестре юзеров на сервере": "G5 CN gone from server-side user registry",
}

def test_table(title, desc, items):
    lis = "".join("<tr><td>%s</td></tr>" % i for i in items)
    return ("<h2>%s <span class=count>%d checks</span></h2>"
            "<p>%s</p><table>%s</table>"
            % (html.escape(title), len(items), desc, lis))

cl = tr_list("clienttest.py", CL)
ad = tr_list("admintest.py", AD)
lg = tr_list("logtest.py", LG)
mx_items = [
    "M1 lab entry exists in the admin registry",
    "M2 SSH + sudo (preflight)",
    "M3 distro/version from detect.sh = matrix expectation",
    "M4 fw backend from fw-manage.sh = expectation",
    "M5 STEP_cloak from probe.sh = expected deployed",
    "M6 registry deployed flag = probe reality",
    "M7 (id,version) covered by _step_audit's supported map",
    "M8 probe localization: raw tokens ASCII; notes in UI language",
]
stands = ("Ubuntu 20.04 \u00b7 Ubuntu 22.04 \u00b7 Ubuntu 24.04 \u00b7 "
          "Ubuntu 26.04 \u00b7 Debian 11 \u00b7 Debian 12 \u00b7 Debian 13 "
          "\u00b7 prod dlmedia (read-only) \u00b7 prod london-aws (read-only)")

tests_body = (
    "<span class=stat>Total: %d checks</span>"
    % (len(cl) + len(ad) + len(lg) + 79)
    + test_table("tests/clienttest.py — client (offline)",
                 "Localization, profile dialog and .dgcloak import, "
                 "runtime.ovpn, bypass route, pids registry, external IP, "
                 "connect flow on fakes, management socket on live localhost, "
                 "auto-reconnect, port probes, status lines, save/load, "
                 "inbox provisioning from the admin app.", cl)
    + test_table("tests/admintest.py — admin app (offline)",
                 "STRINGS_EN localization audit; regressions DG_LANG/"
                 "single-sudo/pipefail-grep/self.t(title); SSH argv per sudo "
                 "mode and key\u2194password fallback (PerSourcePenalties); "
                 "parse_section/_probe_note/_apply_probe; make_ckclient/"
                 "make_ovpn; ServerDialog/UserDialog; labs.json\u2194supported; "
                 "predeploy snapshot and revert branches; server marks and "
                 "online states; provision_client into the client inbox; "
                 "firewalld forward; on-disk journals (JSONL, rotation). "
                 "113 PASS at runtime \u2014 some checks are parameterized.", ad)
    + test_table("tests/logtest.py — journals + live user lifecycle",
                 "Per-server deploy/users journals, vsay/verbose, "
                 "clear/copy during another server's op, tracking and "
                 "reaping of plink/pscp orphans, own-VPN detection and "
                 "warnings, fw allow/deny on a live lab, full user lifecycle "
                 "on a live lab (create\u2192export\u2192dup\u2192delete).", lg)
    + test_table("tests/matrix.py — live labs",
                 "Read-only smoke against every auto=true stand "
                 "(79 checks per run; 1 expected FAIL \u2014 the london-aws "
                 "entry is auto=false pending manual cleanup). Stands: "
                 + stands + ".", mx_items))
open(REPO + r"\docs\tests-en.html", "w", encoding="utf-8").write(
    page("Automated tests", tests_body,
         "Four suites: client and admin offline units, journal harness with "
         "a live user lifecycle, live-lab matrix."))

print("OK: -en files written (%d commits, %d bugs, %d+%d+%d checks)"
      % (tot, len(BUGS), len(cl), len(ad), len(lg)))
