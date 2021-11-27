#!/usr/bin/env python3

import json
import shutil
import time
import re
import subprocess
import sys
import os
import os.path
from logging import Logger
from fcntl import ioctl

logger = Logger(os.path.split(os.path.basename(__file__))[0])

DEFAULT_DEVICE_VID_PID = '1199:9071'  # maybe these? 1199:9071|1199:9079|413C:81B6

#define USBDEVFS_RESET             _IO('U', 20)
USBDEVFS_RESET = ord('U') << (4*2) | 20

class NoUsbDeviceFoundError(RuntimeError):
    ...

def getVidPidRegex(vidPid=None):
    global vidPidRegex

    if vidPid is None:
        vidPid = DEFAULT_DEVICE_VID_PID

    # TODO: vidPid could be an re object (for multiple or more interesting matches)
    # TODO: vidpid could be a list
    vidPidRegex = re.compile(r'^'+r'\S+\s+'*5+r'(?P<vidpid>' + re.escape(vidPid) + r')\b.*$')

    return vidPidRegex

def waitForModem(vidPid=None, maxRetries:int=None, interval:float=None) -> None:
    if maxRetries is None:
        maxRetries = 10
    if interval is None:
        interval = 3

    lsusbPath = shutil.which('lsusb')

    # Try this up to 10 times
    for curTry in range(0,maxRetries):
        # if this is not the first loop, log that we are waiting and sleep
        logging.debug(f'Waiting for modem to appear in lsusb output (retry {curTry}) ...')
        time.sleep(interval)

        proccomp = subprocess.run([lsusbPath], capture_output=True, check=True, encoding='utf-8')

        matches = []
        for curLine in proccomp.stdout.splitlines():
            curMatch = getVidPidRegex(vidPid).search(curLine)
            if curMatch is not None:
                matches += [curMatch]
        matchLines = [curMatch.group(0) for curMatch in matches]

        # can only do 1 atm
        if len(matches) > 1:
            raise RuntimeError(f'Too many matching devices.  Expected max of 1, but found {len(matches)}:\n{json.dumps(matchLines, indent=4)}')
        # if we found our device, exit the loop
        if len(matches) > 0:
            logger.debug(f'found the following device line:\n{matches[0]}')
            break
    else:
        # we did not find the target device
        raise NoUsbDeviceFoundError('No usb device found')

def waitForModemDevice(vidPid=None, maxRetries:int=None, interval:float=None):
    findPath = shutil.which('find')

    waitForModem(vidPid=vidPid, maxRetries=maxRetries, interval=interval)

    # do we really need this here? searching dmesg is rough
    # this looks for the output from qcserial creating a tty for subdevice 3
    #ttyUSB=$(dmesg | grep '.3: Qualcomm USB modem converter detected' -A1 | grep -Eo 'ttyUSB[0-9]$' | tail -1)

    proccomp = subprocess.run([findPath, '/dev', '-maxdepth', '1', '-regex', '/dev/cdc-wdm[0-9]', '-o', '-regex', '/dev/qcqmi[0-9]'], capture_output=True, check=True, encoding='utf-8')

    matches = proccomp.stdout.splitlines()
    
    # can only do 1 atm
    #if len(matches) > 1:
    #    raise RuntimeError(f'Too many matching devices.  Expected max of 1, but found {len(matches)}')
    # if we found our device, exit the loop
    if len(matches) < 1:
        raise RuntimeError(f'No matching device files.  Expected 1, but found {len(matches)}')

    return matches[0]

def resetModem(vidPid=None, maxRetries:int=None, interval:float=None):
    modemDevPath = waitForModemDevice(vidPid=vidPid, maxRetries=maxRetries, interval=interval)

    # get enough device info to find the pci device
    qmiDevStat = os.stat(modemDevPath)
    qmiDevRdev = qmiDevStat.st_rdev
    qmiDevMajor = os.major(qmiDevRdev)
    qmiDevMinor = os.minor(qmiDevRdev)
    # USB sysfs for QMI char device of modem
    sysfsCharDevPath = f'/sys/dev/char/{qmiDevMajor}:{qmiDevMinor}/device'
    sysfsCharDevPciPath = os.path.realpath(sysfsCharDevPath)
    logger.debug(f'USB sysfs for QMI char device of modem: {sysfsCharDevPciPath}')
    # PCI device
    sysfsUsbDevPciPath = os.path.join(sysfsCharDevPciPath, '..')
    logger.debug(f'sysfs path for PCI device for QMI char device of modem: {sysfsUsbDevPciPath}')
    # PCI device info
    sysfsUsbDevPciBusnumPath = os.path.join(sysfsUsbDevPciPath, 'busnum')
    sysfsUsbDevPciDevnumPath = os.path.join(sysfsUsbDevPciPath, 'devnum')

    usbDevPciBusNum = None
    with open(sysfsUsbDevPciBusnumPath, 'r') as busnumFileObj:
        usbDevPciBusNum = int(busnumFileObj.read())

    usbDevPciDevNum = None
    with open(sysfsUsbDevPciDevnumPath, 'r') as devnumFileObj:
        usbDevPciDevNum = int(devnumFileObj.read())

    devNodePath=f'/dev/bus/usb/{usbDevPciBusNum:03d}/{usbDevPciDevNum:03d}'
    logger.debug(f'Path to the USB device node of the modem: {devNodePath}')

    if os.environ.get('PREP_ONLY', 'false') != 'true':
        # This part works with ANY usb device
        with open(devNodePath, "wb") as fd:
            ioctl(fd, USBDEVFS_RESET, 0)

if __name__ == '__main__':
    import logging

    # define file handler and set formatter
    streamHandler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s : %(levelname)s : %(name)s : %(message)s')
    streamHandler.setFormatter(formatter)

    # add file handler to logger
    logger.addHandler(streamHandler)

    # set log level
    logger.setLevel(logging.DEBUG)

    # TODO: if this gets more complex, implement click
    devVidPid = None
    if len(sys.argv) > 1:
        devVidPid = sys.argv[1]

    resetModem(devVidPid)