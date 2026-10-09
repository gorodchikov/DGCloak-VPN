#!/bin/bash
# Маршрутизация и NAT для VPN — nftables.
# Если таблица inet dgcloak уже есть (ставили fw-install.sh) — дополняем её.
# Если nftables на хосте чужой/пустой — создаём свою таблицу с NAT и forward.
# Использование: sudo bash nat-enable.sh
set -euo pipefail

EXT_IF=$(ip route show default | awk '{print $5; exit}')
echo "EXT_IF=$EXT_IF"

if ! nft list table inet dgcloak >/dev/null 2>&1; then
    echo "таблицы inet dgcloak нет — nftables чужой/пустой, создаём свою"
    nft add table inet dgcloak
    nft 'add chain inet dgcloak forward { type filter hook forward priority 0; policy accept; }'
    # если чужой ruleset дропает forward — наши accept-цепочки не спасут, предупреждаем
    nft list ruleset 2>/dev/null | grep -E 'hook forward' | grep 'policy drop' >/dev/null \
        && echo "!! внимание: чужие правила с forward policy=drop могут блокировать VPN-трафик" || true
fi

# ip_forward
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-dgcloak-vpn.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# forward-правила (идемпотентно: пропускаем если уже есть)
nft list chain inet dgcloak forward | grep 'iifname "tun0"' >/dev/null || \
    nft add rule inet dgcloak forward iifname "tun0" oifname "$EXT_IF" accept comment \"vpn-wan\"
nft list chain inet dgcloak forward | grep 'oifname "tun0"' >/dev/null || \
    nft add rule inet dgcloak forward iifname "$EXT_IF" oifname "tun0" ct state established,related accept

# postrouting-цепочка создаётся один раз
nft list chain inet dgcloak postrouting >/dev/null 2>&1 || \
    nft 'add chain inet dgcloak postrouting { type nat hook postrouting priority srcnat; }'

nft list chain inet dgcloak postrouting | grep 'masquerade' >/dev/null || \
    nft add rule inet dgcloak postrouting ip saddr 10.8.0.0/24 oifname "$EXT_IF" masquerade comment \"vpn-nat\"

# персист
nft list ruleset > /etc/nftables.conf
systemctl enable nftables >/dev/null 2>&1 || true

echo '--- postrouting ---'; nft list chain inet dgcloak postrouting
echo '--- forward ---'; nft list chain inet dgcloak forward

# Контроль результата: masquerade именно 10.8.0.0/24 + forward tun0
nft list chain inet dgcloak postrouting | grep -q '10\.8\.0\.0/24.*masquerade' \
    || { echo "!! masq для 10.8.0.0/24 не появился"; exit 1; }
nft list chain inet dgcloak forward | grep -q 'iifname "tun0"' \
    || { echo "!! нет forward-правил tun0"; exit 1; }

echo "=== OK nat-enable ==="
