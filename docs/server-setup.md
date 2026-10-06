# Эталонное развёртывание OpenVPN + Cloak на Ubuntu 24.04

Чек-лист ручной установки, **пройден на тестовой VM** (Hyper-V, Ubuntu 24.04.5 minimal,
192.168.50.x). Документ — спецификация для `cloak_admin.py`; то, что ниже, проверено фактом,
если не стоит `⚠ ПРОВЕРИТЬ` (осталось непроверенным или требует сверки с доками).

## Схема

```
клиент: openvpn ─► ck-client (127.0.0.1:1984, UDP) ══TLS (443/tcp)══► ck-server ─► udp 127.0.0.1:1194 ─► openvpn-server
```

- Наружу слушает **только ck-server** на 443/tcp. OpenVPN слушает `127.0.0.1` и недоступен снаружи.
- Протокол между Cloak и OpenVPN задаётся в `ProxyBook` ck-server (`["udp","127.0.0.1:1194"]`)
  и должен совпадать с `proto` в server.conf и флагом `-u` у ck-client.
- Юзер = сертификат OpenVPN (easyrsa) + UID Cloak (в `userinfo.db` через admin-API).

## Проверенные факты и грабли (важно для приложения)

1. **easyrsa жрёт stdin.** `gen-req`/`sign-req` при отсутствии ответа читают stdin —
   в скрипте, поданном через `ssh host bash -s`, они съедают следующие строки скрипта.
   Правило: скрипты копировать файлом + `ssh -n`, а промпты кормить явно:
   `echo | ./easyrsa gen-req NAME nopass`, `echo yes | ./easyrsa sign-req client NAME`.
   Для `build-ca` тоже `echo |`.
2. **`ufw` и `iptables-persistent` взаимоисключающие** (Conflicts в пакетах — установка одного
   сносит другой). В минимальном образе Ubuntu Server 24.04 нет ни того ни другого.
   **Выбрано: iptables-persistent** — весь ruleset одним файлом `/etc/iptables/rules.v4`,
   приложение управляет им атомарно через `iptables-restore`. ufw не используем.
3. В minimal-образе **нет iptables** — ставится как зависимость iptables-persistent (nft-бэкенд).
4. Релизы Cloak — **голые бинарники** без архива: `ck-server-linux-amd64-vX.Y.Z`.
   Флаги v2.12.0 проверены: `-k`/`-key` → `<public>,<private>`, `-u`/`-uid` → UID.
5. **Admin-API работает без SSH** — проверено: `ck-client -a <AdminUID>` на машине админа,
   `GET /admin/users` → `[]`, `POST` → 201, юзер в списке. См. п.5.
6. Windows `curl.exe` к `https://<IP>/` падает в schannel (нет SNI на IP) — маскировку
   проверять браузером или curl с `--resolve <домен>:443:<IP>`.

## 0. Исходные данные

- [x] VM Ubuntu 24.04 Server (minimal), пользователь с sudo NOPASSWD, SSH по ключу.
- `EXT_IF` — внешний интерфейс: `ip route show default | awk '{print $5; exit}'` (у лабы `eth0`).
- `MASK_DOMAIN` — домен маскировки (RedirAddr/ServerName), у лабы `www.bing.com`.
- `SSH_PORT` — параметр (дефолт 22; у боевых Lightsail пользователя — 8081).
- `OVPN_PROTO` — `udp` (рекомендуемо) или `tcp`, единый для трёх мест.

## 1. OpenVPN + PKI (easyrsa) — выполнено

```bash
apt-get update && apt-get install -y openvpn easy-rsa   # openvpn 2.6.19, easy-rsa 3.1.7
make-cadir ~/openvpn-ca && cd ~/openvpn-ca   # под sudo → CA окажется в /root/openvpn-ca (нормально)
./easyrsa init-pki
echo | ./easyrsa build-ca nopass
echo | ./easyrsa gen-req server nopass
echo yes | ./easyrsa sign-req server server
./easyrsa gen-dh          # 2048 бит, ~1 мин
./easyrsa gen-crl
openvpn --genkey secret ta.key
# install -m… в /etc/openvpn/server/: ca.crt, server.crt, server.key(600), dh.pem, crl.pem, ta.key(600)
```

