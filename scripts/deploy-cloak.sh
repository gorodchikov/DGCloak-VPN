#!/bin/bash
# Этап 3: ck-server (docs/server-setup.md, п.3).
# Использование: sudo bash deploy-cloak.sh <mask_domain> [proto] [ck_ver] [port]
set -euo pipefail
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }

MASK_DOMAIN=${1:?usage: deploy-cloak.sh <mask_domain> [proto] [ck_ver] [port]}
OVPN_PROTO=${2:-udp}          # udp|tcp — совпадает с proto в server.conf и "UDP" у клиента
CK_VER=${3:-2.12.0}
CK_PORT=${4:-443}             # внешний TCP-порт Cloak (BindAddr + проверка listen)

# архитектура бинарника по uname -m (VPS бывают arm64 — Oracle, Ampere и т.п.)
case "$(uname -m)" in
    x86_64|amd64)   CK_ARCH=amd64 ;;
    aarch64|arm64)  CK_ARCH=arm64 ;;
    armv7l|armv6l)  CK_ARCH=arm ;;
    i386|i686)      CK_ARCH=386 ;;
    *) _ "!! неизвестная архитектура: $(uname -m)" \
          "!! unknown architecture: $(uname -m)"; exit 1 ;;
esac

if ! command -v ck-server >/dev/null; then
    curl -fsSL -o /usr/local/bin/ck-server \
        "https://github.com/cbeuw/Cloak/releases/download/v${CK_VER}/ck-server-linux-${CK_ARCH}-v${CK_VER}"
    chmod +x /usr/local/bin/ck-server
    echo "installed ($CK_ARCH): $(ck-server -v 2>&1 | head -1 || echo '?')"
fi

if [ ! -f /etc/ck-server/ckserver.json ]; then
    KP=$(ck-server -k)                       # формат: <public>,<private>
    PUB=${KP%%,*}
    PRIV=${KP##*,}
    ADMIN_UID=$(ck-server -u)

    mkdir -p /etc/ck-server /var/lib/ck-server/db-backup
    cat > /etc/ck-server/ckserver.json <<EOF
{
  "ProxyBook": { "openvpn": ["$OVPN_PROTO", "127.0.0.1:1194"] },
  "BindAddr": [":$CK_PORT"],
  "BypassUID": [],
  "RedirAddr": "$MASK_DOMAIN",
  "PrivateKey": "$PRIV",
  "AdminUID": "$ADMIN_UID",
  "DatabasePath": "/var/lib/ck-server/userinfo.db",
  "BackupDirPath": "/var/lib/ck-server/db-backup"
}
EOF
    echo "$PUB"       > /etc/ck-server/publickey.txt
    echo "$ADMIN_UID" > /etc/ck-server/adminuid.txt
    chmod 600 /etc/ck-server/adminuid.txt
else
    # Конфиг есть — обновим только маскировку/протокол, ключи не трогаем
    python3 - "$MASK_DOMAIN" "$OVPN_PROTO" "$CK_PORT" <<'PY'
import json, sys
p = "/etc/ck-server/ckserver.json"
c = json.load(open(p))
c["RedirAddr"] = sys.argv[1]
c["ProxyBook"] = {"openvpn": [sys.argv[2], "127.0.0.1:1194"]}
c["BindAddr"] = [":%s" % sys.argv[3]]
json.dump(c, open(p, "w"), indent=2)
PY
fi

id cloak >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d /var/lib/ck-server cloak
chown -R cloak:cloak /var/lib/ck-server
chown root:cloak /etc/ck-server/ckserver.json
chmod 640 /etc/ck-server/ckserver.json   # приватный ключ: читает только сервис

cat > /etc/systemd/system/ck-server.service <<'EOF'
[Unit]
Description=Cloak server
After=network-online.target
Wants=network-online.target

[Service]
User=cloak
Group=cloak
ExecStart=/usr/local/bin/ck-server -c /etc/ck-server/ckserver.json
Restart=always
RestartSec=3
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ck-server
systemctl restart ck-server
sleep 2
systemctl is-active ck-server
if ! ss -tlpn | grep ":$CK_PORT " >/dev/null; then
    _ "!! ck-server не слушает :$CK_PORT" "!! ck-server is not listening on :$CK_PORT"
    ss -tlnp | grep ":$CK_PORT " && _ "!! порт $CK_PORT занят другим сервисом (см. выше)" \
        "!! port $CK_PORT is held by another service (see above)"
    exit 1
fi
echo "===PUB==="; cat /etc/ck-server/publickey.txt
echo "===ADMIN_UID==="; cat /etc/ck-server/adminuid.txt
echo "=== OK deploy-cloak ==="
