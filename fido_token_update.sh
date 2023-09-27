#!/bin/bash
set -e
trap 'catch $? $LINENO' ERR
# recovery, reset failed
recovery_failing=17
catch() {
  echo "Error $1 occurred on $2"
  (>/dev/null sudo blinkMplay.sh ${recovery_failing} 2>&1) || true
}
set -euo pipefail

# recovery, reset started
recovery_proceeding=10
echo "FIDO enhancement started"
(>/dev/null sudo blinkMplay.sh ${recovery_proceeding} 2>&1) || true

# recovery, reset filesystem setup (including cryptsetup)
recovery_filesystem_setup=11
# recovery, reset awaiting storage device
recovery_waiting_for_storage=12
# recovery, reset awaiting crypto key
recovery_waiting_for_token=13
# recovery, reset awaiting crypto key touch
recovery_waiting_for_token_authorize=14
# recovery, reset copying files
recovery_loading_files=15
# recovery, reset complete, rebooting
recovery_complete=16

if [[ $( (sudo dmidecode | grep -E '^[^#]' > /dev/null) || echo 'no_dmi') == "no_dmi" ]]; then
  # Hail mary to get the serial number of the processor; this works on rpi w no dmi
  DEVICE_SERIAL_NUMBER=$(cat /proc/cpuinfo | grep Serial | cut -d ' ' -f 2)
else
  # Look for Processor Information block in dmidecode
  found_proc=false
  indented_regex='^[[:space:]][[:space:]]*.*'
  id_regex='^[[:space:]][[:space:]]*ID:.*'
  while IFS= read -r line ; do
    if [[ 'true' == "${found_proc}" ]]; then
      #echo 'in proc'
      if [[ "${line}" =~ $indented_regex ]]; then
        #printf '%s\n' "$line"
        if [[ "${line}" =~ $id_regex ]]; then
          #echo "Serial line: ${line}"
          DEVICE_SERIAL_NUMBER=$(printf '%s' "$line" | sed "s/ *ID: //" | sed "s/\\s*//g")
          #echo "Device Serial Number: ${DEVICE_SERIAL_NUMBER}"
          break
        fi
      else
        #printf '!! %s\n' "$line"
        break
      fi
    elif [[ 'Processor Information' == "${line}" ]]; then
      found_proc=true
      #echo "-- found proc"
    fi
  done <<< "$(sudo dmidecode)"
fi

echo "Device Serial Number: ${DEVICE_SERIAL_NUMBER}"

BASE_ETH_DEV=${BASE_ETH_DEV:-eth0}
ETHERNET_MAC_ADDR=$(cat /sys/class/net/${BASE_ETH_DEV}/address | sed 's/://g')
echo "Ethernet MAC Address: ${ETHERNET_MAC_ADDR}"
DEVICE_PATH="${1:-${DEVICE_PATH:-/dev/sda}}"
BOOTLOADER_DEVICE_NUMBER=${BOOTLOADER_DEVICE_NUMBER:-1}
BOOTLOADER_DEVICE_PATH="${BOOTLOADER_DEVICE_PATH:-${DEVICE_PATH}${BOOTLOADER_DEVICE_NUMBER}}"
BOOT_DEVICE_NUMBER=${BOOT_DEVICE_NUMBER:-1}
BOOT_DEVICE_PATH="${BOOT_DEVICE_PATH:-${DEVICE_PATH}${BOOT_DEVICE_NUMBER}}"
ROOT_DEVICE_NUMBER=${ROOT_DEVICE_NUMBER:-2}
ROOT_DEVICE_PATH="${ROOT_DEVICE_PATH:-${DEVICE_PATH}${ROOT_DEVICE_PATH}}"
BOOT_MOUNT_POINT=${BOOT_MOUNT_POINT:-/boot}
ROOT_MOUNT_POINT=${ROOT_MOUNT_POINT:-/}
DM_CRYPT_NAME=${DM_CRYPT_NAME:-dm_crypt-0}
# Try a maximum of 60 times to ensure FIDO2 token and storage device are attached
MAX_HW_RETRY=60

# loop to ensure both storage device and token are installed on device
for i in $(seq 1 ${MAX_HW_RETRY}); do
  ((sudo fido2luks connected > /dev/null 2>&1) || (echo '** FIDO2 token not detected; please attach token **' && ((>/dev/null sudo blinkMplay.sh ${recovery_waiting_for_token} 2>&1) || true) && false)) && ([ -e "${DEVICE_PATH}" ] || (echo '** Storage device not detected; please attach storage **' && ((>/dev/null sudo blinkMplay.sh ${recovery_waiting_for_storage} 2>&1) || true) && false)) && break || true
  sleep 1
done



## set up the token
echo "Configuring FIDO2 token credentials"

# set the fido pin (required)
sudo ykman fido access verify-pin -P "${DEVICE_SERIAL_NUMBER}" || sudo ykman fido access change-pin -n "${DEVICE_SERIAL_NUMBER}"