`/etc/openvpn/server/server.conf` (как в прежней версии документа; ключевые строки):

```
port 1194
proto udp
local 127.0.0.1
dev tun
topology subnet
server 10.8.0.0 255.255.255.0
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 1.1.1.1"
push "dhcp-option DNS 8.8.8.8"
ca ca.crt / cert server.crt / key server.key / dh dh.pem
tls-crypt ta.key
crl-verify crl.pem          # CRL перечитывается на каждый новый handshake — рестарт НЕ нужен (проверено)
remote-cert-tls client
cipher AES-256-GCM / auth SHA256
keepalive 10 120
user nobody / group nogroup
persist-key / persist-tun
client-config-dir ccd
status /var/log/openvpn-status.log
verb 3
```

```bash
mkdir -p /etc/openvpn/server/ccd
systemctl enable --now openvpn-server@server
```

- [x] Сервис active, `ss -ulpn` → `127.0.0.1:1194`.

## 2. Форвардинг и firewall — выполнено (iptables-persistent)

```bash
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-vpn.conf && sysctl --system
apt-get install -y iptables-persistent
```

`/etc/iptables/rules.v4` (генерируется целиком; SSH-порт — параметр):

```
*filter
:INPUT DROP / :FORWARD DROP / :OUTPUT ACCEPT
-A INPUT -i lo -j ACCEPT
-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A INPUT -p icmp -j ACCEPT
-A INPUT -p udp --dport 68 -j ACCEPT                    # DHCP-клиент VM — без этого renew сломается!
-A INPUT -p tcp --dport <SSH_PORT> -m conntrack --ctstate NEW -j ACCEPT
-A INPUT -p tcp --dport 443 -m conntrack --ctstate NEW -j ACCEPT
-A FORWARD -i tun0 -o <EXT_IF> -j ACCEPT
-A FORWARD -i <EXT_IF> -o tun0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
COMMIT
*nat
-A POSTROUTING -s 10.8.0.0/24 -o <EXT_IF> -j MASQUERADE
COMMIT
```

```bash
iptables-restore < /etc/iptables/rules.v4    # атомарно; текущая SSH-сессия живёт (ESTABLISHED)
netfilter-persistent save && systemctl enable netfilter-persistent
```

- [x] INPUT DROP + SSH/443, FORWARD tun0↔eth0, MASQUERADE; применено без разрыва SSH.
- [x] Перезагрузка VM → правила восстановились (netfilter-persistent enabled).

## 3. ck-server — выполнено

```bash
# https://api.github.com/repos/cbeuw/Cloak/releases/latest → tag_name
curl -sLO https://github.com/cbeuw/Cloak/releases/download/v2.12.0/ck-server-linux-amd64-v2.12.0
install -m755 ck-server-linux-amd64-v2.12.0 /usr/local/bin/ck-server
ck-server -k   # → "<public>,<private>"
ck-server -u   # → AdminUID
```

`/etc/ck-server/ckserver.json` (`640 root:cloak`, внутри приватный ключ):

```json
{
  "ProxyBook": { "openvpn": ["udp", "127.0.0.1:1194"] },
  "BindAddr": [":443"],
  "BypassUID": [],
  "RedirAddr": "www.bing.com",
  "PrivateKey": "<private>",
  "AdminUID": "<admin uid>",
  "DatabasePath": "/var/lib/ck-server/userinfo.db",
  "BackupDirPath": "/var/lib/ck-server/db-backup"
}
```

Сервис под отдельным юзером `cloak` (system user), биндинг 443 через `CAP_NET_BIND_SERVICE`:

