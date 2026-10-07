#!/bin/bash
# Этап 1: OpenVPN server + easyrsa PKI (docs/server-setup.md, п.1).
# Идемпотентно: если PKI уже есть — пропускает генерацию, но приводит server.conf
# к эталону и перезапускает сервис.
# Использование: sudo bash deploy-openvpn.sh [proto]   (proto = udp|tcp, дефолт udp)
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

OVPN_PROTO=${1:-udp}
if [ "$OVPN_PROTO" != "udp" ] && [ "$OVPN_PROTO" != "tcp" ]; then
    echo "!! proto must be udp|tcp"; exit 2
fi

# DPkg::Lock::Timeout: unattended-upgrades на свежем VPS может держать лок apt
apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false update -qq
apt-get -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false install -y -qq openvpn easy-rsa

CADIR=/root/openvpn-ca
if [ ! -d "$CADIR" ]; then
    if command -v make-cadir >/dev/null 2>&1; then
        make-cadir "$CADIR"
    else
        # make-cadir — debian-хелпер; на части систем его нет — копируем вручную
        mkdir -p "$CADIR"
        cp -r /usr/share/easy-rsa/* "$CADIR/"
    fi
fi
cd "$CADIR"

gen_ta() {
    # openvpn >=2.5: --genkey secret f; openvpn 2.4: --genkey --secret f
    openvpn --genkey secret "$1" 2>/dev/null \
        || openvpn --genkey --secret "$1"
}

if [ ! -f pki/issued/server.crt ] || [ ! -f pki/private/server.key ]; then
    # неполная PKI (прошлый прогон оборвался) — чистый рестарт
    [ -d pki ] && rm -rf pki
    # --batch: без интерактива; printf-stdin ломался на SIGPIPE/pipefail
    ./easyrsa --batch init-pki
    ./easyrsa --batch build-ca nopass
    ./easyrsa --batch gen-req server nopass
    ./easyrsa --batch sign-req server server
    ./easyrsa --batch gen-dh
    ./easyrsa --batch gen-crl
    gen_ta ta.key
    echo "PKI: создана"
else
    echo "PKI: уже есть, пропускаю генерацию"
fi
# материалы в /etc/openvpn/server — всегда (идемпотентно, даже после обрыва)
[ -f ta.key ] || gen_ta ta.key
install -m600 -o root -g root pki/private/server.key /etc/openvpn/server/server.key
install -m644 -o root -g root pki/ca.crt /etc/openvpn/server/ca.crt
install -m644 -o root -g root pki/issued/server.crt /etc/openvpn/server/server.crt
install -m644 -o root -g root pki/dh.pem /etc/openvpn/server/dh.pem
install -m644 -o root -g root pki/crl.pem /etc/openvpn/server/crl.pem
install -m600 -o root -g root ta.key /etc/openvpn/server/ta.key

mkdir -p /etc/openvpn/server/ccd

# management-порт для «отключить юзера сейчас» (kill CN). Пароль — файл, порт только localhost.
if [ ! -f /etc/openvpn/server/mgmt.pass ]; then
    head -c 24 /dev/urandom | base64 > /etc/openvpn/server/mgmt.pass
    chmod 600 /etc/openvpn/server/mgmt.pass
fi

cat > /etc/openvpn/server/server.conf <<EOF
cd /etc/openvpn/server
port 1194
proto $OVPN_PROTO
local 127.0.0.1
dev tun
topology subnet
server 10.8.0.0 255.255.255.0
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 1.1.1.1"
push "dhcp-option DNS 8.8.8.8"
ca ca.crt
cert server.crt
key server.key
dh dh.pem
tls-crypt ta.key
crl-verify crl.pem
remote-cert-tls client
cipher AES-256-GCM
auth SHA256
keepalive 10 120
user nobody
group nogroup
persist-key
persist-tun
client-config-dir ccd
management 127.0.0.1 7505 /etc/openvpn/server/mgmt.pass
status /var/log/openvpn-status.log
verb 3
EOF

systemctl enable --now openvpn-server@server
systemctl restart openvpn-server@server
sleep 2
systemctl is-active openvpn-server@server

if [ "$OVPN_PROTO" = "udp" ]; then
    ss -ulpn | grep 1194 || { echo "!! openvpn not listening on udp/1194"; exit 1; }
else
    ss -tlpn | grep 1194 || { echo "!! openvpn not listening on tcp/1194"; exit 1; }
fi
ss -tlpn | grep 7505 || echo "!! mgmt :7505 not listening"
echo "=== OK deploy-openvpn (proto=$OVPN_PROTO) ==="
