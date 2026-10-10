# Backlog — отложенные задачи

## 1. ~~Firewalld-бэкенд: полноценная верификация NAT/forward~~

**Статус:** сделано. Реализация по схеме «детект → своя ветка»:
firewalld-пробы выполняются только когда `fw_backend`/`FW` = firewalld,
для остальных бэкендов логика прежняя.

- `_step_nat`: к masquerade добавлен `--add-forward --zone=public`
  (на firewalld ≥ 0.9 masq сам по себе forward не открывает; на старее
  опции нет — ошибка глушится, там masq открывал forward сам).
- Пост-проверка для firewalld: `--query-masquerade` + `--query-forward`
  (ответ `yes/no`; опция недоступна → `na`, пропускаем).
- `probe.sh`: firewalld-ветка аудита NAT — те же два query, выводится
  `nat_ok`/`nat_no_masq`/`nat_no_fwd`/`no_forward`.
- Снимок/revert: `predeploy-save.sh` пишет `FIREWALLD_FWD=yes|no|na`;
  `purge-dgcloak.sh` снимает `--remove-forward`, только если до нас было
  `no`.
- admintest: U9.21–U9.25.

## 2. ~~Предупреждение: сервер управляется через свой же VPN-туннель~~

**Статус:** сделано (`600b19a`). `vpn_active_host()` читает
`%APPDATA%\DGCloak\VPN` (pids.json — жив ли клиент; data.json/`RemoteHost`
профиля — куда подключён); `_warn_own_tunnel()` предупреждает перед
Deploy all, опасными шагами (fw/sysupd/pkgs/ovpn/nat/cloak), purge и reboot.

Ограничение: детектит только наш клиент (DGCloakVPN); сторонние VPN
(Amnezia и т.п.) не видны.

## 2b. Матрица поддержки: RHEL-семейство (dnf)

**Статус:** план. Лабы поднимаются: Rocky 9, Alma 9, Fedora 41/42.

Что нужно в коде/скриптах:

- `pkgs.sh`, `sysupdate.sh`, `deploy-openvpn.sh`, `deploy-net-iptables.sh`,
  `purge-amnezia.sh`, `purge-dgcloak.sh`, `probe.sh` — ветки dnf вместо apt
  (openvpn/easy-rsa на RHEL-клонах — через `epel-release`; на Fedora — из
  базовых реп). Путь easy-rsa на RHEL — `/usr/share/easy-rsa/3`, не
  `/usr/share/easy-rsa`.
- **SELinux** (enforcing на всём семействе): OpenVPN на tcp/443 не
  забиндится без `semanage port -m -t openvpn_port_t -p tcp 443` (+udp при
  UDP-режиме). Пакет `policycoreutils-python-utils`. Откат — снять метку.
- firewalld — уже поддержан (fw-detect/fw-manage), проверить на живом
  firewalld-хосте.
- `iptables-persistent` (deploy-net-iptables.sh) — debian-only пакет; для
  RHEL-семейства ветка через nftables (`/etc/sysconfig/nftables.conf`) или
  чистый firewalld-путь.
- `_detect_check`/матрица в `cloak_admin.py`: `rocky`/`alma`/`fedora` +
  версии (9, 41/42).
- detect.sh: убедиться, что PKG=dnf корректно ловится (уже печатает).

Amazon Linux 2023 — осознанно пропускаем: курируемые репы без EPEL
(easy-rsa под вопросом), образ только cloud-init, аудитория = EC2, где он
и так нативный. Вернёмся, если появятся юзеры на AWS.

## 3. ~~Добивать дочерние plink/pscp/ssh при выходе админки~~

**Статус:** сделано (`600b19a`). Реестр `Admin\pids.json` (PID → имя exe):
`track_proc`/`untrack_proc` на всех Popen (`_run_proc`, `run_stream`,
`CloakAPI`); `atexit` → `kill_child_procs()` (taskkill /F /T, проверка
имени exe); `cleanup_stale_procs()` при старте добивает сирот от аварийного
завершения.
