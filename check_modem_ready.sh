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

# inject location assistance data
for x in {1..4}; do
  curl -fL http://xtrapath1.izatcloud.net/xtra3grc.bin -o /tmp/xtra3grc.bin && mmcli -m 0 --location-inject-assistance-data=/tmp/xtra3grc.bin && break || true
done
rm -f /tmp/xtra3grc.bin

# set the modem manager to handle stuff.  note: this       dables th serial port!
#mmcli -m 0 --location-set-supl-server=supl.google.com:7275 || true
#mmcli -m 0  --location-enable-agps-msb
#mmcli -m 0 --location-enable-gps-raw --location-enable-gps-nmea && \
#  #mmcli -m 0 --location-enable-agps-msa && \
#  mmcli -m 0 --location-set-gps-refresh-rate=0 && \
#  mmcli -m 0 --location-set-enable-signal

GPS_DEV=$(find /dev -mindepth 1 -maxdepth 1 -type l -iname 'mm-gps*' | head -1)
AT_DEV=$(find /dev -mindepth 1 -maxdepth 1 -type l -iname 'mm-at*' | head -1)

# TODO: Force aGPS by clearing data
#cat /dev/ttyUSB2 &
#TTY_PID=$!
#printf 'AT!ENTERCND="A710"\r\n' > /dev/ttyUSB2
#sleep 2
##printf 'AT!GPSEND=0,255\r\n' > /dev/ttyUSB2
##sleep 2
#printf 'AT!GPSCOLDSTART\r\n' > /dev/ttyUSB2
#sleep 5
#kill $TTY_PID || true

# trigger the gps sessions
(echo "\$GPS_START" > "${GPS_DEV}") || true
#sleep 1

echo '# Devices gpsd should collect to at boot time.
# They need to be read/writeable, either by user gpsd or the group dialout.
DEVICES="'"${GPS_DEV}"'"
#DEVICES="/tmp/GPSDEVICE"

#DEVICES="tcp://127.0.0.1:12345"

# Other options you want to pass to gpsd
#GPSD_OPTIONS="-s115200"
#GPSD_OPTIONS="-s9600"
' > /etc/default/gpsd

# Get gpsd to use the right driver
for x in {1..4}; do
  gpsctl -n -D 4 "${GPS_DEV}" && break || true
done

#sudo qmicli -d /dev/cdc-wdm1 -p --loc-set-nmea-types="gga|gsa|gsv"
#qmicli -d /dev/cdc-wdm1 -p --client-cid=1 --client-no-release-cid --loc-set-nmea-types="gga|gsa|gsv|rmc|vtg|pqxfi|pstis"
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


## We can just let mmcli do all the work if we do this...
#socat -d -d 'exec:qmicli -d /dev/cdc-wdm1 -p --client-cid=1 --client-no-release-cid --loc-follow-nmea'  tcp:12345
#gpsctl -n -D 4 tcp://127.0.0.1:12345
#gpsmon