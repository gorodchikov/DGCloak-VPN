# Backlog — отложенные задачи

## 1. Firewalld-бэкенд: полноценная верификация NAT/forward

**Статус:** отложено. **Приоритет:** низкий (на поддерживаемых дистрах
Ubuntu 20–26 / Debian 11–13 firewalld нет из коробки).

Текущее состояние: `_step_nat` для `fw == "firewalld"` делает только
`firewall-cmd --permanent --add-masquerade --zone=public`, а пост-проверка
(`masq 10.8.0.0/24` + forward-правила `tun0`) для firewalld сознательно
пропущена — её проверки на iptables/nft на этом бэкенде давали бы ложный
`warn`.

Что нужно при реализации:
- проверять `firewall-cmd --query-masquerade --zone=public`;
- проверять форвардинг между зонами: на firewalld ≥ 1.0 masquerade
  не открывает forward — нужен `--add-forward` на интерфейсах или
  policy-объект (`--new-policy`, `--add-ingress-zone`, `--add-egress-zone`);
- в `probe.sh` добавить firewalld-ветку аудита (`--query-masquerade` +
  наличие forward-policy для tun0), иначе аудит будет врать в обе стороны.

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
