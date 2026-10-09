#!/bin/bash
# Кто держит TCP-порт: печатает ровно одну строку
#   FREE                          — порт свободен (v4+v6, только LISTEN)
#   OWNER=docker:<контейнер>      — порт опубликован из контейнера
#                                   (docker-proxy или host-network через cgroup)
#   OWNER=svc:<systemd-юнит>      — держит system-сервис
#   OWNER=proc:<процесс>          — держит процесс без юнита
# Дополнительно (если есть): AUTOSTART=<имя,имя> — остановленные
# amnezia-контейнеры с restart-policy != no (воскреснут после ребута).
# Запуск: sudo bash port-owner.sh <port>
PORT="$1"
[ -z "$PORT" ] && { echo "FREE"; exit 0; }

LINE=$(ss -tlnpH "sport = :$PORT" 2>/dev/null | head -1)

if [ -z "$LINE" ]; then
    echo "FREE"
else
    PROC=$(echo "$LINE" | grep -oE '"[^"]+"' | tr -d '"' | head -1)
    PID=$(echo "$LINE" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | head -1)

    if echo "$LINE" | grep -q docker-proxy; then
        # классический путь: порт опубликован (-p 443:xxx)
        CNAME=$(docker ps --filter "publish=$PORT" \
                --format '{{.Names}}' 2>/dev/null | head -1)
        echo "OWNER=docker:${CNAME:-container}"
    elif [ -n "$PID" ] && grep -qE 'docker[-/][0-9a-f]{12,64}' \
            "/proc/$PID/cgroup" 2>/dev/null; then
        # host-network контейнер: docker-proxy нет, владельца видно
        # по cgroup процесса
        CID=$(grep -oE 'docker[-/][0-9a-f]{12,64}' "/proc/$PID/cgroup" | \
              grep -oE '[0-9a-f]{12,64}' | head -1)
        CNAME=$(docker inspect -f '{{.Name}}' "$CID" 2>/dev/null | tr -d '/')
        echo "OWNER=docker:${CNAME:-${CID:0:12}}"
    else
        UNIT=""
        [ -n "$PID" ] && UNIT=$(systemctl status "$PID" 2>/dev/null | \
            grep -oE '[a-zA-Z0-9_.@-]+\.service' | head -1)
        if [ -n "$UNIT" ]; then
            echo "OWNER=svc:$UNIT"
        else
            echo "OWNER=proc:${PROC:-?}"
        fi
    fi
fi

# остановленные amnezia-* с автозапуском — воскреснут после ребута
if command -v docker >/dev/null 2>&1; then
    AS=""
    for c in $(docker ps -a --filter 'name=amnezia' \
               --format '{{.Names}}' 2>/dev/null); do
        RP=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' \
             "$c" 2>/dev/null)
        RUN=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)
        [ "$RUN" != "true" ] && [ "$RP" != "no" ] && AS="$AS,$c"
    done
    [ -n "$AS" ] && echo "AUTOSTART=${AS#,}"
fi
echo DONE