# Create the fido application credential in the yubikey and
# configure it and a new random salt in the /tmp/fido2luks.conf
# file to be copied to encrypted fs after creation
sudo cp -a /etc/fido2luks.conf /tmp/

echo '** Please authorize (touch) the FIDO2 token **'
(>/dev/null sudo blinkMplay.sh ${recovery_waiting_for_token_authorize} 2>&1) || true
sudo sed -i 's/^FIDO2LUKS_CREDENTIAL_ID=.*//' /tmp/fido2luks.conf
(echo FIDO2LUKS_CREDENTIAL_ID=$(echo "${DEVICE_SERIAL_NUMBER}" | sudo fido2luks credential --pin --pin-source /dev/stdin "xcape_ptdb_${DEVICE_SERIAL_NUMBER}_dm_crypt_0") | sudo tee -a /tmp/fido2luks.conf)
(>/dev/null sudo blinkMplay.sh ${recovery_proceeding} 2>&1) || true

sudo sed -i 's/^FIDO2LUKS_SALT=.*//' /tmp/fido2luks.conf
(echo "FIDO2LUKS_SALT=string:$(tr -dc A-Za-z0-9 </dev/urandom | head -c 32 ; echo '')" | sudo tee -a /tmp/fido2luks.conf)
(echo "FIDO2LUKS_CTAP_PIN=${DEVICE_SERIAL_NUMBER}" | sudo tee -a /tmp/fido2luks.conf)


#mkdir -p "${BOOT_MOUNT_POINT}"
mkdir -p "${ROOT_MOUNT_POINT}"
# TODO: do away with initial use of passphrase; use fido2luks generated file to directly set slot 0 secret
#export LUKS_ORIG_PASSWORD=${LUKS_ORIG_PASSWORD:-$(tr -dc A-Za-z0-9 </dev/urandom | head -c 32)}
#echo -n "${LUKS_ORIG_PASSWORD}" | sudo cryptsetup --type luks2 --cipher aes-xts-plain64 --hash sha512 --iter-time 2000 --key-size 512 --pbkdf argon2i --sector-size 512 --use-urandom --verify-passphrase=No $(if [ 'true' = "${USE_INTEGRITY:-false}" ]; then printf '%s' '--integrity hmac-sha256'; else echo ''; fi) luksFormat "${DEVICE_PATH}2"
#echo '* root filesystem formatted *'
(echo "${DEVICE_SERIAL_NUMBER}" > /tmp/ykpin) && (set -a; . /tmp/fido2luks.conf; ROOT_DEVICE_PATH=${ROOT_DEVICE_PATH} sudo -E /opt/fake_stdin/fake_stdin.py); result=$?; rm -f /tmp/ykpin; (if [[ 0 -ne "${result}" ]]; then echo enroll failed; false; else echo enroll success; fi)
echo '* FIDO2 token enrolled to unlock encrypted filesystem'
#echo -n 'pentestdropbox' | sudo cryptsetup open "${DEVICE_PATH}2" "${DM_CRYPT_NAME}"
#echo '** Please authorize (touch) the FIDO2 token **'
#(>/dev/null sudo blinkMplay.sh ${recovery_waiting_for_token_authorize} 2>&1) || true
#(set -a; . /tmp/fido2luks.conf; echo "${DEVICE_SERIAL_NUMBER}" | sudo -E fido2luks open --pin --pin-source /dev/stdin "${DEVICE_PATH}2" "${DM_CRYPT_NAME}")
#echo '* FIDO2 token token successfully unlocked filesystem'
#(>/dev/null sudo blinkMplay.sh ${recovery_filesystem_setup} 2>&1) || true

# copy the filled in fido2luks.conf to the new root filesystem
sudo cp -a /tmp/fido2luks.conf "${ROOT_MOUNT_POINT}/etc/fido2luks.conf"
sudo rm /tmp/fido2luks.conf

# https://askubuntu.com/questions/1097407/grub-looking-for-an-encrypted-root-uuid-before-the-container-decryption

# edit /mnt/etc/crypttab to add root partion
ROOT_CRYPT_UUID=$(sudo blkid -s UUID -o value ${ROOT_DEVICE_PATH})
#ROOT_UUID=$(sudo blkid -s UUID -o value /dev/ubuntu-vg/root)
BOOT_UUID=$(sudo blkid -s UUID -o value ${BOOT_DEVICE_PATH})
# TODO: have to be sure to remove existing entry first
if [[ -e /etc/crypttab ]]; then
  sudo sed -i "/UUID=${ROOT_CRYPT_UUID}/d" /etc/crypttab
else
  (echo '# <target name>	<source device>		<key file>	<options>' | sudo tee /etc/crypttab > /dev/null)
