#!/bin/bash
# Снимок «что было до нас» — для кнопки «Вернуть сервер» (revert).
# Пишет /etc/dgcloak/predeploy.env; НЕ перезаписывает существующий файл
# (baseline = состояние перед ПЕРВЫМ деплоем; файл уходит вместе с purge).
# Динамика (кого загасили на порту Cloak) дописывает админка при деплое:
#   STOPPED_SVC=<unit>          — сервис, выключенный disable --now
#   DOCKER_POLICY_<name>=<pol>  — исходный restart-policy контейнера
#   DOCKER_START=<name>         — контейнер, который был запущен (start обратно)
# Использование: sudo bash predeploy-save.sh
set -u
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }

mkdir -p /etc/dgcloak
F=/etc/dgcloak/predeploy.env
if [ -f "$F" ]; then
    _ "снимок уже есть — baseline оставлен" "snapshot exists — baseline kept"
    cat "$F"
    echo "=== OK predeploy-save (kept) ==="
    exit 0
fi

{
    echo "IP_FORWARD=$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo 0)"
    dpkg -l openvpn  2>/dev/null | grep -q '^ii' \
        && echo "OPENVPN_PKG=1" || echo "OPENVPN_PKG=0"
    dpkg -l easy-rsa 2>/dev/null | grep -q '^ii' \
        && echo "EASYRSA_PKG=1" || echo "EASYRSA_PKG=0"
    dpkg -l nftables 2>/dev/null | grep -q '^ii' \
        && echo "NFT_PKG=1" || echo "NFT_PKG=0"
    if command -v firewall-cmd >/dev/null 2>&1 && \
       firewall-cmd --state >/dev/null 2>&1; then
        firewall-cmd --query-masquerade >/dev/null 2>&1 \
            && echo "FIREWALLD_MASQ=yes" || echo "FIREWALLD_MASQ=no"
        # --query-forward есть с firewalld 0.9; на старее опции нет —
        # там forward держал сам masq, снимать нечего → пишем na
        O=$(firewall-cmd --query-forward --zone=public 2>/dev/null)
        [ "$O" = yes ] && echo "FIREWALLD_FWD=yes"
        [ "$O" = no ] && echo "FIREWALLD_FWD=no"
        [ -z "$O" ] && echo "FIREWALLD_FWD=na"
    fi
    # ufw-оригиналы — до правок deploy-net-ufw.sh
    [ -f /etc/default/ufw ] && \
        grep -m1 '^DEFAULT_FORWARD_POLICY=' /etc/default/ufw | \
        sed 's/^DEFAULT_FORWARD_POLICY=/UFW_FWD_POLICY=/'
    [ -f /etc/ufw/sysctl.conf ] && \
        grep -m1 '^#\?net/ipv4/ip_forward=' /etc/ufw/sysctl.conf | \
        sed 's/^/UFW_IPFWD=/'
} > "$F"
_ "снимок состояния до деплоя:" "pre-deploy state snapshot:"
cat "$F"
echo "=== OK predeploy-save ==="
