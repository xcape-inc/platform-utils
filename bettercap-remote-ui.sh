#!/bin/bash
set -e
trap 'catch $? $LINENO' ERR
catch() {
  echo "Error $1 occurred on $2"
}
set -euo pipefail

SCRIPT_PATH=$0
REAL_SCRIPT_PATH=$(readlink -f ${SCRIPT_PATH})
SCRIPT_DIR=$(dirname ${REAL_SCRIPT_PATH}})

#touch /var/log/bettercap.log
#tail -f /var/log/bettercap.log &
KERNEL_VERSION=$(uname -r)
DKMS_DRIVER_NAME=8814au
DKMS_DRIVER_FILENAME="${DKMS_DRIVER_NAME}.ko"
DKMS_DRIVER_PATH="/lib/modules/${KERNEL_VERSION}/updates/dkms/${DKMS_DRIVER_FILENAME}"
echo "DKMS_DRIVER_PATH=${DKMS_DRIVER_PATH}"
#exit 1
sudo rmmod "${DKMS_DRIVER_NAME}" || true

sudo insmod "${DKMS_DRIVER_PATH}" || true
sleep 1
sudo /usr/local/bin/fix-bettercap.sh
sleep 1
exec sudo bettercap -no-colors -eval "set events.stream.output /var/log/bettercap.log; events.stream off; events.stream on; set api.rest.websocket true; https-ui; set gps.device localhost:2947; gps on; set wifi.interface wlan0; wifi.recon on"
#bettercap -no-colors -caplet https-ui & #-autostart 'gps' #,wifi.recon'
#exit 0