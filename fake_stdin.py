#!/opt/fake_stdin/bin/python3
import pexpect
import pexpect.popen_spawn
import time
import sys
import os
import subprocess

recovery_waiting_for_token_authorize = '14'
recovery_proceeding = '10'
recovery_failing = '17'
fido2luks_add_key_command = 'fido2luks add-key --exclusive --token --pin --pin-source /tmp/ykpin ' + os.getenv('ROOT_DEVICE_PATH','/dev/sda2')

subprocess.run(f'(>/dev/null blinkMplay.sh {recovery_proceeding} 2>&1) || true', shell=True)

fido2luks_add_key = pexpect.spawn(fido2luks_add_key_command, logfile=sys.stdout, encoding='utf-8')
fido2luks_add_key.expect('Current password:')
time.sleep (0.1)

luks_passphrase_key = 'LUKS_ORIG_PASSWORD'
luks_passphrase = os.getenv(luks_passphrase_key)
if luks_passphrase is None or '' == luks_passphrase.strip():
    raise RuntimeError('LUKS_ORIG_PASSWORD env value is not set; please set it')
fido2luks_add_key.sendline(luks_passphrase)
time.sleep(1)
print('** Please authorize (touch) the FIDO2 token **')

# Try to set the led script pattern to 2; ignore if not working
subprocess.run(f'(>/dev/null blinkMplay.sh {recovery_waiting_for_token_authorize} 2>&1) || true', shell=True)
fido2luks_add_key.expect(pexpect.EOF)
fido2luks_add_key.close()

fido2luks_exit_code = fido2luks_add_key.exitstatus

if '0' == fido2luks_exit_code:
    subprocess.run(f'(>/dev/null blinkMplay.sh {recovery_proceeding} 2>&1) || true', shell=True)
else:
    subprocess.run(f'(>/dev/null blinkMplay.sh {recovery_failing} 2>&1) || true', shell=True)

sys.exit(fido2luks_exit_code)
