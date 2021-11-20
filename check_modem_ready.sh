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
  (mmcli -m 0 > /dev/null) && echo modem found && mmcli -m 0 --enable && break || true
  if [ 9 -le $i ]; then
    echo no modem found
    false
  fi
  echo waiting on modem $i
  sleep 1
done
sleep 1
#/opt/modem_config/modem_config.py || true
#mmcli -m 0 --location-set-supl-server=supl.google.com:7275 || true
mmcli -m 0 --location-enable-gps-raw --location-enable-gps-nmea && \
  #mmcli -m 0 --location-enable-agps-msa && \
  mmcli -m 0 --location-enable-agps-msb && \
  mmcli -m 0 --location-set-enable-signal && \
  mmcli -m 0 --location-set-gps-refresh-rate=10 && \
  # trigger the gps sessions
  (echo "\$GPS_START" > /dev/ttyUSB1) || true
sudo qmicli -d /dev/cdc-wdm1 -p --loc-set-nmea-types="gga|gsa|gsv"
#Note:
# root@MP:~ wget http://xtrapath1.izatcloud.net/xtra3grc.bin
# --2018-09-21 11:33:49--  http://xtrapath1.izatcloud.net/xtra3grc.bin
# Resolving xtrapath1.izatcloud.net (xtrapath1.izatcloud.net)... 
# 52.85.255.229, 52.85.255.242, 52.85.255.168, ...
# Connecting to xtrapath1.izatcloud.net 
# (xtrapath1.izatcloud.net)|52.85.255.229|:80... connected.
# HTTP request sent, awaiting response... 200 OK
# Length: 29150 (28K) [application/octet-stream]
# Saving to: ‘xtra3grc.bin.6’

# xtra3grc.bin.6  100%[==>]  28.47K  
# --.-KB/sin 0s

# 2018-09-21 11:33:49 (324 MB/s) - ‘xtra3grc.bin.6’ saved [29150/29150]

# root@MP:~ mmcli -m 0 --location-inject-assistance-data=xtra3grc.bin
# successfully injected assistance data
