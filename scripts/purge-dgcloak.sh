#!/bin/bash
# Revert: полный сброс деплоя DGCloak + возврат состояния «как до деплоя»:
# ck-server, OpenVPN+PKI, наши правила фаервола, sysctl, пакеты — и обратно:
# прежний владелец порта Cloak (сервис/контейнер), автозапуск docker,
# исходный ip_forward, оригиналы ufw/firewalld, чужой nftables не трогаем,
# наш — сносим с пакетом. SSH-доступ и юзеры НЕ затрагиваются.
# Снимок «до» — /etc/dgcloak/predeploy.env (predeploy-save.sh при деплое +
# админка дописывает, кого загасила на порту Cloak). Снимка нет → старое
# поведение: сносим только своё, ничего не восстанавливаем.
# Использование: sudo bash purge-dgcloak.sh [cloak_port]
set -u
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
export DEBIAN_FRONTEND=noninteractive
CK_PORT="${1:-443}"
EXT_IF=$(ip route show default | awk '{print $5; exit}')
echo "=== PURGE DGCloak ==="

# --- снимок «до деплоя»: копируем до rm -rf /etc/dgcloak ниже ---
PD=/etc/dgcloak/predeploy.env
PDC=/tmp/pd.env.$$
[ -f "$PD" ] && cp "$PD" "$PDC" || \
    _ "снимка predeploy.env нет — возврат минимальный (только наше)" \
      "no predeploy.env snapshot — minimal revert (our stuff only)"
pdget() { grep -m1 "^$1=" "$PDC" 2>/dev/null | cut -d= -f2-; }
pdall() { grep "^$1=" "$PDC" 2>/dev/null | cut -d= -f2-; }

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
    _ "nft: таблица inet dgcloak удалена" "nft: table inet dgcloak removed"
fi
if [ "$(pdget FW)" = "none" ]; then
    # nftables ставили МЫ (фаервола не было): чистим ruleset, гасим сервис,
    # пакет сносим если его тоже поставили мы (NFT_PKG=0 в снимке)
    : > /etc/nftables.conf
    systemctl disable --now nftables >/dev/null 2>&1 || true
    if [ "$(pdget NFT_PKG)" != "1" ]; then
        apt-get -o DPkg::Lock::Timeout=300 purge -y -qq nftables 2>/dev/null
        _ "nftables: пакет и конфиг снесены (ставили мы)" \
          "nftables: package and config removed (installed by us)"
    else
        _ "nftables: ruleset очищен, пакет оставлен (был до нас)" \
          "nftables: ruleset cleared, package kept (pre-existed)"
    fi
elif command -v nft >/dev/null 2>&1 && [ -f /etc/nftables.conf ]; then
    # чужой nftables остаётся: пересохранить ruleset без нашей таблицы —
    # иначе после ребута она воскреснет из /etc/nftables.conf
    nft list ruleset > /etc/nftables.conf 2>/dev/null || true
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
    # исходные строки — из снимка (дефолт: как раньше)
    UIP=$(pdget UFW_IPFWD)
    sed -i "s|^#\?net/ipv4/ip_forward=.*|${UIP:-#net/ipv4/ip_forward=1}|" \
        /etc/ufw/sysctl.conf 2>/dev/null || true
    UFP=$(pdget UFW_FWD_POLICY)
    sed -i "s|^DEFAULT_FORWARD_POLICY=.*|${UFP:-DEFAULT_FORWARD_POLICY=\"ACCEPT\"}|" \
        /etc/default/ufw 2>/dev/null || true
    iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o "$EXT_IF" \
        -j MASQUERADE 2>/dev/null || true
    ufw reload >/dev/null 2>&1 || true
    _ "ufw: наши правила сняты" "ufw: our rules removed"
fi

