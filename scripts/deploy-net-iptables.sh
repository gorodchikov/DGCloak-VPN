#!/bin/bash
# Этап 2 (вариант B): чистый хост без ufw — iptables-persistent, ruleset одним файлом.
# ufw и iptables-persistent взаимоисключающие (Conflicts) — ставить ТОЛЬКО когда ufw нет.
# Использование: sudo bash deploy-net-iptables.sh <ssh_port,ssh_port,...>
# Анти-локаут: перед применением сохраняет текущие правила и запускает
# отложенный откат (120с). Админка открывает НОВОЕ ssh-подключение и делает
# canary-файл /tmp/dgcloak-fw-ok — если SSH жив, откат отменяется сам.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

SSH_PORTS=${1:?usage: deploy-net-iptables.sh <ssh_port,...> [cloak_port]}
CK_PORT=${2:-443}
EXT_IF=$(ip route show default | awk '{print $5; exit}')
echo "EXT_IF=$EXT_IF SSH_PORTS=$SSH_PORTS CK_PORT=$CK_PORT"

echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-dgcloak-vpn.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# iptables-persistent ставим явно (он снесёт ufw по Conflicts, если тот вдруг есть).
# DPkg::Lock::Timeout: unattended-upgrades на свежем VPS может держать лок
apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false update -qq \
    || echo "!! apt update: часть репозиториев недоступна — ставим из имеющихся списков"
apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false install -y -qq iptables-persistent

{
cat <<EOF
*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT ACCEPT [0:0]
-A INPUT -i lo -j ACCEPT
-A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A INPUT -p icmp -j ACCEPT
-A INPUT -p udp --dport 68 -j ACCEPT
EOF
# каждый реальный порт sshd + порт подключения (DNAT: внешний порт ≠ listen)
for p in ${SSH_PORTS//,/ }; do
    echo "-A INPUT -p tcp --dport $p -m conntrack --ctstate NEW -j ACCEPT"
done
cat <<EOF
-A INPUT -p tcp --dport $CK_PORT -m conntrack --ctstate NEW -j ACCEPT
-A FORWARD -i tun0 -o $EXT_IF -j ACCEPT
-A FORWARD -i $EXT_IF -o tun0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
COMMIT
*nat
:PREROUTING ACCEPT [0:0]
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
-A POSTROUTING -s 10.8.0.0/24 -o $EXT_IF -j MASQUERADE
COMMIT
EOF
} > /etc/iptables/rules.v4

# --- анти-локаут: сохранить старые правила и запланировать откат ---
iptables-save > /tmp/dgcloak-fw.bak
rm -f /tmp/dgcloak-fw-ok
setsid bash -c 'sleep 120; if [ ! -f /tmp/dgcloak-fw-ok ]; then
    iptables-restore < /tmp/dgcloak-fw.bak
    netfilter-persistent save 2>/dev/null || true
fi; rm -f /tmp/dgcloak-fw.bak /tmp/dgcloak-fw-ok' </dev/null >/dev/null 2>&1 &
echo "rollback-таймер: 120с (ожидаю canary /tmp/dgcloak-fw-ok)"

iptables-restore < /etc/iptables/rules.v4
netfilter-persistent save >/dev/null
systemctl enable netfilter-persistent >/dev/null 2>&1 || true

echo '--- filter INPUT ---'; iptables -S INPUT | head -10
echo '--- nat POSTROUTING ---'; iptables -t nat -S POSTROUTING | tail -3

# Контроль результата: masquerade именно 10.8.0.0/24 + forward tun0
iptables -t nat -S POSTROUTING | grep -q '10\.8\.0\.0/24 .*MASQUERADE' \
    || { echo "!! masq для 10.8.0.0/24 не появился в POSTROUTING"; exit 1; }
iptables -S FORWARD | grep -q tun0 \
    || { echo "!! нет forward-правил для tun0"; exit 1; }

echo "=== OK deploy-net-iptables ==="
