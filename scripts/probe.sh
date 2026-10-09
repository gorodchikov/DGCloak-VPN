#!/bin/bash
# Read-only аудит статуса шагов деплоя. Формат вывода:
#   ===STEP_<key>===
#   <ok|warn|fail>|<токен[:параметры] — локализуется клиентом>
# Запуск: sudo bash probe.sh
set -u
emit() { echo "===STEP_$1==="; echo "$2|$3"; }

# --- ssh ---
AUTH_USER="${SUDO_USER:-$(id -un)}"
emit ssh "ok" "U=$AUTH_USER H=$(hostname)"

# --- key: есть ли authorized_keys у SSH-юзера (под sudo $HOME=root!) ---
AHOME="$(getent passwd "$AUTH_USER" | cut -d: -f6)"
AK="$AHOME/.ssh/authorized_keys"
if [ -s "$AK" ]; then
    emit key "ok" "ak_count:$(grep -c . "$AK")"
else
    emit key "fail" "ak_missing"
fi

# --- audit ---
. /etc/os-release 2>/dev/null || true
emit audit "ok" "${ID:-?} ${VERSION_ID:-?} / $(uname -m)"

# --- fw: бэкенд + открытые порты ---
CKPORT=""
[ -f /etc/ck-server/ckserver.json ] && \
    CKPORT=$(grep -A1 '"BindAddr"' /etc/ck-server/ckserver.json | grep -oE ':[0-9]+' | tr -d : | head -1)
[ -z "$CKPORT" ] && CKPORT=443
SSHP=$(ss -tln 2>/dev/null | awk '{print $4}' | grep -oE '[0-9]+$' | sort -un | head -3 | tr '\n' ',' | sed 's/,$//')
FW="none"; PORTS=""
if nft list table inet dgcloak >/dev/null 2>&1; then
    FW="nftables-dg"
    PORTS=$(nft list chain inet dgcloak input 2>/dev/null | \
        grep -oE 'dport [0-9]+' | awk '{print $2}' | sort -un | tr '\n' ',' | sed 's/,$//')
elif [ -s /etc/iptables/rules.v4 ]; then
    FW="iptables-persistent"
    PORTS=$(grep -oE '\-\-dport [0-9]+' /etc/iptables/rules.v4 | \
        awk '{print $2}' | sort -un | tr '\n' ',' | sed 's/,$//')
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    FW="ufw"
    PORTS=$(ufw status | awk '/ALLOW/{print $1}' | cut -d/ -f1 | sort -un | tr '\n' ',' | sed 's/,$//')
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    FW="firewalld"
    PORTS=$(firewall-cmd --list-ports 2>/dev/null | tr ' ' '\n' | cut -d/ -f1 | sort -un | tr '\n' ',' | sed 's/,$//')
fi
if [ "$FW" = "none" ]; then
    emit fw "fail" "fw_none"
else
    emit fw "ok" "fw_ok:$FW:${PORTS:-?}"
fi

# --- sysupd ---
# «ok» только если реально нечего ставить: на свежем снапшоте флага
# reboot-required нет просто потому, что апгрейд ещё не делали
if [ -f /var/run/reboot-required ]; then
    emit sysupd "warn" "reboot_required"
elif ! ls /var/lib/apt/lists/*Packages >/dev/null 2>&1; then
    emit sysupd "warn" "apt_stale"
else
    PEND=$(apt-get -s dist-upgrade 2>/dev/null | grep -c '^Inst ' || true)
    if [ "${PEND:-0}" -gt 0 ]; then
        emit sysupd "warn" "pending:$PEND"
    else
        emit sysupd "ok" "clean"
    fi
fi

# --- pkgs ---
MISS=""
for p in openvpn easy-rsa nftables curl python3 ca-certificates; do
    dpkg -s "$p" >/dev/null 2>&1 || MISS="$MISS $p"
done
if [ -n "$MISS" ]; then
    emit pkgs "warn" "pkgs_missing:$MISS"
else
    emit pkgs "ok" "pkgs_ok"
fi

# --- ovpn ---
if systemctl is-active --quiet openvpn-server@server 2>/dev/null; then
    PROTO=$(awk '/^proto /{print $2}' /etc/openvpn/server/server.conf 2>/dev/null)
    MGMT=$(awk '/^management /{print $3}' /etc/openvpn/server/server.conf 2>/dev/null)
    emit ovpn "ok" "ovpn_ok:${PROTO:-?}:${MGMT:-?}"
else
    emit ovpn "fail" "ovpn_down"
fi

# --- nat ---
FWD=$(sysctl -n net.ipv4.ip_forward 2>/dev/null)
NATOK=""
nft list chain inet dgcloak postrouting 2>/dev/null | grep -q masquerade && NATOK=1
iptables -t nat -S POSTROUTING 2>/dev/null | grep -q MASQUERADE && NATOK=1
if [ "$FWD" = "1" ] && [ -n "$NATOK" ]; then
    emit nat "ok" "nat_ok"
elif [ "$FWD" = "1" ]; then
    emit nat "warn" "nat_no_masq"
else
    emit nat "fail" "no_forward"
fi

# --- cloak ---
if systemctl is-active --quiet ck-server 2>/dev/null; then
    LIS=$(ss -tln 2>/dev/null | grep -c ":$CKPORT ")
    if [ -f /etc/ck-server/ckserver.json ] && [ "$LIS" -gt 0 ]; then
        MASK=$(grep -o '"RedirAddr": *"[^"]*"' /etc/ck-server/ckserver.json | cut -d'"' -f4)
        emit cloak "ok" "cloak_ok:$CKPORT:${MASK:-?}"
    else
        emit cloak "warn" "cloak_no_listen:$CKPORT"
    fi
else
    # ck-server не активен — кто тогда держит его порт, если держит?
    L=$(ss -tlnpH "sport = :$CKPORT" 2>/dev/null | head -1)
    if [ -z "$L" ]; then
        emit cloak "fail" "cloak_down"
    elif echo "$L" | grep -q docker-proxy; then
        NM=$(docker ps --filter "publish=$CKPORT" \
             --format '{{.Names}}' 2>/dev/null | head -1)
        emit cloak "fail" "cloak_busy:$CKPORT:docker:${NM:-container}"
    else
        PN=$(echo "$L" | grep -oE '"[^"]+"' | tr -d '"' | head -1)
        emit cloak "fail" "cloak_busy:$CKPORT:proc:${PN:-?}"
    fi
fi
echo "=== DONE ==="