if command -v firewall-cmd >/dev/null 2>&1 && \
   firewall-cmd --state >/dev/null 2>&1; then
    if [ "$(pdget FIREWALLD_MASQ)" = "yes" ]; then
        _ "firewalld: masquerade был до нас — оставлен" \
          "firewalld: masquerade pre-existed — kept"
    else
        firewall-cmd --permanent --remove-masquerade >/dev/null 2>&1 || true
    fi
    firewall-cmd --permanent "--remove-port=$CK_PORT/tcp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    _ "firewalld: наш порт $CK_PORT/tcp снят" \
      "firewalld: our port $CK_PORT/tcp removed"
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
        _ "iptables-persistent: правила и пакет удалены" "iptables-persistent: rules and package removed"
    else
        iptables -t nat -D POSTROUTING -s 10.8.0.0/24 -o "$EXT_IF" \
            -j MASQUERADE 2>/dev/null || true
        _ "iptables-persistent: чужой — удалён только masquerade" \
          "iptables-persistent: foreign — removed masquerade only"
    fi
fi

# --- sysctl + tmp ---
rm -f /etc/sysctl.d/99-dgcloak-vpn.conf
FWD=$(pdget IP_FORWARD)
sysctl -w "net.ipv4.ip_forward=${FWD:-0}" >/dev/null
rm -f /tmp/dgadm-* /tmp/dgcloak-* /tmp/probe.sh

# --- вернуть то, что гасили при деплое (по снимку) ---
if [ -s "$PDC" ]; then
    pdall STOPPED_SVC | while read -r u; do
        [ -n "$u" ] || continue
        systemctl enable --now "$u" >/dev/null 2>&1 && \
            _ "сервис «$u» возвращён (enable --now)" \
              "service \"$u\" restored (enable --now)" || true
    done
    grep '^DOCKER_POLICY_' "$PDC" 2>/dev/null | while IFS='=' read -r k v; do
        c=${k#DOCKER_POLICY_}
        { [ -n "$c" ] && [ -n "$v" ]; } || continue
        docker update --restart="$v" "$c" >/dev/null 2>&1 && \
            _ "docker: автозапуск «$c» восстановлен ($v)" \
              "docker: \"$c\" autostart restored ($v)" || true
    done
    pdall DOCKER_START | while read -r c; do
        [ -n "$c" ] || continue
        docker start "$c" >/dev/null 2>&1 && \
            _ "контейнер «$c» запущен обратно" \
              "container \"$c\" started back" || true
    done
fi

# --- пакеты: свои сносим, чужие (были до деплоя) оставляем ---
PKGS=""
[ "$(pdget OPENVPN_PKG)" = "1" ] && \
    _ "openvpn стоял до нас — пакет оставлен" \
      "openvpn pre-existed — package kept" || PKGS="$PKGS openvpn"
[ "$(pdget EASYRSA_PKG)" = "1" ] && \
    _ "easy-rsa стоял до нас — пакет оставлен" \
      "easy-rsa pre-existed — package kept" || PKGS="$PKGS easy-rsa"
# shellcheck disable=SC2086
[ -n "${PKGS// /}" ] && apt-get -o DPkg::Lock::Timeout=300 purge -y -qq \
    $PKGS 2>/dev/null
apt-get -o DPkg::Lock::Timeout=300 autoremove -y -qq 2>/dev/null

echo "=== VERIFY ==="
systemctl list-units --all | grep -iE "ck-server|openvpn-server@" \
    || _ "units: чисто" "units: clean"
if ss -tln 2>/dev/null | grep -E ":$CK_PORT |:1194 " >/dev/null; then
    if grep -qE '^(STOPPED_SVC|DOCKER_START)=' "$PDC" 2>/dev/null; then
        _ "порт $CK_PORT снова у прежнего владельца — так и задумано:" \
          "port $CK_PORT is back to its pre-deploy owner — intended:"
        ss -tln | grep -E ":$CK_PORT |:1194 " | head -3
    else
        ss -tln | grep -E ":$CK_PORT |:1194 "
    fi
else
    _ "порты свободны" "ports are free"
fi
rm -f "$PDC"
dpkg -l openvpn easy-rsa 2>/dev/null | grep "^ii" || _ "пакеты вычищены" "packages purged"
id cloak 2>/dev/null || _ "юзер cloak удалён" "cloak user removed"
echo "=== DONE purge ==="
