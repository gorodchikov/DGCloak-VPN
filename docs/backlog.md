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

## 3. ~~Добивать дочерние plink/pscp/ssh при выходе админки~~

**Статус:** сделано (`600b19a`). Реестр `Admin\pids.json` (PID → имя exe):
`track_proc`/`untrack_proc` на всех Popen (`_run_proc`, `run_stream`,
`CloakAPI`); `atexit` → `kill_child_procs()` (taskkill /F /T, проверка
имени exe); `cleanup_stale_procs()` при старте добивает сирот от аварийного
завершения.
