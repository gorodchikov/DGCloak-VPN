#!/bin/bash
# Создание клиентского сертификата OpenVPN + выгрузка материалов бандла в stdout.
# Использование: sudo bash user-cert.sh <username>
# Имя: только [a-zA-Z0-9_-] (идёт в CN и в пути файлов).
set -euo pipefail
USER_NAME=${1:?usage: user-cert.sh <name>}
[[ "$USER_NAME" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "!! bad name: [a-zA-Z0-9_-] only"; exit 2; }

# CA может лежать в /root или в домашнем каталоге (зависит от того, как делали PKI)
CADIR=""
for d in /root/openvpn-ca /home/*/openvpn-ca; do
    if [ -d "$d/pki" ]; then CADIR="$d"; break; fi
done
[ -n "$CADIR" ] || { echo "!! openvpn-ca не найден (ни /root, ни /home/*/openvpn-ca)"; exit 1; }
cd "$CADIR"
if [ ! -f "pki/issued/${USER_NAME}.crt" ]; then
    # easyrsa/openssl могут читать stdin больше одного раза и на ровном EOF
    # падают → многострочный printf вместо одиночного echo (буфер умещается
    # в пайп, SIGPIPE не будет)
    printf '\n%.0s' $(seq 50) | ./easyrsa gen-req "$USER_NAME" nopass
    printf 'yes\n%.0s' $(seq 50) | ./easyrsa sign-req client "$USER_NAME"
    echo "cert: создан $USER_NAME"
else
    echo "cert: уже существует $USER_NAME"
fi

echo "===CA===";    cat pki/ca.crt
echo "===CERT===";  cat "pki/issued/${USER_NAME}.crt"
echo "===KEY===";   cat "pki/private/${USER_NAME}.key"
echo "===TA===";    cat ta.key
echo "===END==="