fi
echo "${DM_CRYPT_NAME} UUID=${ROOT_CRYPT_UUID} none luks,initramfs,keyscript=fido2luks-xcape" | sudo tee -a "${ROOT_MOUNT_POINT}/etc/crypttab"
# update root in fstab
sudo sed -i "s/^LABEL=system-boot/UUID=${BOOT_UUID}/" "${ROOT_MOUNT_POINT}/etc/fstab"
sudo sed -i "s/^LABEL=writable/\\/dev\\/mapper\\/ubuntu--vg-root/" "${ROOT_MOUNT_POINT}/etc/fstab"
# update root in cmdline.txt to make the serial console primary (needed for preboot debugging)
# note: removed this as the initramfs hooks for blinkm are in place; console will be on hdmi
#sudo sed -i "s/^\\(.* \\)console=tty1 \\(.*\\)\$/\\1\\2/" "${ROOT_MOUNT_POINT}/boot/firmware/cmdline.txt"
#sudo sed -i "s/^\\(.*\\)\$/console=tty1 \\1/" "${ROOT_MOUNT_POINT}/boot/firmware/cmdline.txt"
# update root in cmdline.txt for encrypt stuff
####################sudo sed -i "s/^\\(.* root=\\)[^ ]* \\(.*\\)\$/\\1\\/dev\\/mapper\\/ubuntu--vg-root cryptdevice=UUID=${ROOT_CRYPT_UUID}:${DM_CRYPT_NAME} \\2/" "${ROOT_MOUNT_POINT}/boot/firmware/cmdline.txt"
#### sudo sed -i " cryptdevice=UUID=${ROOT_CRYPT_UUID}:${DM_CRYPT_NAME} \\2/" "${ROOT_MOUNT_POINT}/boot/firmware/cmdline.txt"
# update the kernel parameters with the newly deployed key and ensure the fido2luks script gets used in initramfs

new_param=$(set -a; . "${ROOT_MOUNT_POINT}/etc/fido2luks.conf"; echo "cryptdevice=UUID=${ROOT_CRYPT_UUID}:${DM_CRYPT_NAME} rd.luks.2fa=${FIDO2LUKS_CREDENTIAL_ID}:${ROOT_DEVICE_PATH}")
#sudo sed -i "s/ fixrtc\$/ fixrtc ${new_param}/" "${ROOT_MOUNT_POINT}/boot/firmware/cmdline.txt"
sudo sed -i "s/^\\(GRUB_CMDLINE_LINUX=\".*\\)\\bcryptdevice=[^ ]* */\\1/; s/^\\(GRUB_CMDLINE_LINUX=\".*\\)\\brd.luks.2fa=[^ ]* */\\1/" "${ROOT_MOUNT_POINT}/etc/default/grub"
add_space=""
grep -E "^GRUB_CMDLINE_LINUX=\"\"" /etc/default/grub || add_space=" "
sudo sed -i "s@^\\(GRUB_CMDLINE_LINUX=\"\\)@\\1${new_param}${add_space}@" "${ROOT_MOUNT_POINT}/etc/default/grub"

# patch /lib/cryptsetup/scripts/fido2luks with pin
#sudo sed -i "s/^\\(fido2luks print-secret .*\\)/echo ${DEVICE_SERIAL_NUMBER} | \\1 --pin --pin-source \\/dev\\/stdin/" "${ROOT_MOUNT_POINT}/lib/cryptsetup/scripts/fido2luks"
# bug fix
#sudo sed -i "s/\\("'$FIDO2LUKS_USE_TOKEN" -eq'"\\) 1/1\\1 11/" "${ROOT_MOUNT_POINT}/lib/cryptsetup/scripts/fido2luks"
#cat "${ROOT_MOUNT_POINT}/lib/cryptsetup/scripts/fido2luks"
# Move the recovery button initramfs script to the correct stage in recovery image
#sudo mv "${ROOT_MOUNT_POINT}/usr/share/initramfs-tools/scripts/check_recovery_button_press" "${ROOT_MOUNT_POINT}/usr/share/initramfs-tools/scripts/init-premount/"

#cat "${ROOT_MOUNT_POINT}/boot/firmware/cmdline.txt"
#sudo mount -o bind /proc "${ROOT_MOUNT_POINT}/proc"
#sudo mount -o bind /dev "${ROOT_MOUNT_POINT}/dev"
#sudo mount -o bind /sys "${ROOT_MOUNT_POINT}/sys"
#sudo chroot "${ROOT_MOUNT_POINT}" /bin/bash <<"EOT"
pwd
cat /etc/crypttab
sudo update-initramfs -u -k all
sudo grub-mkconfig | sudo tee /boot/grub/grub.cfg
#EOT
#sudo umount "${ROOT_MOUNT_POINT}/proc"
#sudo umount "${ROOT_MOUNT_POINT}/dev"
#sudo umount "${ROOT_MOUNT_POINT}/sys"

echo "FIDO enhancement complete"
(>/dev/null sudo blinkMplay.sh ${recovery_complete} 2>&1) || true

#sudo reboot
