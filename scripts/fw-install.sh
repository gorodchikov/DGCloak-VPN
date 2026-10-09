#!/bin/bash
# Установка фаервола на голый хост: nftables (нативно для Debian/Ubuntu).
# Правила: INPUT DROP + lo + established + icmp + ssh-порты + cloak tcp/443(или другой).
# Анти-локаут: перед применением — бэкап + отложенный откат 120с,
# отменяется canary-файлом /tmp/dgcloak-fw-ok (трогает админка новым ssh-коннектом).
# Использование: sudo bash fw-install.sh <ssh_ports_csv> <cloak_port>
set -euo pipefail
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
export DEBIAN_FRONTEND=noninteractive

SSH_PORTS=${1:?usage: fw-install.sh <ssh_port,...> <cloak_port>}
CLOAK_PORT=${2:-443}

apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false update -qq \
    || _ "!! apt update: часть репозиториев недоступна — ставим из имеющихся списков" \
         "!! apt update: some repositories unavailable — installing from existing lists"
apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false install -y -qq nftables

# --- новый ruleset ---
{
cat <<'HDR'
#!/usr/sbin/nft -f
flush ruleset

table inet dgcloak {
    chain input {
        type filter hook input priority filter; policy drop;
        iifname "lo" accept
        ct state established,related accept
        ip protocol icmp accept
HDR
for p in ${SSH_PORTS//,/ }; do
    echo "        tcp dport $p ct state new accept comment \"ssh\""
done
cat <<EOF
        tcp dport $CLOAK_PORT ct state new accept comment "cloak"
    }
    chain forward {
        type filter hook forward priority filter; policy drop;
    }
    chain output {
        type filter hook output priority filter; policy accept;
    }
}
EOF
} > /etc/nftables.conf

# --- бэкап + отложенный откат ---
nft list ruleset > /tmp/dgcloak-fw.bak 2>/dev/null || : > /tmp/dgcloak-fw.bak
rm -f /tmp/dgcloak-fw-ok
setsid bash -c 'sleep 120; if [ ! -f /tmp/dgcloak-fw-ok ]; then
    nft -f /tmp/dgcloak-fw.bak 2>/dev/null || true
    : > /etc/nftables.conf
fi; rm -f /tmp/dgcloak-fw.bak /tmp/dgcloak-fw-ok' </dev/null >/dev/null 2>&1 &
_ "rollback-таймер: 120с (ожидаю canary)" "rollback timer: 120s (waiting for canary)"

systemctl enable --now nftables >/dev/null 2>&1 || true
nft -f /etc/nftables.conf

echo '--- ruleset ---'
nft -s list ruleset | head -20
echo "=== OK fw-install ==="
