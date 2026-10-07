#!/bin/bash
# Аудит фаервола: бэкенд, политики, открытые входящие порты.
# Read-only, ничего не меняет. Под sudo — для полного вывода правил.
set -uo pipefail

echo "===FW_BACKEND==="
FW=none
# NB: не grep -q в пайпе — ранний выход grep'а даёт SIGPIPE производителю
# и pipefail возвращает 141 вместо 0 (флаки на старых nft/iptables).
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep 'Status: active' >/dev/null; then
    FW=ufw
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active firewalld 2>/dev/null | grep '^active' >/dev/null; then
    FW=firewalld
elif [ -f /etc/iptables/rules.v4 ] || command -v netfilter-persistent >/dev/null 2>&1; then
    # ВАЖНО: раньше проверки 'hook input' — iptables-persistent на новых дистрах
    # (iptables-nft) хранит правила в nftables и виден в nft list ruleset
    FW=iptables-persistent
elif nft list ruleset 2>/dev/null | grep 'hook input' >/dev/null; then
    # nftables считаем фаерволом только если есть реальная input-цепочка;
    # enabled-сервис с пустым ruleset (дефолт Ubuntu) — это «фаервола нет»
    FW=nftables
elif iptables -S INPUT 2>/dev/null | grep -vE '^-(P|A INPUT -i lo|-A INPUT -m conntrack --ctstate ESTABLISHED)' | grep . >/dev/null; then
    FW=iptables-custom
fi
echo "FW=$FW"

echo "===FW_POLICY==="
iptables -S INPUT 2>/dev/null | head -1 || echo "?"
nft -s list ruleset 2>/dev/null | grep -A3 'chain input' | grep -oP 'policy \w+' | head -1 || true

echo "===FW_RULES==="
case "$FW" in
    ufw)
        ufw status verbose 2>/dev/null | sed -n '/--/,$p' ;;
    firewalld)
        firewall-cmd --list-all 2>/dev/null ;;
    nftables)
        nft -s list ruleset 2>/dev/null | grep -E 'dport|jump|accept' | head -25 ;;
    iptables-persistent|iptables-custom)
        iptables -S INPUT 2>/dev/null ;;
    *)
        echo "нет правил (INPUT ACCEPT или пусто)" ;;
esac

echo "===LISTEN_TCP==="
# все слушающие TCP-порты снаружи (не 127.0.0.1)
ss -tlnp 2>/dev/null | awk 'NR>1{print $4, $6}' \
    | grep -vE '127\.0\.0\.|::1|\[::ffff:127' | sed 's/users:.*"//' | tr -d ')'

echo "===LISTEN_UDP==="
ss -ulnp 2>/dev/null | awk 'NR>1{print $4, $6}' \
    | grep -vE '127\.0\.0\.|::1|\[::ffff:127' | sed 's/users:.*"//' | tr -d ')'

echo "===DONE==="