```
[Service]
User=cloak / Group=cloak
ExecStart=/usr/local/bin/ck-server -c /etc/ck-server/ckserver.json
Restart=always / RestartSec=3 / LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

- [x] `systemctl enable --now ck-server` → active, `ss -tlpn` → `*:443`.
- [x] Паблик-ключ и AdminUID сохранены в `/etc/ck-server/publickey.txt` / `adminuid.txt` (600).
- [ ] Маскировка наружу: браузером `https://<домен>` через `--resolve`. ⚠ ПРОВЕРИТЬ на VPS с реальным IP.

## 4. Юзер: сертификат + UID — выполнено

```bash
cd /root/openvpn-ca
echo | ./easyrsa gen-req ivan nopass
echo yes | ./easyrsa sign-req client ivan
ck-server -u   # новый UID юзера
```

## 5. Admin-API Cloak — проверено и работает

На машине админа: `ck-client -a <AdminUID> -s <server> -p 443 -l <localport> -c ckclient-admin.json`
(в конфиге `UID` = AdminUID) → лог `API base is 127.0.0.1:<localport>`.

| Метод | URL | Результат в лабе |
|---|---|---|
| GET | `/admin/users` | `[]` → после POST список с юзерами |
| POST | `/admin/users/<uid-b64url>` | `201` при теле `{UID,SessionsCap,UpRate,DownRate,UpCredit,DownCredit,ExpiryTime}` |
| DELETE | `/admin/users/<uid-b64url>` | `200`, юзер исчезает из GET мгновенно |

- UID в URL — **base64url**: `+`→`-`, `/`→`_` (сохраняя `=`). Процент-кодирование `%2B`
  даёт `400 illegal base64 data` — проверено.
- **DELETE работает мгновенно, без рестарта ck-server.** Сервер отвечает на новые
  попытки `unauthorised UID ... "UID does not correspond to a user"` (journalctl).
  На клиенте это выглядит как `Attempting to start a new session` без `established`
  → OpenVPN `Server poll timeout`. Уже установленная сессия продолжает жить.
- **Admin-ck-client засыпает по idle** (~минута без запросов → session закрывается,
  API перестаёт отвечать, `HTTP:000`). Приложение должно (пере)запускать
  `ck-client -a` на каждую пачку операций и/или уметь рестартовать при 000.
- **`ExpiryTime: 0` = unix-эпоха = юзер ПРОСРОЧЕН** — проверено в лабе: подключение отвергается
  с `cipher: message authentication failed` на клиенте. «Бессрочный» — ставить далёкое будущее
  (в лабе 2000000000 ≈ 2033).
- **`SessionsCap: 0` = ноль сессий** — сервер узнаёт UID, но сразу
  `Terminating active user ... no session left` + `Sessions cap has reached`;
  клиент висит в `Attempting to start a new session` без established.
  «Безлимит» = большое число (16+).
- **`UpRate/DownRate: 0` — КРАШ СЕРВЕРА**: `panic: token bucket capacity is not > 0`
  в `MakeValve`/`userpanel.GetUser` — сервис уходит в restart-loop, все коннекты
  рвутся RST/EOF на клиенте. «Безлимит скорости» = большое число (1e9).
- **`UpCredit/DownCredit: 0` = ноль трафика** — сервер пишет `unauthorised UID ... No upload credit left`
  (видно в `journalctl -u ck-server`; клиент при этом просто висит в TLS handshake — диагностика
  ТОЛЬКО по серверному логу!). «Безлимит» — большое число (в лабе int64 max).
  UpRate/DownRate — байт/с, кредиты — байт накопленного трафика.
- POST по существующему UID — **upsert** (обновил ExpiryTime без удаления юзера).
- В клиентском `ckclient.json` есть поле `"UDP": true` — включает UDP-режим без флага `-u`.
- Admin-ck-client требует `RemoteHost` (в json или `-s`): без него `fatal: RemoteHost cannot be empty`.
- GET `/admin/users` возвращает только сохранённые поля (7 ключей); счётчиков
  usage (UpUsed/DownUsed) в ответе не наблюдалось — для статистики трафика нужен
  отдельный механизм (не исследовано).

## 6. Лимиты per-user в OpenVPN (фаза 2)

`ccd/<CN>` + `ifconfig-push`, скорость через `tc`. Не реализовано в лабе.

