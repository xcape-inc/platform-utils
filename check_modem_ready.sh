#!/bin/bash
set -e
trap 'catch $? $LINENO' ERR

normal_failing=17
catch() {
  echo "Error $1 occurred on $2"
  (>/dev/null blinkMplay.sh ${normal_failing} 2>&1) || true
}
set -euo pipefail

(blinkMplay.sh 10 || true)
(blinkMplay.sh -X 19 || true)
for i in $(seq 0 9); do
  (mmcli -m 0 > /dev/null) && echo modem found && break || true
  if [ 9 -le $i ]; then
    echo no modem found
    false
  fi
  echo waiting on modem $i
  sleep 1
done
sleep 1
mmcli -m 0 --location-enable-gps-raw --location-enable-gps-nmea || true