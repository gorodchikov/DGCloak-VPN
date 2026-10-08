#!/usr/bin/env python3
# Хелпер для management-интерфейса OpenVPN (localhost:7505, пароль из mgmt.pass).
# Запускается на сервере под sudo: python3 ovpn-mgmt.py <cmd> [arg]
#   kill <CN>   — разорвать живую сессию юзера немедленно
#   status      — содержимое management status (клиенты, байты)
#   clients     — только список подключённых CN (удобно парсить)
import socket
import sys
import time

MGMT = ("127.0.0.1", 7505)
PASS_FILE = "/etc/openvpn/server/mgmt.pass"


def talk(cmds, wait=0.6):
    s = socket.create_connection(MGMT, 5)
    s.settimeout(2)
    pw = open(PASS_FILE, "rb").read().strip()
    s.sendall(pw + b"\n")
    time.sleep(0.2)
    for c in cmds:
        s.sendall(c.encode() + b"\n")
    time.sleep(wait)
    out = b""
    try:
        while True:
            d = s.recv(65535)
            if not d:
                break
            out += d
    except socket.timeout:
        pass
    s.close()
    return out.decode(errors="replace")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "clients"
    if cmd == "kill":
        cn = sys.argv[2]
        try:
            print(talk(["kill %s" % cn]))
        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            print("mgmt недоступен: %s" % e)
    elif cmd == "status":
        print(talk(["status", "exit"]))
    elif cmd == "clients":
        try:
            txt = talk(["status", "exit"])
        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            print("mgmt недоступен: %s" % e)
            sys.exit(0)
        for line in txt.splitlines():
            # CLIENT_LIST,cn,real-addr,virt-addr,...
            if line.startswith("CLIENT_LIST,"):
                print(line.split(",")[1])
    else:
        print("usage: ovpn-mgmt.py kill <CN> | status | clients", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
