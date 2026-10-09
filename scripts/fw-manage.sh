#!/bin/bash
# Единое управление фаерволом поверх любого бэкенда.
# Использование:
#   sudo bash fw-manage.sh list        # сырые правила (отладка)
#   sudo bash fw-manage.sh ports       # простой список открытых портов
#   sudo bash fw-manage.sh allow tcp 8080
#   sudo bash fw-manage.sh deny  tcp 8080
set -euo pipefail
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }

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
        none) _ "фаервола нет — все входящие открыты" \
                "no firewall — all inbound open" ;;
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
        none) _ "фаервола нет — снаружи открыто всё" \
                "no firewall — everything is open from outside" ;;
    esac
    ;;
allow|deny)
    [ -n "$PORT" ] || { _ "!! нужен порт" "!! port required"; exit 2; }
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
                # идемпотентно: правило на этот порт уже есть (ssh/cloak) — не дублируем
                nft list chain inet dgcloak input 2>/dev/null \
                  | grep "$PROTO dport $PORT " >/dev/null || \
                nft add rule inet dgcloak input "$PROTO" dport "$PORT" ct state new accept comment \"manual\"
            else
                # порт мог быть открыт несколькими правилами — снимаем ВСЕ
                n=0
                while H=$(nft -a list chain inet dgcloak input 2>/dev/null \
                          | grep "$PROTO dport $PORT " \
                          | grep -oP 'handle \K\d+' | head -1) && [ -n "$H" ]; do
                    nft delete rule inet dgcloak input handle "$H"; n=$((n+1))
                done
                [ "$n" -gt 0 ] || _ "правило не найдено" "rule not found"
            fi
            nft list ruleset > /etc/nftables.conf ;;
        nftables)
            _ "!! чужой ruleset nftables — правьте вручную на сервере" \
              "!! foreign nftables ruleset — edit manually on the server"; exit 1 ;;
        iptables-persistent|iptables-custom)
            if [ "$ACTION" = allow ]; then
                iptables -C INPUT -p "$PROTO" --dport "$PORT" \
                    -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null || \
                iptables -A INPUT -p "$PROTO" --dport "$PORT" -m conntrack --ctstate NEW -j ACCEPT
            else
                # убираем ВСЕ accept-правила на этот порт (дупликаты возможны)
                while iptables -D INPUT -p "$PROTO" --dport "$PORT" \
                        -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null \
                   || iptables -D INPUT -p "$PROTO" --dport "$PORT" \
                        -j ACCEPT 2>/dev/null; do :; done
            fi
            netfilter-persistent save >/dev/null 2>&1 \
              || iptables-save > /etc/iptables/rules.v4 2>/dev/null \
              || _ "!! правило живёт до перезагрузки (на диск не сохранено)" \
                   "!! rule lives until reboot (not saved to disk)" ;;
        none)
            _ "!! фаервол не установлен — сначала шаг «Фаервол»" \
              "!! no firewall installed — run the 'Firewall' step first"; exit 1 ;;
    esac
    echo "ok: $ACTION $PROTO/$PORT"
    ;;
*)
    echo "!! unknown action $ACTION"; exit 2 ;;
esac
echo "=== OK fw-manage ==="
