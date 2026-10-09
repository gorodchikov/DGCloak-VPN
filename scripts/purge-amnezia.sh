#!/bin/bash
# Снятие наследия Amnezia: контейнеры amnezia-*, их образы, данные.
# Хирургически: чужой docker-контент НЕ трогаем. Docker purge — только
# если после чистки Amnezia в docker больше ничего не осталось.
# Идемпотентно. НЕ трогает: ufw, OpenVPN, ck-server, cron.
set -uo pipefail
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
export DEBIAN_FRONTEND=noninteractive

if ! command -v docker >/dev/null 2>&1; then
    _ "docker нет — нечего чистить" "no docker — nothing to clean"; echo DONE; exit 0
fi

_ '=== 1. контейнеры/образы Amnezia ===' '=== 1. Amnezia containers/images ==='
# имена контейнеров Amnezia: amnezia-openvpn, amnezia-awg, amnezia-ipsec,
# amnezia-shadowsocks, amnezia-cloak, amnezia-xray, amnezia-website, ...
AMC=$(docker ps -aq --filter 'name=amnezia' 2>/dev/null || true)
if [ -n "$AMC" ]; then
    docker rm -f $AMC 2>/dev/null || true
    _ "удалены контейнеры: $(echo $AMC | wc -w)" \
      "containers removed: $(echo $AMC | wc -w)"
else
    _ "amnezia-контейнеров нет" "no amnezia containers"
fi
AMI=$(docker images --format '{{.ID}} {{.Repository}}' 2>/dev/null \
      | grep -i amnezia | awk '{print $1}' || true)
if [ -n "$AMI" ]; then
    docker rmi -f $AMI 2>/dev/null || true
    _ "удалены образы: $(echo $AMI | wc -w)" \
      "images removed: $(echo $AMI | wc -w)"
else
    _ "amnezia-образов нет" "no amnezia images"
fi

_ '=== 2. данные Amnezia ===' '=== 2. Amnezia data ==='
rm -rf /opt/amnezia /root/amnezia* /etc/amnezia 2>/dev/null || true

_ '=== 3. docker целиком — только если в нём больше ничего нет ===' \
  '=== 3. docker entirely — only if nothing else is left ==='
LEFT_C=$(docker ps -aq 2>/dev/null | wc -l)
LEFT_I=$(docker images -q 2>/dev/null | wc -l)
if [ "$LEFT_C" = "0" ] && [ "$LEFT_I" = "0" ]; then
    systemctl stop docker.socket docker.service containerd.service 2>/dev/null || true
    systemctl disable docker.socket docker.service containerd.service 2>/dev/null || true
    docker network prune -f 2>/dev/null || true
    docker volume prune -f 2>/dev/null || true
    apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false purge -y \
        docker.io docker-ce docker-ce-cli containerd containerd.io runc || true
    dpkg -l | grep -E '^ii  (docker|containerd)' >/dev/null && \
        apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false purge -y docker.io containerd || true
    apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false autoremove -y --purge || true
    rm -rf /var/lib/docker /var/lib/containerd /etc/docker /etc/containerd \
           /opt/containerd /run/docker /run/docker.sock /run/containerd 2>/dev/null || true
    for iface in docker0 $(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' \
                     | grep -E '^br-' || true); do
        ip link set "$iface" down 2>/dev/null || true
        ip link delete "$iface" 2>/dev/null || true
    done
    # чистим DOCKER-цепочки iptables (filter + nat)
    for t in filter nat; do
        iptables -t $t -S 2>/dev/null | grep -oE 'DOCKER[A-Z-]*' | sort -u \
            | while read -r ch; do
            iptables -t $t -S | grep -- "-j $ch" | sed "s/^-A /-D /" \
                | while read -r del; do
                eval "iptables -t $t $del" 2>/dev/null || true
            done
            iptables -t $t -F "$ch" 2>/dev/null || true
            iptables -t $t -X "$ch" 2>/dev/null || true
        done
    done
    groupdel docker 2>/dev/null || true
    rm -f /usr/local/bin/docker-compose /usr/local/libexec/docker/cli-plugins/* 2>/dev/null || true
    ldconfig 2>/dev/null || true
    _ "docker полностью снесён (был пустой)" "docker fully removed (was empty)"
else
    _ "!! docker оставлен: чужих контейнеров=$LEFT_C образов=$LEFT_I" \
      "!! docker kept: foreign containers=$LEFT_C images=$LEFT_I"
    _ "!! если 443 занят чужим контейнером — освободи вручную" \
      "!! if 443 is held by a foreign container — free it manually"
fi

echo '=== verify ==='
command -v docker >/dev/null 2>&1 && docker ps -a --format '{{.Names}}' 2>/dev/null || echo "docker: CLEAN"
ss -tlnp 2>/dev/null | grep ':443 ' || echo "443: FREE"
echo DONE
