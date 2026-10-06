#!/bin/bash
# Ретрофит management-порта OpenVPN на старый деплой (без полного передеплоя).
# Порт только localhost:7505, пароль в mgmt.pass (600).
set -e

if [ ! -f /etc/openvpn/server/mgmt.pass ]; then
    head -c 24 /dev/urandom | base64 > /etc/openvpn/server/mgmt.pass
    chmod 600 /etc/openvpn/server/mgmt.pass
    echo "mgmt.pass создан"
fi

if ! grep -q '^management ' /etc/openvpn/server/server.conf; then
    cp -a /etc/openvpn/server/server.conf \
        /etc/openvpn/server/server.conf.bak.$(date +%s)
    sed -i '/^status /i management 127.0.0.1 7505 /etc/openvpn/server/mgmt.pass' \
        /etc/openvpn/server/server.conf
    grep -q '^management ' /etc/openvpn/server/server.conf || \
        echo 'management 127.0.0.1 7505 /etc/openvpn/server/mgmt.pass' \
            >> /etc/openvpn/server/server.conf
    echo "server.conf: добавлен management"
fi

systemctl restart openvpn-server@server
sleep 2
systemctl is-active openvpn-server@server
ss -tlpn | grep 7505 || { echo "!! mgmt не поднялся"; exit 1; }
echo "=== OK enable-mgmt ==="
