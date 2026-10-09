#!/bin/bash
# MSS-clamp: лечение PMTUD blackhole (путь ~MTU 1000, ICMP режется).
# Применять ТОЛЬКО по симптому: Cloak-сессии рвутся ~каждые 20-30с при живом
# handshake. На части хостеров не нужен.
# Использование: sudo bash mss-clamp.sh [mss]   (дефолт 800)
# Требует ufw (пишет в before.rules). Без ufw — пишет в iptables rules.v4 mangle.
set -euo pipefail
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }

EXT_IF=$(ip route show default | awk '{print $5; exit}')
MSS=${1:-800}

if [ -f /etc/ufw/before.rules ] && command -v ufw >/dev/null; then
    F=/etc/ufw/before.rules
    cp -a "$F" "$F.bak.$(date +%s)"
    if ! grep -q 'TCPMSS' "$F"; then
        cat >> "$F" <<EOF

*mangle
:PREROUTING ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
# PMTUD blackhole - clamp MSS
-A PREROUTING -i $EXT_IF -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS
-A POSTROUTING -o $EXT_IF -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS
COMMIT
EOF
    fi
    iptables-restore --test < "$F" || { echo "SYNTAX ERROR"; exit 1; }
    ufw reload
else
    F=/etc/iptables/rules.v4
    [ -f "$F" ] || { _ "!! ни ufw, ни rules.v4 — некуда писать" \
                       "!! neither ufw nor rules.v4 — nowhere to write"; exit 1; }
    cp -a "$F" "$F.bak.$(date +%s)"
    if ! grep -q 'TCPMSS' "$F"; then
        cat >> "$F" <<EOF
*mangle
:PREROUTING ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
-A PREROUTING -i $EXT_IF -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS
-A POSTROUTING -o $EXT_IF -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $MSS
COMMIT
EOF
    fi
    iptables-restore < "$F"
fi

iptables -t mangle -L PREROUTING -n -v | grep TCPMSS \
    || _ "!! PREROUTING TCPMSS не найден" "!! PREROUTING TCPMSS not found"
echo "MSS clamp $MSS applied and persisted on $EXT_IF"
echo "=== OK mss-clamp ==="
