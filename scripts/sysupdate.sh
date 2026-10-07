#!/bin/bash
# Обновление системы: apt update + full-upgrade + autoremove.
# Не трогает sshd-конфиг; если обновился ssh — демон перезапускается пакетом,
# но listen-порт/ключи не меняются.
# Использование: sudo bash sysupdate.sh
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
# conffile-подсказки не должны интерактивить: новые версии конфигов не берём
APT_OPTS="-o Dpkg::Options::=--force-confold -o DPkg::Lock::Timeout=300 -o Acquire::Check-Valid-Until=false"

echo "=== apt update ==="
apt-get $APT_OPTS update
echo "=== full-upgrade ==="
apt-get $APT_OPTS -y full-upgrade
echo "=== autoremove ==="
apt-get $APT_OPTS -y --purge autoremove

if [ -f /var/run/reboot-required ]; then
    echo "===REBOOT=== required"
    cat /var/run/reboot-required.pkgs 2>/dev/null | head -10 || true
fi
echo "=== OK sysupdate ==="
