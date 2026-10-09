#!/bin/bash
# Создание клиентского сертификата OpenVPN + выгрузка материалов бандла в stdout.
# Использование: sudo bash user-cert.sh <username>
# Имя: только [a-zA-Z0-9_-] (идёт в CN и в пути файлов).
set -euo pipefail
# Локализация вывода: админка передаёт DG_LANG=ru|en
_() { if [ "${DG_LANG:-ru}" = en ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
USER_NAME=${1:?usage: user-cert.sh <name>}
[[ "$USER_NAME" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "!! bad name: [a-zA-Z0-9_-] only"; exit 2; }

# CA может лежать в /root или в домашнем каталоге (зависит от того, как делали PKI)
CADIR=""
for d in /root/openvpn-ca /home/*/openvpn-ca; do
    if [ -d "$d/pki" ]; then CADIR="$d"; break; fi
done
[ -n "$CADIR" ] || { _ "!! openvpn-ca не найден (ни /root, ни /home/*/openvpn-ca)" \
    "!! openvpn-ca not found (neither /root nor /home/*/openvpn-ca)"; exit 1; }
cd "$CADIR"
# залипший lock от убитого easyrsa (обрыв SSH/таймаут): PID мёртв — снимаем
if [ -f pki/lock.file ]; then
    LPID=$(cat pki/lock.file 2>/dev/null)
    kill -0 "$LPID" 2>/dev/null || rm -f pki/lock.file
fi
if [ ! -f "pki/issued/${USER_NAME}.crt" ]; then
    # --batch: без интерактива (printf-stdin ломался на SIGPIPE/pipefail)
    ./easyrsa --batch gen-req "$USER_NAME" nopass
    ./easyrsa --batch sign-req client "$USER_NAME"
    _ "cert: создан $USER_NAME" "cert: created $USER_NAME"
else
    _ "cert: уже существует $USER_NAME" "cert: already exists $USER_NAME"
fi

echo "===CA===";    cat pki/ca.crt
echo "===CERT===";  cat "pki/issued/${USER_NAME}.crt"
echo "===KEY===";   cat "pki/private/${USER_NAME}.key"
echo "===TA===";    cat ta.key
echo "===END==="