## 7. Клиентский пакет — выполнено

`ivan.ovpn` (инлайн `<ca>/<cert>/<key>/<tls-crypt>`):

```
client
dev tun
proto udp                  # совпадает с ProxyBook
remote 127.0.0.1 1984      # локальный ck-client! НЕ адрес сервера — OpenVPN идёт к Cloak
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
auth SHA256
verb 3
<ca>…</ca>
<cert>…</cert>
<key>…</key>
<tls-crypt>…</tls-crypt>
```

`ckclient-ivan.json` — **самодостаточный** (клиент Cloak можно запустить вообще без флагов;
CLI-флаги `-s/-p/-l` перекрывают json при необходимости):

```json
{
  "Transport": "direct",
  "ProxyMethod": "openvpn",
  "EncryptionMethod": "plain",
  "UID": "<uid юзера>",
  "PublicKey": "<public key сервера>",
  "ServerName": "www.bing.com",
  "NumConn": 16,
  "BrowserSig": "chrome",
  "StreamTimeout": 300,
  "RemoteHost": "<server-ip-or-domain>",
  "RemotePort": "443",
  "LocalHost": "127.0.0.1",
  "LocalPort": "1984"
}
```

- `RemoteHost`/`RemotePort`/`LocalHost`/`LocalPort` — строки (JSON-структура `RawConfig` в `internal/client/state.go`).
- `UID` в json — base64-строка (Go `[]byte` парсится из b64 автоматически).
- `ServerName` ↔ `RedirAddr` — один домен маскировки; подтверждено живым подключением
  (сессия Cloak + TLS handshake).

## 8. End-to-end — выполнено, все пункты проверены

- [x] Подключение DGCloakVPN-клиента `ivan`: `CONNECTED,SUCCESS,10.8.0.2`,
  `ping 10.8.0.1` ок, внешний трафик идёт, сервер: `CLIENT_LIST,ivan`.
- [x] **Отзыв сертификата**: `echo yes | ./easyrsa revoke ivan` + `./easyrsa gen-crl`
  + `install -m644 pki/crl.pem /etc/openvpn/server/crl.pem` — **без рестарта сервиса**.
  Сервер на следующем же handshake: `CRL: loaded 1 CRLs from file crl.pem` →
  `VERIFY ERROR: certificate revoked: CN=ivan` → `TLS handshake failed`. Мгновенно.
- [x] **DELETE UID через admin-API**: мгновенный отказ, без рестарта ck-server
  (см. п.5; симптом на клиенте — бесконечный session attempt → server_poll timeout).
- [x] **Ребут VM**: `openvpn-server@server` и `ck-server` поднялись (enabled),
  порты `127.0.0.1:1194/udp` и `:443/tcp` слушают, `ip_forward=1`,
  правила iptables и MASQUERADE восстановлены netfilter-persistent,
  база юзеров `/var/lib/ck-server/userinfo.db` сохранилась.
- [x] **Новый юзер `boris` после ребута**: Cloak-сессия устанавливается,
  TLS handshake проходит (`Peer Connection Initiated`).

### Важные нюансы, вскрытые тестами

- **Ни отзыв сертификата, ни удаление UID не рвут живую сессию.** CRL проверяется
  только на новом TLS handshake, UID — только при установке Cloak-сессии.
  Чтобы выкинуть подключённого юзера, нужен `kill <CN>` через management-интерфейс
  OpenVPN — стоит добавить `management 127.0.0.1 <port>` в server.conf для админки
  (иначе «отключить сейчас» невозможно без рестарта сервиса).
- Отзыв: `revoke` → `gen-crl` → заменить файл по пути из `crl-verify` — и всё,
  юзер мёртв для новых подключений. Сервис трогать не надо.

## Заметки для cloak_admin.py

