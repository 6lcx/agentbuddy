set -eu
work=$(mktemp -d /tmp/agentbuddy-cgroup-XXXXXX)
unit=agentbuddy-t16-tree-$(date +%s)
trap 'systemctl stop "$unit" 2>/dev/null || true; systemctl reset-failed "$unit" 2>/dev/null || true' EXIT
systemd-run --expand-environment=no --quiet --unit="$unit" --property=KillMode=control-group /bin/sh -c 'sleep 100 & echo $! > "$1/child"; echo $$ > "$1/parent"; wait' sh "$work"
for i in $(seq 1 100); do test -f "$work/parent" && break; sleep 0.05; done
parent=$(cat "$work/parent")
child=$(cat "$work/child")
kill -KILL "$parent"
for i in $(seq 1 100); do
    test ! -d "/proc/$child" && break
    sleep 0.05
done
if test -d "/proc/$child"; then
    state=$(awk '{print $3}' "/proc/$child/stat")
    test "$state" = Z
fi
systemctl show "$unit" -p ActiveState -p Result
systemctl reset-failed "$unit"
echo 'Linux control-group child cleanup passed'
