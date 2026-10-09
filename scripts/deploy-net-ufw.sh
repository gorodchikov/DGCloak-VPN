#!/bin/bash
# Этап 2 (вариант A): хост уже под ufw — NAT и форвардинг добавляем в его правила.
# Выбирается когда `ufw status` = active (AWS-образы пользователя).
# NAT — отдельной *nat-таблицей В КОНЦЕ /etc/ufw/before.rules (после COMMIT фильтра!).
# Использование: sudo bash deploy-net-ufw.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

EXT_IF=$(ip route show default | awk '{print $5; exit}')
F=/etc/ufw/before.rules
echo "EXT_IF=$EXT_IF"

[ -f "$F" ] || { echo "!! $F отсутствует — ufw не установлен?"; exit 1; }

# ip_forward — в sysctl ufw (он перезаписывает /etc/sysctl.conf при reload)
grep -q '^net/ipv4/ip_forward=1' /etc/ufw/sysctl.conf || \
    sed -i 's|^#\?net/ipv4/ip_forward=.*|net/ipv4/ip_forward=1|' /etc/ufw/sysctl.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# FORWARD policy + разрешение форвардинга туннеля
sed -i 's|^DEFAULT_FORWARD_POLICY=.*|DEFAULT_FORWARD_POLICY="DROP"|' /etc/default/ufw
ufw route allow in on tun0 out on "$EXT_IF" comment 'vpn->wan' >/dev/null 2>&1 || true
ufw route allow in on "$EXT_IF" out on tun0 comment 'wan->vpn established' >/dev/null 2>&1 || true

# NAT: *nat-блок в конец before.rules (идемпотентно по маркеру)
if ! grep -q 'OPENVPN-NAT' "$F"; then
    cp -a "$F" "$F.bak.$(date +%s)"
    cat >> "$F" <<EOF

*nat
:POSTROUTING ACCEPT [0:0]
# OPENVPN-NAT: маскируем VPN-подсеть за внешний интерфейс
-A POSTROUTING -s 10.8.0.0/24 -o $EXT_IF -j MASQUERADE
COMMIT
EOF
fi

# Валидация файла БЕЗ применения — только потом reload
iptables-restore --test < "$F" || { echo "!! before.rules syntax ERROR — не применяю"; exit 1; }

# Live-правило (идемпотентно, чтобы не ждать reload)
iptables -t nat -C POSTROUTING -s 10.8.0.0/24 -o "$EXT_IF" -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o "$EXT_IF" -j MASQUERADE

# Грабля: ufw reload при ENABLED=no молча не грузит цепочки
grep -q '^ENABLED=yes' /etc/ufw/ufw.conf || sed -i 's/^ENABLED=.*/ENABLED=yes/' /etc/ufw/ufw.conf
ufw enable <<<"y" >/dev/null 2>&1 || ufw enable >/dev/null
ufw reload

# Контроль: цепочки реально загружены (иначе outbound мёртв)
iptables -S ufw-before-input >/dev/null 2>&1 || { echo "!! ufw цепочки не загружены"; exit 1; }

# Контроль результата: masquerade НАШЕЙ подсети + forward-правила tun0.
# Без них VPN подключается, но интернета нет (живой кейс: чужие
# docker/amnezia masq-правила давали ложное «всё на месте»).
iptables -t nat -S POSTROUTING | grep -q '10\.8\.0\.0/24 .*MASQUERADE' \
    || { echo "!! masq для 10.8.0.0/24 не появился в POSTROUTING"; exit 1; }
iptables -S | grep -q tun0 \
    || { echo "!! нет forward-правил для tun0"; exit 1; }
grep -q 'OPENVPN-NAT' "$F" \
    || { echo "!! нет персиста masq в $F — после ребута сломается"; exit 1; }

echo '--- nat POSTROUTING ---'; iptables -t nat -S POSTROUTING | tail -3
echo '--- ufw status ---'; ufw status | head -8
echo '--- forward ---'; sysctl -n net.ipv4.ip_forward
echo "=== OK deploy-net-ufw ==="
