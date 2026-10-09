#!/bin/bash
# Ретрофит management-порта OpenVPN на старый деплой (без полного передеплоя).
# Порт только localhost:7505, пароль в mgmt.pass (600).
set -e
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }

if [ ! -f /etc/openvpn/server/mgmt.pass ]; then
    head -c 24 /dev/urandom | base64 > /etc/openvpn/server/mgmt.pass
    chmod 600 /etc/openvpn/server/mgmt.pass
    _ "mgmt.pass создан" "mgmt.pass created"
fi

if ! grep -q '^management ' /etc/openvpn/server/server.conf; then
    cp -a /etc/openvpn/server/server.conf \
        /etc/openvpn/server/server.conf.bak.$(date +%s)
    sed -i '/^status /i management 127.0.0.1 7505 /etc/openvpn/server/mgmt.pass' \
        /etc/openvpn/server/server.conf
    grep -q '^management ' /etc/openvpn/server/server.conf || \
        echo 'management 127.0.0.1 7505 /etc/openvpn/server/mgmt.pass' \
            >> /etc/openvpn/server/server.conf
    _ "server.conf: добавлен management" "server.conf: management added"
fi

systemctl restart openvpn-server@server
sleep 2
systemctl is-active openvpn-server@server
ss -tlpn | grep 7505 || { _ "!! mgmt не поднялся" "!! mgmt did not come up"; exit 1; }
echo "=== OK enable-mgmt ==="
