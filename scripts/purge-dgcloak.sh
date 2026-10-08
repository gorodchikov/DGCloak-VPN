#!/bin/bash
# Полный сброс деплоя DGCloak: ck-server, OpenVPN+PKI, наши правила фаервола,
# sysctl, пакеты. SSH-доступ и юзеры НЕ трогаются.
# Использование: sudo bash purge-dgcloak.sh [cloak_port]
set -u
export DEBIAN_FRONTEND=noninteractive
CK_PORT="${1:-443}"
EXT_IF=$(ip route show default | awk '{print $5; exit}')
echo "=== PURGE DGCloak ==="

# --- сервисы ---
systemctl disable --now ck-server 2>/dev/null
systemctl disable --now openvpn-server@server 2>/dev/null
systemctl disable openvpn 2>/dev/null
rm -f /etc/systemd/system/ck-server.service
systemctl daemon-reload
systemctl reset-failed 2>/dev/null

# --- cloak / openvpn файлы ---
rm -rf /etc/ck-server /var/lib/ck-server /etc/dgcloak
id cloak >/dev/null 2>&1 && userdel cloak
rm -f /usr/local/bin/ck-server
rm -rf /etc/openvpn/server /etc/openvpn/easyrsa /etc/openvpn/easy-rsa \
       /etc/openvpn/pki /root/easy-rsa* 2>/dev/null

# --- фаервол: снимаем ТОЛЬКО наши следы, по бэкенду ---
if nft list table inet dgcloak >/dev/null 2>&1; then
    nft delete table inet dgcloak
    echo "nft: таблица inet dgcloak удалена"
fi

if command -v ufw >/dev/null 2>&1 && \
   ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw --force delete allow "$CK_PORT/tcp" >/dev/null 2>&1 || true
    for n in $(ufw status numbered 2>/dev/null | grep tun0 | \
               grep -oE '^\[ *[0-9]+\]' | tr -d '[] ' | sort -rn); do
        ufw --force delete "$n" >/dev/null 2>&1 || true
    done
    B=""
    for f in $(ls -t /etc/ufw/before.rules.bak.* 2>/dev/null); do
        grep -qE 'OPENVPN-NAT|TCPMSS|10\.8\.0' "$f" || { B="$f"; break; }
    done
    if [ -n "$B" ]; then
        cp -a "$B" /etc/ufw/before.rules; echo "before.rules ← $B"
    else
        sed -i '/OPENVPN-NAT/d;/10\.8\.0\.0\/24/d;/TCPMSS/d' \
            /etc/ufw/before.rules 2>/dev/null || true
    fi
    rm -f /etc/ufw/before.rules.bak.*
    sed -i 's|^net/ipv4/ip_forward=.*|#net/ipv4/ip_forward=1|' \
        /etc/ufw/sysctl.conf 2>/dev/null || true
    sed -i 's|^DEFAULT_FORWARD_POLICY=.*|DEFAULT_FORWARD_POLICY="ACCEPT"|' \
        /etc/default/ufw 2>/dev/null || true
    iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o "$EXT_IF" \
        -j MASQUERADE 2>/dev/null || true
    ufw reload >/dev/null 2>&1 || true
    echo "ufw: наши правила сняты"
fi

if command -v firewall-cmd >/dev/null 2>&1 && \
   firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --remove-masquerade >/dev/null 2>&1 || true
    firewall-cmd --permanent "--remove-port=$CK_PORT/tcp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    echo "firewalld: masquerade и $CK_PORT/tcp сняты"
fi

if [ -f /etc/iptables/rules.v4 ]; then
    if grep -qE '10\.8\.0|cloak|dgcloak' /etc/iptables/rules.v4; then
        # rules.v4 наш — весь файербол наш: политики в ACCEPT и пакет снести
        iptables -P INPUT ACCEPT; iptables -P FORWARD ACCEPT
        iptables -P OUTPUT ACCEPT
        iptables -F; iptables -X 2>/dev/null
        iptables -t nat -F; iptables -t nat -X 2>/dev/null
        apt-get -o DPkg::Lock::Timeout=300 purge -y -qq \
            iptables-persistent 2>/dev/null
        rm -rf /etc/iptables
        echo "iptables-persistent: правила и пакет удалены"
    else
        iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o "$EXT_IF" \
            -j MASQUERADE 2>/dev/null || true
        echo "iptables-persistent: чужой — удалён только masquerade"
    fi
fi

# --- sysctl + tmp ---
rm -f /etc/sysctl.d/99-dgcloak-vpn.conf
sysctl -w net.ipv4.ip_forward=0 >/dev/null
rm -f /tmp/dgadm-* /tmp/dgcloak-* /tmp/probe.sh

# --- пакеты ---
apt-get -o DPkg::Lock::Timeout=300 purge -y -qq \
    openvpn easy-rsa 2>/dev/null
apt-get -o DPkg::Lock::Timeout=300 autoremove -y -qq 2>/dev/null

echo "=== VERIFY ==="
systemctl list-units --all | grep -iE "ck-server|openvpn-server@" \
    || echo "units: чисто"
ss -tln 2>/dev/null | grep -E ":$CK_PORT |:1194 " || echo "порты свободны"
dpkg -l openvpn easy-rsa 2>/dev/null | grep "^ii" || echo "пакеты вычищены"
id cloak 2>/dev/null || echo "юзер cloak удалён"
echo "=== DONE purge ==="
