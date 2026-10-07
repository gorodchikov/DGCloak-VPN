#!/bin/bash
# Единое управление фаерволом поверх любого бэкенда.
# Использование:
#   sudo bash fw-manage.sh list        # сырые правила (отладка)
#   sudo bash fw-manage.sh ports       # простой список открытых портов
#   sudo bash fw-manage.sh allow tcp 8080
#   sudo bash fw-manage.sh deny  tcp 8080
set -euo pipefail

ACTION=${1:?usage: fw-manage.sh list|allow|deny [proto port]}
PROTO=${2:-tcp}
PORT=${3:-}

detect_fw() {
    # NB: не использовать grep -q в пайпе — ранний выход даёт SIGPIPE
    # производителю и pipefail возвращает 141 вместо 0.
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep 'Status: active' >/dev/null; then
        echo ufw
    elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active firewalld 2>/dev/null | grep '^active' >/dev/null; then
        echo firewalld
    elif nft list ruleset 2>/dev/null | grep 'table inet dgcloak' >/dev/null; then
        echo nftables-dg
    elif [ -f /etc/iptables/rules.v4 ] || command -v netfilter-persistent >/dev/null 2>&1; then
        # ВАЖНО: до проверки 'hook input' — iptables-persistent на новых дистрах
        # хранит правила в nftables (iptables-nft) и отображается в nft list ruleset
        echo iptables-persistent
    elif nft list ruleset 2>/dev/null | grep 'hook input' >/dev/null; then
        echo nftables
    elif iptables -S INPUT 2>/dev/null | grep 'dport' >/dev/null; then
        echo iptables-custom
    else
        echo none
    fi
}

FW=$(detect_fw)
echo "FW=$FW"

case "$ACTION" in
list)
    case "$FW" in
        ufw) ufw status numbered ;;
        firewalld) firewall-cmd --list-all ;;
        nftables-dg) nft -a list table inet dgcloak ;;
        nftables) nft list ruleset ;;
        iptables-persistent) iptables -S INPUT ;;
        iptables-custom) iptables -S INPUT ;;
        none) echo "фаервола нет — все входящие открыты" ;;
    esac
    ;;
ports)
    # Открытые снаружи порты, простой формат: OPEN <proto> <port> <comment>
    case "$FW" in
        ufw)
            { ufw status 2>/dev/null || true; } | { grep -w ALLOW || true; } \
              | awk '{print $1}' | grep -E '^[0-9]+/(tcp|udp)$' \
              | while IFS=/ read -r p pr; do
                echo "OPEN $pr $p -"; done || true ;;
        firewalld)
            # порты + сервисы (ssh → 22/tcp, dhcpv6-client → 546/udp и т.п.)
            for pp in $(firewall-cmd --list-ports 2>/dev/null); do
                echo "OPEN ${pp##*/} ${pp%%/*} -"; done
            for sv in $(firewall-cmd --list-services 2>/dev/null); do
                firewall-cmd --info-service="$sv" 2>/dev/null \
                  | awk '/^ *ports:/{sub(/^ *ports: */,""); print}' \
                  | tr ' ' '\n' | grep -E '^[0-9]+/(tcp|udp)$' \
                  | while IFS=/ read -r p pr; do
                      echo "OPEN $pr $p $sv"; done
            done ;;
        nftables-dg|nftables)
            # сканируем весь ruleset — input-цепочки может и не быть
            { nft list ruleset 2>/dev/null || true; } | { grep 'dport' || true; } \
              | while read -r l; do
                pr=$(echo "$l" | grep -oE '(tcp|udp) dport' | head -1 | awk '{print $1}') || true
                p=$(echo "$l" | grep -oE 'dport [0-9]+' | head -1 | awk '{print $2}') || true
                cm=$(echo "$l" | sed -n 's/.*comment "\([^"]*\)".*/\1/p') || true
                [ -n "$pr" ] && [ -n "$p" ] && echo "OPEN $pr $p ${cm:--}" || true
            done ;;
        iptables-persistent|iptables-custom)
            { iptables -S INPUT 2>/dev/null || true; } | { grep ' ACCEPT' || true; } \
              | while read -r l; do
                case "$l" in
                    *"--dport "*)
                        pr=$(echo "$l" | grep -oE -- '-p (tcp|udp)' | awk '{print $2}') || true
                        p=$(echo "$l" | grep -oE -- '--dport [0-9]+' | awk '{print $2}') || true
                        cm=$(echo "$l" | sed -n 's/.*--comment "\([^"]*\)".*/\1/p') || true
                        [ -n "$pr" ] && [ -n "$p" ] && echo "OPEN $pr $p ${cm:--}" || true
                        ;;
                esac
            done ;;
        none) echo "фаервола нет — снаружи открыто всё" ;;
    esac
    ;;
allow|deny)
    [ -n "$PORT" ] || { echo "!! нужен порт"; exit 2; }
    case "$FW" in
        ufw)
            [ "$ACTION" = allow ] && ufw allow "$PORT/$PROTO" || ufw delete allow "$PORT/$PROTO" ;;
        firewalld)
            if [ "$ACTION" = allow ]; then
                firewall-cmd --permanent --add-port="$PORT/$PROTO"
            else
                firewall-cmd --permanent --remove-port="$PORT/$PROTO"
            fi
            firewall-cmd --reload ;;
        nftables-dg)
            if [ "$ACTION" = allow ]; then
                nft add rule inet dgcloak input "$PROTO" dport "$PORT" ct state new accept comment \"manual\"
            else
                H=$(nft -a list table inet dgcloak | grep "$PROTO dport $PORT " | grep -oP 'handle \K\d+' | head -1)
                [ -n "$H" ] && nft delete rule inet dgcloak input handle "$H" || echo "правило не найдено"
            fi
            nft list ruleset > /etc/nftables.conf ;;
        nftables)
            echo "!! чужой ruleset nftables — правьте вручную на сервере"; exit 1 ;;
        iptables-persistent|iptables-custom)
            if [ "$ACTION" = allow ]; then
                iptables -A INPUT -p "$PROTO" --dport "$PORT" -m conntrack --ctstate NEW -j ACCEPT
            else
                iptables -D INPUT -p "$PROTO" --dport "$PORT" -j ACCEPT 2>/dev/null \
                 || iptables -D INPUT -p "$PROTO" --dport "$PORT" -m conntrack --ctstate NEW -j ACCEPT
            fi
            netfilter-persistent save >/dev/null 2>&1 \
              || iptables-save > /etc/iptables/rules.v4 2>/dev/null \
              || echo "!! правило живёт до перезагрузки (на диск не сохранено)" ;;
        none)
            echo "!! фаервол не установлен — сначала шаг «Фаервол»"; exit 1 ;;
    esac
    echo "ok: $ACTION $PROTO/$PORT"
    ;;
*)
    echo "!! unknown action $ACTION"; exit 2 ;;
esac
echo "=== OK fw-manage ==="
