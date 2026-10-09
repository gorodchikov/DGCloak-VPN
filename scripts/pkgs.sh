#!/bin/bash
# Пакеты серверной части.
#   sudo bash pkgs.sh check    — отчёт: что стоит, версии, последний релиз Cloak
#   sudo bash pkgs.sh install  — поставить/обновить недостающее (apt)
set -uo pipefail
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
export DEBIAN_FRONTEND=noninteractive
ACTION=${1:-check}
APT="-o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false"

NEED_PKGS="openvpn easy-rsa nftables curl python3 ca-certificates"

ver() { dpkg-query -W -f='${Version}' "$1" 2>/dev/null || echo "-"; }

if [ "$ACTION" = check ]; then
    echo "===PKGS==="
    for p in $NEED_PKGS; do
        printf "%s=%s\n" "$p" "$(ver "$p")"
    done
    echo "===OPENVPN==="
    openvpn --version 2>/dev/null | head -1 || echo "not installed"
    echo "===EASYRSA==="
    ER=$(command -v easyrsa || ls /usr/share/easy-rsa/easyrsa 2>/dev/null | head -1 || true)
    [ -n "$ER" ] && echo "easyrsa: $ER" || echo "easyrsa: -"
    echo "===CKSERVER==="
    if command -v ck-server >/dev/null 2>&1; then
        ck-server -v 2>&1 | head -1
    else
        echo "not installed"
    fi
    echo "===CK_LATEST==="
    curl -fsSL --max-time 10 \
        "https://api.github.com/repos/cbeuw/Cloak/releases/latest" 2>/dev/null \
        | grep -oP '"tag_name":\s*"\K[^"]+' | head -1 || echo "?"
    echo "===UNIT_OVPN==="; systemctl is-enabled openvpn-server@server 2>/dev/null || echo "none"
    echo "===UNIT_CK===";   systemctl is-enabled ck-server 2>/dev/null || echo "none"
    echo "===DONE==="
elif [ "$ACTION" = install ]; then
    apt-get $APT update -qq \
        || _ "!! apt update: часть репозиториев недоступна — ставим из имеющихся списков" \
             "!! apt update: some repositories unavailable — installing from existing lists"
    apt-get $APT install -y -qq $NEED_PKGS
    echo "=== OK pkgs install ==="
else
    echo "usage: pkgs.sh check|install"; exit 2
fi
