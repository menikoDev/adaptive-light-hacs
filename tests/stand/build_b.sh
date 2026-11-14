#!/usr/bin/env bash
# Stand B: Home Assistant 2026.11.1 with ONLY this integration (no YAML package), same fake lamps as stand A.
# Needs the harness venv (/cursor/stores/self/internal/ha-harness/setup.sh creates /tmp/hatest/venv). Port 18125, token /tmp/hatest_int/token.
# usage: bash build_b.sh [start|stop|fresh]
set -e
H=/cursor/stores/self/internal/ha-harness
D=$(cd "$(dirname "$0")/../.." && pwd)
T=/tmp/hatest_int; V=/tmp/hatest/venv
stop() { [ -f $T/ha.pid ] && kill $(cat $T/ha.pid) 2>/dev/null || true; sleep 2; }
start() {
  cd $T
  (SETUP_PORT=18125 setsid nohup $V/bin/hass -c $T/config --log-file $T/ha.log >$T/ha.stdout 2>&1 & echo $! > $T/ha.pid)
  for i in $(seq 1 300); do
    curl -sf -H "Authorization: Bearer $(cat $T/token)" http://127.0.0.1:18125/api/ >/dev/null 2>&1 && { echo "B up after ${i}s"; return; }
    sleep 1
  done
  echo "B did not start, see $T/ha.log"; exit 1
}
case "${1:-fresh}" in
  stop) stop; exit;;
  start) start; exit;;
esac
stop
rm -rf $T; mkdir -p $T/config/.storage
mkdir -p $T/config/custom_components $T/config/harness
cp -r $H/overlay/custom_components/fakelight $T/config/custom_components/fakelight
cp $H/overlay/harness/{lights,input_select,input_text,input_boolean}.yaml $T/config/harness/
ln -s $D/custom_components/adaptive_light $T/config/custom_components/adaptive_light
{ cat $H/overlay/harness/header.yaml
  echo "    custom_components.adaptive_light: debug"
  echo "light: !include harness/lights.yaml"
  echo "input_select: !include harness/input_select.yaml"
  echo "input_text: !include harness/input_text.yaml"
  echo "input_boolean: !include harness/input_boolean.yaml"; } > $T/config/configuration.yaml
$V/bin/python $H/gen_auth.py $T/config $T/token
start