- Скрипты на сервер: копировать файлом + `ssh -n`; easyrsa — кормить `echo`/`echo yes`.
- Firewall: iptables-persistent + rules.v4 целиком (без ufw — конфликт пакетов).
- `DatabasePath`/`BackupDirPath` — абсолютные пути (issue #13).
- Юзеры Cloak — через `ck-client -a` локально, без SSH; SSH — установка, PKI/CRL, статус.
- Отзыв сертификата — мгновенный (CRL перечитывается на handshake); удаление UID — тоже.
  Живые сессии при этом не рвутся: для «отключить сейчас» нужен management-интерфейс OpenVPN.
- Admin-API ck-client засыпает по idle — (пере)запускать его на каждую операцию/пачку.

## Прогон на AWS (Lightsail-like, ens5, Ubuntu 24.04, SSH:8081 по ppk)

- **SSH**: `plink -batch -i key.ppk -P 8081 ubuntu@host`. puttygen старый (без `-O`),
  ppk → OpenSSH не конвертировал — plink ест ppk напрямую. Heredoc внутри
  plink-команды виснет → скрипты заливать файлом (pscp) и запускать `sudo bash`.
- **UFW остаётся** (в отличие от VPS без ufw): NAT — отдельной `*nat`-таблицей в конец
  `/etc/ufw/before.rules` (после `COMMIT` фильтра!), форварды через
  `ufw route allow`, `net/ipv4/ip_forward=1` в `/etc/ufw/sysctl.conf`.
- **Грабля**: `ufw reload` при `ENABLED=no` в `ufw.conf` молча пропускает загрузку
  цепочек → `ufw-before-input` пустая, весь outbound мёртв (DNS-таймауты).
  Лечение: `ufw enable` + `ufw reload`. Проверять `iptables -S ufw-before-input`.
- **Наследие Amnezia** (образ ранее был ею настроен): docker.io+containerd,
  `ENABLED=no`, alpine-образ, docker0. Снос: `scripts/purge-amnezia.sh`
  (apt purge + dir wipe + чистка DOCKER-цепочек iptables + docker0).
  Ещё 4 таких сервера в очереди на чистку.
- **Admin-API**: POST `/admin/users/<uid-base64url>` → 201; тело обязано
  содержать `UID`. `ExpiryTime: 0` = просрочен, кредиты 0 = ноль трафика —
  см. п.5.
- На этом образе был также найден duckdns-cron — не трогать.
- **PMTUD blackhole на пути к инстансу (критично!)**: Cloak-сессии устанавливались
  и умирали каждые ~20-30 с (`a connection has dropped unexpectedly`), сайты не
  открывались, при этом SSH:8081 и настоящий TLS через ck-server→RedirAddr жили
  стабильно. tcpdump показал: сервер шлёт сегменты 1460 байт → клиент их не
  получает → ретрансмиты ~20 с → RST. На пути к инстансу реальный MTU ≈1000,
  а ICMP Fragmentation Needed не доходит (AWS Security Group режет ICMP,
  консоли нет — открыть нельзя). MSS 1280 и даже 1100 всё ещё терялись.
  **Лечение: TCPMSS-clamp 800 на ens5** — сессия стабильна, спидтест 18/3.4
  Мбит/с. Правила — `*mangle`-секцией в конец `/etc/ufw/before.rules`
  (после `*nat` `COMMIT`), иначе слетят при ребуте:
  ```
  *mangle
  :PREROUTING ACCEPT [0:0]
  :POSTROUTING ACCEPT [0:0]
  -A PREROUTING -i ens5 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 800
  -A POSTROUTING -o ens5 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 800
  COMMIT
  ```
  Признак проблемы в диагностике: `ss -tin` на :443 → conns в
  `backoff:15 rto:120000` с `lost: N` и неподтверждёнными ретрансмитами.
  Признак проблемы в диагностике: `ss -tin` на :443 → conns в
  `backoff:15 rto:120000` с `lost: N` и неподтверждёнными ретрансмитами.
  **Проверено на втором AWS-инстансе (Frankfurt, eu-central-1): проблема НЕ
  воспроизводится** — сессии стабильны без clamp'а. Т.е. это особенность пути
  к конкретному инстансу/региону, а не всех AWS.
  **Админке: MSS-clamp — опция при деплое + авто-диагностика по симптому**
  (сессии рвутся по ~20-30 с при живом handshake → предложить clamp 800;
  1100 на этом пути было мало). Без симптомов не применять.
- **Crash-настройки юзера Cloak** (воспроизвелись и тут): `UpRate/DownRate: 0`
  → panic `token bucket capacity is not > 0` и restart-loop ck-server
  (на клиенте — RST/EOF сразу после ClientHello); `SessionsCap: 0` →
  `Sessions cap has reached`. См. п.5 — админка обязана ставить дефолты
  UpRate/DownRate=1e9, SessionsCap≥16.
- **DPI на AWS-диапазоны (главный вывод прогона).** На втором инстансе
  (Frankfurt, eu-central-1)
  развёрнуто то же, что и на London. Факты:
  - мобильная сеть → всё работает и на 1460 MSS (18/3.4 Мбит);
  - домашний провайдер → сессии рвутся ~каждые 20-30 с при любом MSS
    (даже 800), теряются и мелкие пакеты — это **не** MTU;
  - контрольный тест: голый TCP-эхо на :443 с того же домашнего пути —
    90 с, 23 МБ, ноль провалов; настоящий TLS через ck-server→RedirAddr —
    тоже живёт. Убивается только **псевдо-TLS трафик Cloak**;
  - реакция зависит от SNI: `www.microsoft.com` режется на ClientHello
    (сервер видит TCP-коннект, но первый пакет не приходит —
    `error reading first packet: i/o timeout`), `www.bing.com` и
    `d1.awsstatic.com` пропускают handshake, но поток всё равно умирает
    через ~20-30 с — т.е. детект идёт **после** handshake по паттерну;
  - исторический факт от пользователя: VLESS+Reality на этих же
    AWS-инстансах внезапно умер ~за 2 мес до прогона — на ВСЕХ инстансах,
    для юзеров из разных регионов РФ.
  - **Контрпример:** Amnezia-Cloak на AWS us-east-1 (Virginia,
    ck-server v2.8.0, OpenVPN-over-TCP, RedirAddr tile.openstreetmap.org)
    работает с того же домашнего провайдера стабильно → детекция
    применяется выборочно, **к конкретным «палёным» IP** (London и
    Frankfurt раньше хостили VLESS Reality и, видимо, попали в kill-лист
    при волне блокировок ~2 мес назад).
  - Проверено и НЕ помогло: ck-server v2.8.0 вместо v2.12.0 на Frankfurt —
    сессии всё равно умирают каждые ~20-40 с (теряются пакеты даже при
    mss:536 → дело точно не в MTU и не в версии).
  - **Фильтрация «палёного» IP динамическая (окнами).** Наблюдено на
    Frankfurt: час сессии умирают каждые ~20-40 с → затем окно открывается
    и тот же профиль даёт 100+ Мбит → затем флаг снова активен.
    **В активном окне режется ВСЁ к этому IP**, включая обычный SSH на
    :8081 (тот же симптом был у пользователя при настройке VLESS —
    приходилось заходить через другой VPN). Практика обхода для
    управления: SSH до палёного хоста гонять через рабочий VPN
    (проверено: plink до Frankfurt через Virginia-туннель — стабильно).
  - **Чистые IP подтверждены:** Stockholm (eu-north-1) и
    Montreal (ca-central-1) — на обоих стоит Amnezia
    openvpn-cloak в docker, оба работают с того же домашнего провайдера
    на 100+ Мбит. Т.е. проблема — не AWS и не регионы сами по себе.
  **Вывод: DPI (вероятно ТСПУ) помечает конкретные IP (видимо, по истории
  запрещённых протоколов на них) и применяет к ним фильтрацию окнами —
  в активном окне умирает любой трафик, включая SSH. Смена SNI/версии
  ck-server/MSS не помогает. Такие IP для юзеров непригодны (работают
  непредсказуемыми окнами), но годятся как тестовый полигон. Спасение —
  смена публичного IP (Elastic IP) или другой хостинг/IP.**
  **Админке:** статус сервера должен уметь показывать «IP под флажком
  DPI» по симптому (Cloak-сессии дохнут ~20-40 с при живом голом TCP и
  периодические разрывы даже SSH) → рекомендация сменить IP.

## Повторный прогон на внешнем VPS (Ubuntu 24.04, ens3)

Схема воспроизведена на голом Ubuntu 24.04.5 с SSH:22 по паролю → ключ → без пароля.
Отличия и грабли прогона:

- **bootstrap по паролю**: на Windows без PTY — через paramiko: установить ключ root'у и
  юзеру `admin` (sudo NOPASSWD), проверить вход по ключу, и только потом
  `PasswordAuthentication no` + `PermitRootLogin prohibit-password`
  (`/etc/ssh/sshd_config.d/99-dgcloak.conf`, `systemctl reload ssh`).
- **`gen-req` падает на CN-промпте при пустом stdin** — обязательно `echo |` перед
  `gen-req` (в 01-openvpn.sh строка была без пайпа — исправить при переносе в админку).
- **Конфиги заливать файлом (scp/SFTP), а не heredoc через `bash -c`** — экранирование
  кавычек через SSH+PowerShell ломает `push "..."` (получилось `Bad backslash`).
- `install` с несколькими источниками требует каталог назначения, не список файлов.
- В этом образе `iptables` уже был → guard `command -v iptables` пропустил установку
  `iptables-persistent` и `rules.v4` некуда писать. Правило: ставить
  `iptables-persistent` явно, не по наличию `iptables`.
- **Маскировка проверена снаружи**: `curl --resolve www.bing.com:443:<IP> https://www.bing.com/`
  → HTTP 200 — ck-server прозрачно проксирует не-Cloak TLS на RedirAddr.
- **ПЕТЛЯ ПОЛНОГО ТУННЕЛЯ (критично!)**: на публичном IP `redirect-gateway def1`
  заворачивает в туннель и сам транспорт Cloak → сессия умирает через ~25 с после
  установки маршрутов, реконнекты не проходят, выглядит как «мертвая сеть».
  В лабе не проявлялось (LAN-подсеть вне туннеля). Лечение: `route <IP сервера>
  255.255.255.255 net_gateway` — поле `bypass_ip` в профиле клиента.
  **Админка при экспорте бандла обязана заполнять bypass_ip автоматически.**
  Ещё лучше — клиенту выводить bypass из RemoteHost ck-конфига без ручного поля.
- Бэкап перед изменениями: `/etc/openvpn` + `/etc/ck-server` + `/var/lib/ck-server`.
- SSH-порт/учётка — настраиваемые (дефолт 22).
- **`BrowserSig: chrome` может не ходить по реальному интернет-пути.** На трассе
  клиент → VPS все ClientHello с chrome-профилем висели до
  timeout (сервер видел TCP-коннект, но первый пакет не приходил — `error reading
  first packet: i/o timeout`), после 4 попыток ck-client откатывался на firefox —
  и сессия вставала мгновенно (~1 с, замерено). Подключение из-за этого занимало
  ~20 с. Лечение на клиенте: `"BrowserSig": "firefox"` — срабатывает без рестарта
  приложения (ck-конфиг читается при каждом подключении).
  **Админке стоит ставить firefox дефолтом** (или делать параметром).
- **`NumConn` критичен для полосы — чем хуже путь, тем больше нужно.**
  Пакеты UDP мультиплексируются в несколько TLS-коннектов и доходят не по
  порядку; OpenVPN дропает их как replay (`AEAD Decrypt error: bad packet ID`).
  Замерено: `NumConn: 1` даёт ~46/17 Мбит против ~67/32 на `NumConn: 4` —
  параллельные коннекты сглаживают head-of-line blocking внешнего TCP.
  На просевшем пути (потери, ~45/8.6 Мбит чистым SSH) `NumConn: 16` поднял
  скорость через VPN с 5.5 до 25.5 Мбит.
  **Дефолт для бандлов: `NumConn: 16` + `mute-replay-warnings` в .ovpn** —
  дропы остаются, но лог чистый, а полоса выше.
