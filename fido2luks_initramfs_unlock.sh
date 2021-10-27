#!/bin/sh -e

trap 'catch $? ${LINENO:-}' EXIT
normal_decrypt_failed=6
catch() {
  line_no=${2:-}
  #if [ -n "${line_no:-}" ]; then
    >&2 echo "Error $1 occurred on ${2:-unset}"
    (>/dev/null blinkMplay.sh ${normal_decrypt_failed} 2>&1) || true
  #fi
}
set -eu

set -a
. /etc/fido2luks.conf
FIDO2LUKS_PASSWORD_HELPER=${FIDO2LUKS_PASSWORD_HELPER:-true}

if [ -z "${FIDO2LUKS_PASSWORD_HELPER:-}" ]; then
	MSG="FIDO2 password salt for $CRYPTTAB_NAME"
	export FIDO2LUKS_PASSWORD_HELPER="plymouth ask-for-password --prompt '$MSG'"
fi

if [ "1${FIDO2LUKS_USE_TOKEN:-}" -eq 11 ]; then
	export FIDO2LUKS_CREDENTIAL_ID="${FIDO2LUKS_CREDENTIAL_ID},$(fido2luks token list --csv ${CRYPTTAB_SOURCE})"
fi

normal_waiting_for_token_authorize=2
recovery_initramfs=7

(>/dev/null blinkMplay.sh ${normal_waiting_for_token_authorize} 2>&1) || true

>&2 echo '** Please authorize (touch) the FIDO2 token **'
if [ -n "${FIDO2LUKS_CTAP_PIN}" ]; then
    echo "${FIDO2LUKS_CTAP_PIN}" | fido2luks print-secret --bin --pin --pin-source /dev/stdin
else
    fido2luks print-secret --bin
fi

(>/dev/null blinkMplay.sh ${recovery_initramfs} 2>&1) || true

trap - EXIT