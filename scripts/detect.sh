#!/bin/bash
# Read-only аудит сервера перед деплоем. Ничего не меняет.
# Использование: bash detect.sh   (можно без sudo, но часть вывода будет пустой)
set -uo pipefail

echo "===OS==="
. /etc/os-release 2>/dev/null
echo "ID=${ID:-?}"
echo "VERSION=${VERSION_ID:-?}"
echo "PRETTY=${PRETTY_NAME:-?}"
uname -r

echo "===ARCH==="
uname -m

echo "===INIT==="
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    echo "systemd"
else
    echo "other"
fi

echo "===PKG==="
if command -v apt-get >/dev/null 2>&1; then echo "apt"
elif command -v dnf >/dev/null 2>&1; then echo "dnf"
elif command -v yum >/dev/null 2>&1; then echo "yum"
elif command -v pacman >/dev/null 2>&1; then echo "pacman"
else echo "none"; fi

echo "===SUDO==="
if [ "$(id -u)" = "0" ]; then
    echo "root"
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    echo "nopasswd"
else
    echo "no"
fi

echo "===SSHD_PORTS==="
ss -tlnp 2>/dev/null | grep -oP ':\K\d+(?= .*sshd)' | sort -un || echo "?"

echo "===HOST==="
hostname

echo "===EXT_IF==="
EXT_IF=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
echo "EXT_IF=$EXT_IF"
ip -4 addr show "$EXT_IF" 2>/dev/null | grep -oP 'inet \K[\d.]+' | head -1

echo "===PUBIP==="
curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo "?"

echo "===PORT443==="
ss -tlnp 2>/dev/null | grep ':443 ' || echo "free"

echo "===LISTEN==="
ss -tulpn 2>/dev/null | grep -E ':(443|1194|7505|8081|22)\b' || echo "none of 22/443/1194/7505/8081"

echo "===UFW==="
if command -v ufw >/dev/null 2>&1; then ufw status 2>/dev/null | head -5; else echo "no ufw"; fi

echo "===FIREWALLD==="
if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active firewalld 2>/dev/null | grep -q active; then
    echo "firewalld active"
else
    echo "no firewalld"
fi

echo "===DOCKER==="
if command -v docker >/dev/null 2>&1; then
    docker ps -a --format '{{.Names}} {{.Ports}}' 2>/dev/null || echo "docker cli есть, демон недоступен"
else
    echo "no docker"
fi

echo "===PKGS==="
dpkg -l 2>/dev/null | grep -E '^ii +[a-z]' | awk '{print $2}' | grep -E '^(docker|containerd|openvpn|easy-rsa|ufw|iptables-persistent|netcat-openbsd)' || echo "none"

echo "===UNITS==="
systemctl list-units --all --no-legend 2>/dev/null | grep -E 'openvpn-server@|ck-server|docker\.|containerd|netfilter' | awk '{print $1, $4}' || echo "none"

echo "===CKDIR==="; ls /etc/ck-server 2>/dev/null || echo "none"
echo "===OVPN==="; ls /etc/openvpn/server 2>/dev/null | head -10 || echo "none"
echo "===FORWARD==="; sysctl -n net.ipv4.ip_forward 2>/dev/null || echo "?"

echo "===DISK==="
df -m / | awk 'NR==2{print "free_mb="$4}'

echo "===DONE==="
