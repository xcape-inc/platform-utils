#!/opt/modem_config/bin/python3
from pexpect.exceptions import TIMEOUT
from typing import Callable, Any
import os
import serial
import sys
import time
import pexpect.fdpexpect
import json
import re
import subprocess
import hashlib
import glob
import shutil
from fcntl import ioctl
from pySmartDL import SmartDL
from pexpect import EOF
from pexpect.exceptions import TIMEOUT
from logging import Logger
from urllib.parse import urljoin, urlparse, unquote as urllib_unquote

logger = Logger(os.path.splitext(os.path.basename(__file__))[0])

SIERRA_WIRELESS_MC74XX_FIRMWARE_URL = 'https://source.sierrawireless.com/resources/airprime/minicard/74xx/airprime-em_mc74xx-approved-fw-packages'

# FYI: known file hashes
'''
18a87c3079efa12f5f4821692a1af3f382e134730e4938748701adbb91fba724127c31c536c32e2f68de44e398ceb92ff21585fbeeb01f1ca70ecd5755fef8b4 *TMO (Generic)@SWI9X30C_02.30.01.01_Generic_002.045_002.zip
df8ae45a3a5c1279809e2e26524d10ff831f0617f940daba6bad3f5db58339542ceb3046197b846e9bb48cd1e5e47c8702fef9cb9d01c7a4cdfc42977697d2c6 *Verizon@SWI9X30C_02.33.03.00_Verizon_002.079_002.zip
c2833454ec801fb65a9190a698c68a8a306485903aa82d50facd28791cfc8556ff6fcc3ef948c176101602534ee5ccc710d46db99b20b5c58e07c8bdc1f90f0f *Sprint@SWI9X30C_02.32.11.00_Sprint_002.062_003.zip
bf2a077d344bed69498c560ca5b14f19491a0545b85faef58617e804dedbccb9ef548bfa8d3bdf8b23a038581df4b604aa2f27c983acce2693caaf35b2f5736c *Generic@SWI9X30C_02.33.03.00_Generic_002.072_001.zip
'''

# Presumed table headers (hardcoded - modelHeader column is the carrier name cells):
# ${modelHeader}   Firmware   PRI   Windows EXE   Linux Binaries   Comment
FIRMWARE_TABLE_HEADERS = [
    'Carrier',
    'Firmware',
    'PRI',
    'Windows EXE',
    'Linux Binaries',
    'Comment'
]

FIRMWARE_LINK_TABLE_HEADERS = [
    'Windows EXE',
    'Linux Binaries'
]

DEFAULT_FIRMWARE_ORDER_LIST = [
    'TMO (Generic)',
    'Verizon',
    'Sprint',
    # This seems to be causing issues
    #'Generic'
]

MODEM_FIRMWARE_DIRNAME = 'modem_firmware'

DOWNLOAD_BLOCK_SIZE = 1024 #1 Kibibyte

HASH_READ_BLOCK_SIZE = 65536  # 2**16

SERVICE_PROG_CODE='000000'

DEFAULT_DEVICE_VID_PID = '1199:9071'  # maybe these? 1199:9071|1199:9079|413C:81B6

#define USBDEVFS_RESET             _IO('U', 20)
USBDEVFS_RESET = ord('U') << (4*2) | 20


def downloadFirmware(pageUrlToParse, modelHeader, carrierList=None):
    import requests
    from bs4 import BeautifulSoup

    if carrierList is None:
        carrierList = DEFAULT_FIRMWARE_ORDER_LIST

    logger.info(f"Parsing available firmware from {pageUrlToParse}")
    firmwarePageText = requests.get(pageUrlToParse).text
    
    # Prepare the soup
    soup = BeautifulSoup(firmwarePageText, "html.parser")
    
    firmwareLinkTableObj = soup.select_one(
        f'table.fw-table:has(> tbody > tr:nth-child(1) > td:nth-child(1) > '
        + f'strong:nth-child(1):-soup-contains-own("{modelHeader}"))')
    logger.debug(f"found table:\n{firmwareLinkTableObj}")

    foundCarrierFwFileLinks = {}
    sawHeader = False
    for curFirmwareLinkTableRowObj in firmwareLinkTableObj.select(':scope > tbody > tr'):
        # skip the header row
        if not sawHeader:
            sawHeader = True
            continue
        curFirstCell = curFirmwareLinkTableRowObj.select_one(':scope > td:nth-child(1)')

        curCellStrippedStringList = [curStrippedString for curStrippedString in curFirstCell.stripped_strings]

        logger.debug(f'Checking row with first cell {curCellStrippedStringList}')

        if len(curCellStrippedStringList) != 1:
            raise RuntimeError("Too many stripped strings found in first cell {curFirstCell}")
        curFirstCellStrippedString = curCellStrippedStringList[0]

        if curFirstCellStrippedString in carrierList:
            logger.debug(f"Found row with first cell {curFirstCellStrippedString}")

            if curFirstCellStrippedString in foundCarrierFwFileLinks.keys():
                raise RuntimeError(
                    f'foundCarrierFwFileLinks already has a link entry for carrier {curFirstCellStrippedString}')

            # get the target field values (skips the carrier column since we already parsed that one)
            foundCarrierFwFileLinks[curFirstCellStrippedString] = {}
            for curCellHeaderIndex in range(1, len(FIRMWARE_TABLE_HEADERS)):
                curCellHeader = FIRMWARE_TABLE_HEADERS[curCellHeaderIndex]
                # Note: css index is 1 based, not 0
                curTargetCell = curFirmwareLinkTableRowObj.select_one(
                    f':scope > td:nth-child({curCellHeaderIndex + 1})')

                # if this is one of the link fields, get the a href value, else get the cell text
                curFieldValue = None
                if curCellHeader in FIRMWARE_LINK_TABLE_HEADERS:
                    # Note: This presently expects there ot be only 1 link in this cell and for it not to be nested
                    curLinkObj = curTargetCell.select_one(':scope > a:nth-child(1)')
                    curFieldValue = urljoin(pageUrlToParse, curLinkObj['href'])
                else:
                    curCellStrippedStringList = [
                        curStrippedString for curStrippedString in curTargetCell.stripped_strings]

                    if not len(curCellStrippedStringList) == 1:
                        raise RuntimeError(
                            f"Too many stripped strings found in cell {curCellHeaderIndex} ({curCellHeader}) for "
                            + f"{curFirstCell}: {curCellStrippedStringList}")
                    curFieldValue = curCellStrippedStringList[0]
                # append current field value to the firmware dictionary
                foundCarrierFwFileLinks[curFirstCellStrippedString].update({curCellHeader: curFieldValue})
    logger.info(f'Parsed firmware:\n{json.dumps(foundCarrierFwFileLinks, indent=4)}')

    # Make sure we found them all
    if sorted(carrierList) != sorted(foundCarrierFwFileLinks.keys()):
        raise RuntimeError(f'Not all carrier firmware files were found.\nSearch list: {sorted(carrierList)}\nFound list: {sorted(foundCarrierFwFileLinks.keys())}')

    # Parse the firmware filename and download firmware
    if not os.path.exists(MODEM_FIRMWARE_DIRNAME):
        os.mkdir(MODEM_FIRMWARE_DIRNAME)
    modemFirmwareDirPath = os.path.realpath(MODEM_FIRMWARE_DIRNAME)

    # Download them in order
    # TODO: this could maybe done concurrently to save time
    for curCarrierName in carrierList:
        curFirmwareDict = foundCarrierFwFileLinks[curCarrierName]
        curZipUrl = curFirmwareDict['Linux Binaries']

        logger.info(f'Downloading firmware for carrier {curCarrierName} ({curZipUrl})')

        targetFilename = None
        targetFilePath = None
        targetFinalUrl = None
        targetFileBytes = None

        # Streaming, so we can iterate over the response.
        '''
        with requests.get(curZipUrl, stream=True, allow_redirects=True) as responseObj:
            # make sure we got an ok response
            if responseObj.status_code != 200:
                responseObj.raise_for_status()  # Will only raise for 4xx codes, so...
                raise RuntimeError(f"Request to {curZipUrl} returned status code {responseObj.status_code}")
            targetFinalUrl = responseObj.url

            # find the filename to save as
            if "Content-Disposition" in responseObj.headers.keys():
                targetFilename = re.findall('filename="(.+)"', responseObj.headers["Content-Disposition"])[0]
            else:
                # get the path after the url has followed redirects
                curZipUrlObj = urlparse(targetFinalUrl)
                curZipUrlPath = curZipUrlObj.path
                targetFilename = urllib_unquote(os.path.basename(curZipUrlPath))
            targetFilename = f'{curCarrierName}@{targetFilename}'
            targetFilePath = os.path.join(modemFirmwareDirPath, targetFilename)

            # download the binary to a file
            logger.debug(f'Writing firmware binary for {curCarrierName} from {targetFinalUrl} to {targetFilePath}')
        
            targetFileBytes = int(responseObj.headers.get('content-length', 0))
            if targetFileBytes == 0:
                raise RuntimeError(f'http response for {curCarrierName} firmware ({targetFinalUrl}) said content size for requested file is 0! This shouldnt happen')
            
            progressBarObj = tqdm(total=targetFileBytes, unit='iB', unit_scale=True)
            with open(targetFilePath, 'wb') as outputFileObj:
                for data in responseObj.iter_content(DOWNLOAD_BLOCK_SIZE):
                    progressBarObj.update(len(data))
                    outputFileObj.write(data)
            progressBarObj.close()
            if progressBarObj.n != targetFileBytes:
                raise RuntimeError(f"something went wrong; download size ({progressBarObj.n}) does match content size from header ({targetFileBytes})")
        '''
        with requests.head(curZipUrl, allow_redirects=True) as responseObj:
            # make sure we got an ok response
            responseObj.raise_for_status()
            targetFinalUrl = responseObj.url

            # find the filename to save as
            if "Content-Disposition" in responseObj.headers.keys():
                targetFilename = re.findall('filename="(.+)"', responseObj.headers["Content-Disposition"])[0]
            else:
                # get the path after the url has followed redirects
                curZipUrlObj = urlparse(targetFinalUrl)
                curZipUrlPath = curZipUrlObj.path
                targetFilename = urllib_unquote(os.path.basename(curZipUrlPath))
            targetFilename = f'{curCarrierName}@{targetFilename}'
            targetFilePath = os.path.join(modemFirmwareDirPath, targetFilename)

            targetFileBytes = int(responseObj.headers.get('content-length', 0))
            if targetFileBytes == 0:
                raise RuntimeError(f'http response for {curCarrierName} firmware ({targetFinalUrl}) said content size for requested file is 0! This shouldnt happen')
            
        logger.debug(f'Writing firmware binary for {curCarrierName} ({targetFileBytes} bytes) from {targetFinalUrl} to {targetFilePath}')

        # delete the file if it already exists
        if os.path.exists(targetFilePath):
            os.remove(targetFilePath)

        smartDlObj = SmartDL(targetFinalUrl, targetFilePath)
        smartDlObj.start()

        sha512sum = hashlib.sha512()
        with open(targetFilePath, 'rb') as source:
            block = source.read(HASH_READ_BLOCK_SIZE)
            while len(block) != 0:
                sha512sum.update(block)
                block = source.read(HASH_READ_BLOCK_SIZE)
        logger.info(f'sha512sum:\n{sha512sum.hexdigest()} *{targetFilename}')
            

qmiFlashBinaryPath:str = None

# TODO: check out doing that with MBPL (swiflasher?)
def getQmiFlashBinaryPath():
    global qmiFlashBinaryPath

    if qmiFlashBinaryPath is None:
        qmiFlashBinaryPath = shutil.which('qmi-firmware-update')

    return qmiFlashBinaryPath


def unpackCarrierZip(carrierName, zipPath):
    carrierFirmwareDirname = os.path.join(MODEM_FIRMWARE_DIRNAME, carrierName)
    if not os.path.exists(carrierFirmwareDirname):
        os.mkdir(carrierFirmwareDirname)

    # remove existing file contents
    for curfile in glob.glob(os.path.join(carrierFirmwareDirname, '*')):
        os.remove(curfile)

    # unzip the file into the new directory
    import zipfile
    with zipfile.ZipFile(zipPath, 'r') as zip_ref:
        zip_ref.extractall(carrierFirmwareDirname)

    # ensure the expected files exist, print their hashes, and return them as a list
    # make sure there is nothing extra
    deglobbedList = glob.glob(os.path.join(carrierFirmwareDirname, '*.cwe'))
    if len(deglobbedList) != 1:
        raise RuntimeError(f'Wrong number of matches found (want exactly 1) when deglobbing cwe file for carrier {carrierName}: {json.dumps(deglobbedList, indent=4)}')
    cweFilePath = deglobbedList[0]
    deglobbedList = glob.glob(os.path.join(carrierFirmwareDirname, '*.nvu'))
    if len(deglobbedList) != 1:
        raise RuntimeError(f'Wrong number of matches found (want exactly 1) when deglobbing nvu file for carrier {carrierName}: {json.dumps(deglobbedList, indent=4)}')
    nvuFilePath = deglobbedList[0]
    deglobbedList = glob.glob(os.path.join(carrierFirmwareDirname, '*'))
    if len(deglobbedList) != 2:
        raise RuntimeError(f'Extra matches found (want exactly 2) when counting all files in carrier {carrierName} dir: {json.dumps(deglobbedList, indent=4)}')

    return [cweFilePath, nvuFilePath]


def getVidPidRegex(vidPid=None):
    global vidPidRegex

    if vidPid is None:
        vidPid = DEFAULT_DEVICE_VID_PID

    # TODO: vidPid could be an re object (for multiple or more interesting matches)
    # TODO: vidpid could be a list
    vidPidRegex = re.compile(r'^'+r'\S+\s+'*5+r'(?P<vidpid>' + re.escape(vidPid) + r')\b.*$')

    return vidPidRegex


class NoUsbDeviceFoundError(RuntimeError):
    ...


lsusbBinaryPath:str = None

def getLsusbBinaryPath():
    global lsusbBinaryPath

    if lsusbBinaryPath is None:
        lsusbBinaryPath = shutil.which('lsusb')

    return lsusbBinaryPath


def waitForModem(vidPid=None, maxRetries:int=None, interval:float=None) -> None:
    if maxRetries is None:
        maxRetries = 10
    if interval is None:
        interval = 3

    lsusbPath = getLsusbBinaryPath()

    # Try this up to 10 times
    for curTry in range(0, maxRetries):
        # if this is not the first loop, log that we are waiting and sleep
        if curTry > 0:
            logger.debug(f'Waiting for modem to appear in lsusb output (retry {curTry}) ...')
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


findBinaryPath:str = None

def getFindBinaryPath():
    global findBinaryPath

    if findBinaryPath is None:
        findBinaryPath = shutil.which('find')

    return findBinaryPath


def waitForModemDevice(vidPid=None, maxRetries:int=None, interval:float=None, pickFirstDevice:bool=None):
    if pickFirstDevice is None:
        pickFirstDevice = False

    findPath = getFindBinaryPath()

    waitForModem(vidPid=vidPid, maxRetries=maxRetries, interval=interval)

    # do we really need this here? searching dmesg is rough
    # this looks for the output from qcserial creating a tty for subdevice 3
    #ttyUSB=$(dmesg | grep '.3: Qualcomm USB modem converter detected' -A1 | grep -Eo 'ttyUSB[0-9]$' | tail -1)

    proccomp = subprocess.run([findPath, '/dev', '-maxdepth', '1', '-regex', '/dev/cdc-wdm[0-9]', '-o', '-regex', '/dev/qcqmi[0-9]'], capture_output=True, check=True, encoding='utf-8')

    matches = proccomp.stdout.splitlines()
    
    # can only do 1 atm
    if len(matches) > 1 and not pickFirstDevice:
        raise RuntimeError(f'Too many matching devices.  Expected max of 1, but found {len(matches)}')
    # if we found our device, exit the loop
    if len(matches) < 1:
        raise NoUsbDeviceFoundError(f'No matching device files.  Expected 1, but found {len(matches)}')
    retDev = matches[0]

    logger.debug(f'qmi device path: {retDev}')

    return retDev


def waitForModemAtDevice(vidPid=None, maxRetries:int=None, interval:float=None, pickFirstDevice:bool=None):
    if pickFirstDevice is None:
        pickFirstDevice = True

    findPath = getFindBinaryPath()

    waitForModemDevice(vidPid=vidPid, maxRetries=maxRetries, interval=interval)

    # do we really need this here? searching dmesg is rough
    # this looks for the output from qcserial creating a tty for subdevice 3
    #ttyUSB=$(dmesg | grep '.3: Qualcomm USB modem converter detected' -A1 | grep -Eo 'ttyUSB[0-9]$' | tail -1)

    proccomp = subprocess.run([findPath, '/dev', '-maxdepth', '1', '-mindepth', '1', '-type', 'l', '-iname', 'mm-at*'], capture_output=True, check=True, encoding='utf-8')

    matches = proccomp.stdout.splitlines()
    
    # can only do 1 atm
    if len(matches) > 1 and not pickFirstDevice:
        raise RuntimeError(f'Too many matching devices.  Expected max of 1, but found {len(matches)}')
    # if we found our device, exit the loop
    if len(matches) < 1:
        raise NoUsbDeviceFoundError(f'No matching device files.  Expected 1, but found {len(matches)}')
    retDev = matches[0]

    logger.debug(f'at device path: {retDev}')

    return retDev


class UsbDeviceFoundError(RuntimeError):
    ...


def waitForModemGoneAfterCall(methodToCall:Callable, vidPid=None, maxRetries:int=None, interval:float=None, pickFirstDevice:bool=None) -> None:
    # by default, wait up to roughly 30 seconds for this to go away
    if maxRetries is None:
        maxRetries = 100
    if interval is None:
        interval = 0.3

    waitForModemDevice(vidPid=vidPid, pickFirstDevice=pickFirstDevice)

    methodToCall()

    # Wait for the usb device to go away
    for curTry in range(0, maxRetries):
        # if this is not the first loop, log that we are waiting and sleep
        if curTry > 0:
            logger.debug(f'Waiting for modem to disappear in lsusb output (retry {curTry}) ...')
            time.sleep(interval)

        try:
            waitForModem(vidPid=vidPid, maxRetries=1)
        except(NoUsbDeviceFoundError):
            break
    else:
        raise UsbDeviceFoundError()


def resetModemUsb(vidPid=None, maxRetries:int=None, interval:float=None, pickFirstDevice=None):
    modemDevPath = waitForModemDevice(vidPid=vidPid, maxRetries=maxRetries, interval=interval, pickFirstDevice=pickFirstDevice)

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

    # This part works with ANY usb device
    with open(devNodePath, "wb") as fd:
        ioctl(fd, USBDEVFS_RESET, 0)


qmicliBinaryPath:str = None

def getQmicliBinaryPath():
    global qmicliBinaryPath

    if qmicliBinaryPath is None:
        qmicliBinaryPath = shutil.which('qmicli')

    return qmicliBinaryPath


def qmiResetModem(vidPid=None, maxRetries:int=None, interval:float=None, pickFirstDevice:bool=None, offlineRetries:int=None, resetRetries:int=None):
    if offlineRetries is None:
        offlineRetries = 2
    if resetRetries is None:
        resetRetries = 3

    qmicliPath = getQmicliBinaryPath()

    modemDevPath = waitForModemDevice(vidPid=vidPid, maxRetries=maxRetries, interval=interval, pickFirstDevice=pickFirstDevice)

    logger.debug('setting modem offline with QMI ...')
    # Set modem offline if we can (maybe modem is already offline? if so just reset)
    for curRetry in range(0, offlineRetries):
        if curRetry > 0:
            logger.debug(f'Retrying putting modem offline: {curRetry}')
        try:
            subprocess.run([qmicliPath, '-p', '-d', modemDevPath, '--dms-set-operating-mode=offline'], check=True, encoding='utf-8')
        except subprocess.CalledProcessError as e:
            # TODO: if captureing output, could look for message like
            # error: couldn't create client for the 'dms' service: CID allocation failed in the CTL client: Transaction timed out
            logger.warn(f'Could not put modem in offline mode: {e}')
        else:
            break

    def resetWithQmiCli():
        for curRetry in range(0, resetRetries):
            if curRetry > 0:
                logger.debug(f'Retrying putting modem in reset: {curRetry}')
            try:
                subprocess.run([qmicliPath, '-p', '-d', modemDevPath, '--dms-set-operating-mode=reset'], check=True, encoding='utf-8')
            except subprocess.CalledProcessError as e:
                # TODO: if captureing output, could look for message like
                # error: couldn't create client for the 'dms' service: CID allocation failed in the CTL client: Transaction timed out
                logger.warn(f'Could not put modem in offline mode: {e}')
            else:
                break
        else:
            raise RuntimeError('Failed to reset the modem')

    # issue modem reset
    logger.debug('Issuing qmi mode reset command now !!!')
    waitForModemGoneAfterCall(methodToCall=resetWithQmiCli, vidPid=vidPid, pickFirstDevice=pickFirstDevice)


def resetModem(vidPid=None, maxRetries:int=None, interval:float=None, pickFirstDevice:bool=None):
    qmiResetModem(vidPid=vidPid, maxRetries=maxRetries, interval=interval, pickFirstDevice=pickFirstDevice)
    #resetModemUsb(vidPid=vidPid, maxRetries=maxRetries, interval=interval)


def setModemToQmiMode(vidPid=None, maxRetries:int=None, interval:float=None, pickFirstDevice:bool=None):
    qmicliPath = getQmicliBinaryPath()

    modemDevPath = waitForModemDevice(vidPid=vidPid, maxRetries=maxRetries, interval=interval, pickFirstDevice=pickFirstDevice)

    # Make sure basic device mode is exposed w qmi
    '''
    [/dev/cdc-wdm1] Successfully retrieved USB compositions:
        [*] USB composition 6: DM, NMEA, AT, QMI
            USB composition 8: DM, NMEA, AT, MBIM
            USB composition 9: MBIM
    '''

    subprocess.run([qmicliPath, '-p', '-d', modemDevPath, '--dms-swi-set-usb-composition=6'], check=True, encoding='utf-8')


def qmiFactoryDefaultModem(serviceProgCode:str=None, vidPid=None, maxRetries:int=None, interval:float=None):
    if serviceProgCode is None:
        serviceProgCode = '000000'

    qmicliPath = getQmicliBinaryPath()

    modemDevPath = waitForModemDevice(vidPid=vidPid, maxRetries=maxRetries, interval=interval)

    # Verify the programming code is correct (just in case; this helps debug)
    subprocess.run([qmicliPath, '-p', '-d', modemDevPath, f'--dms-validate-service-programming-code={serviceProgCode}'], check=True, encoding='utf-8')
    
    # Reset the modem to factory defaults
    subprocess.run([qmicliPath, '-p', '-d', modemDevPath, f'--dms-restore-factory-defaults={serviceProgCode}'], check=True, encoding='utf-8')


def noop():
    pass


def getSendAtCommand(spawn) -> Callable[[str, Any, int], None]:
    def sendAtCommand(command: str, expectedResponse=None, waitTime:int=None, sleepAfter:int=None) -> None:
        if expectedResponse is None:
            expectedResponse = ['OK']
        if waitTime is None:
            waitTime = 5
        if sleepAfter is None:
            sleepAfter = 1

        if not isinstance(expectedResponse, list):
            expectedResponse = [expectedResponse]

        spawn.send(f'{command}\r\n')
        try:
            spawn.expect(expectedResponse + [EOF], waitTime)
        except TIMEOUT as e:
            e.__cause__ = RuntimeError(f'Output did not match expected response:\n{expectedResponse}\nfound before:\n{spawn.before}\nfound after:\n{spawn.after}')
            raise e
        time.sleep(sleepAfter)
    return sendAtCommand



def configureModem(serialDevPath, firmwareToApply:list=None, unlockPassword='A710') -> None:
    '''
    Method to configure a modem as desired for our carrier(s) and GPS.  This method requires the modem to be currently
    exposing its AT interface via the specified serial device path and for the unlock password provided to work

    :param serialDevPath: The device path to the serial device that exposes AT command controls
    :param firmwareToApply: A list of firmware files to expect to exist and be applied.  Each file should be a zip
                            and contain 2 file: the firmware is the .CWE file and the .NVU is the carrier
                            provisioning PRI file
    '''

    if firmwareToApply is None:
        firmwareToApply = DEFAULT_FIRMWARE_ORDER_LIST

    modemFirmwareDirPath = os.path.realpath(MODEM_FIRMWARE_DIRNAME)
    carrierFileDict = {}
    for curCarrierName in firmwareToApply:
        logger.debug(f'Unzipping firmware for carrier {curCarrierName}')
        curCarrierZip = os.path.join(modemFirmwareDirPath, f'{curCarrierName}@*.zip')

        # deglob carrier zip file
        deglobbedList = glob.glob(curCarrierZip)
        if len(deglobbedList) != 1:
            raise RuntimeError(f'Wrong number of matches found (want exactly 1) when deglobbing firmware files for carrier {curCarrierName}: {json.dumps(deglobbedList, indent=4)}')
        curCarrierZip = deglobbedList[0]

        carrierFileDict[curCarrierName] = unpackCarrierZip(curCarrierName, curCarrierZip)

    # Make sure the modem is ready (reset it first)
    resetModemUsb(pickFirstDevice=True)

    time.sleep(1)

    resetModem(pickFirstDevice=True)

    # Make sure device is in qmi mode
    setModemToQmiMode(pickFirstDevice=True)
    
    # Reset the modem to ensure settings take
    resetModem(pickFirstDevice=True)

    # factory reset modem
    # Note: cant get this to work ATM, so doing this with AT command
    #qmiFactoryDefaultModem()
    waitForModemDevice()

    # auto-discover at device name
    curSerialDevPath = serialDevPath
    if serialDevPath is None:
        curSerialDevPath = waitForModemAtDevice()

    with serial.Serial(curSerialDevPath, 115200, timeout=0) as ser:
        spawn = pexpect.fdpexpect.fdspawn(ser, encoding='utf-8', logfile = sys.stdout)

        sendAtCommand = getSendAtCommand(spawn)

        # unlock "privileged" commands on the modem
        sendAtCommand(f'AT!ENTERCND="{unlockPassword}"', waitTime=10)

        # example output from rmareset 
        '''
        AT!RMARESET=1
        !RMARESET: DEVICE REBOOT REQUIRED

        Items Restored:  2161
        Items Deleted:   0

        ERROR
        '''

        # Do a factory reset
        # <restore point> = 1—“Provision” (Sierra-provisioned SKU configuration)
        # Note: give this some extra time to respond
        sendAtCommand('AT!RMARESET=1', expectedResponse='!RMARESET: DEVICE REBOOT REQUIRED', waitTime=10)
    
    # Reset the modem to ensure modem at factory defaults
    resetModemUsb(pickFirstDevice=True)
    resetModem(pickFirstDevice=True)

    # Note: the modem may actually reboot twice; watchout! Lets give it some time to see if it will reboot a second time on its own.
    try:
        waitForModemGoneAfterCall(noop, pickFirstDevice=True)
    except UsbDeviceFoundError:
        pass

    # Give this command some extra buffer time since the modem DID just factory reset; it might take a minute to reappear (this should wait up to roughly 90 seconds)
    setModemToQmiMode(pickFirstDevice=True, maxRetries=30)
    resetModem(pickFirstDevice=True) 

    modemQmiDev = waitForModemDevice()

    # auto-discover at device name
    curSerialDevPath = serialDevPath
    if serialDevPath is None:
        curSerialDevPath = waitForModemAtDevice()

    with serial.Serial(curSerialDevPath, 115200, timeout=0) as ser:
        spawn = pexpect.fdpexpect.fdspawn(ser, encoding='utf-8', logfile = sys.stdout)

        sendAtCommand = getSendAtCommand(spawn)

        # unlock "privileged" commands on the modem
        sendAtCommand(f'AT!ENTERCND="{unlockPassword}"', waitTime=10)
        # clear all firmware images
        sendAtCommand('AT!IMAGE=0')
    # reset the modem
    resetModem()
    waitForModemDevice()

    # TODO: Move the firmware laoding to a method
    
    firmwareCommandArgs = [getQmiFlashBinaryPath(), '--update', '-d', '1199:9071', '--override-download']
    # flash each set of firmware files
    slotIndex = 0
    for curCarrierName in firmwareToApply:
        slotIndex += 1
        logger.debug(f'Flashing firmware for carrier {curCarrierName} to slot {slotIndex}')
        # Add on carrier files; each time this is called, the modem will USB will do away AGAIN and come back before its ready
        waitForModemGoneAfterCall(
            methodToCall=lambda :subprocess.run(firmwareCommandArgs + [f'--modem-storage-index={slotIndex}'] + carrierFileDict[curCarrierName], check=True, encoding='utf-8'))
        # give the modem a moment to be ready (for a new image?)
        logger.debug('Waiting for modem to return ...')
        waitForModemDevice()
        # This is good for diag AND to tell us the modem is ready
        # auto-discover at device name
        curSerialDevPath = serialDevPath
        if serialDevPath is None:
            curSerialDevPath = waitForModemAtDevice()

        with serial.Serial(curSerialDevPath, 115200, timeout=0) as ser:
            spawn = pexpect.fdpexpect.fdspawn(ser, encoding='utf-8', logfile = sys.stdout)

            sendAtCommand = getSendAtCommand(spawn)
            
            sendAtCommand('AT!IMAGE?', waitTime=10)

    # auto-discover at device name
    curSerialDevPath = serialDevPath
    if serialDevPath is None:
        curSerialDevPath = waitForModemAtDevice()

    with serial.Serial(curSerialDevPath, 115200, timeout=0) as ser:
        spawn = pexpect.fdpexpect.fdspawn(ser, encoding='utf-8', logfile = sys.stdout)

        sendAtCommand = getSendAtCommand(spawn)

        # unlock "privileged" commands on the modem
        sendAtCommand(f'AT!ENTERCND="{unlockPassword}"', waitTime=10)
        
        # TODO: need to talk to TJ about this; only have single at!sim w xcape apn (on tmo); can this work??? I've had issues...
        # enable auto-sim for firmware hopping+APN
        #sendAtCommand('AT!IMPREF="AUTO-SIM"')
        sendAtCommand('AT!IMPREF="GENERIC"')


        # set QMI mode and expose composite devices (diag,nmea,modem,rmnet0)
        #sendAtCommand('AT!USBCOMP=1,1,10D')
        sendAtCommand('AT!USBCOMP=1,1,0000010D')

        sendAtCommand('AT!LTECA=1')
        # Clears Band Restrictions
        sendAtCommand('AT!BAND=00')
    # Reboot the modem
    resetModem()
    # wait for modem to come back
    waitForModemDevice(maxRetries=30)

    # Give the modem a couple seconds to settle down
    time.sleep(2)

    # auto-discover at device name
    curSerialDevPath = serialDevPath
    if serialDevPath is None:
        curSerialDevPath = waitForModemAtDevice()

    with serial.Serial(curSerialDevPath, 115200, timeout=0) as ser:
        spawn = pexpect.fdpexpect.fdspawn(ser, encoding='utf-8', logfile = sys.stdout)

        sendAtCommand = getSendAtCommand(spawn)

        # Get a final statement about the loaded images
        sendAtCommand('AT!IMAGE?', waitTime=10)

        # unlock "privileged" commands on the modem
        sendAtCommand(f'AT!ENTERCND="{unlockPassword}"', waitTime=10)

        # disable gps auto start
        #sendAtCommand('AT!GPSAUTOSTART=0')

        # stop any running gps sessions
        #sendAtCommand('AT!GPSEND=0,255')

        ## undocumented command to enable xtra location assistance
        # Note: this MUST go before posmode options as it will reset them
        sendAtCommand('AT!GPSXTRADATAENABLE=1,3,10,1,24')

        # enabled ALL GPS modes
        #sendAtCommand('AT!GPSPOSMODE=7F')
        ## More undocumented junk.....
        '''
        !GPSPOSMODE: <MASK>
        Bit0-Standalone
        Bit1-UP MS-Based
        Bit2-UP MS-Assisted
        Bit3-CP MS-Based(2G)
        Bit4-CP MS-Assisted(2G)
        Bit5-CP MS-Based(3G)
        Bit6-CP MS-Assisted(3G)
        Bit8-UP MS-Based(4G)
        Bit9-UP MS-Assisted(4G)
        Bit10-CP MS-Based(4G)
        Bit11-CP MS-Assisted(4G)
        Bit17-A-Glonass UP MS-Based(3G)
        Bit18-A-Glonass UP MS-Assisted(3G)
        Bit19-A-Glonass CP MS-Based(3G)
        Bit20-A-Glonass CP MS-Assisted(3G)
        Bit21-A-Glonass UP MS-Based(4G)
        Bit22-A-Glonass UP MS-Assisted(4G)
        Bit23-A-Glonass CP MS-Based(4G)
        Bit24-A-Glonass CP MS-Assisted(4G)
        '''
        # Enable EVERYTHING
        sendAtCommand('AT!GPSPOSMODE=1FE037F')

        # TODO: make sure we double check all these settings...

        ##Active GOOGLE
        #### AGPS server settings GOOGLE ###
        #SUPL_HOST=supl.google.com (74.125.20.192)
        #SUPL_PORT=7276
        #SUPL_SECURE_PORT=7278
        #SUPL_NO_SECURE_PORT=3425
        #############################

        try:
            # get the current server name for assisted gps http calls
            #sendAtCommand('AT!GPSSUPLURL?', expectedResponse='supl.google.com:7275')
            sendAtCommand('AT!GPSSUPLURL?', expectedResponse='supl.google.com:7276')
        except TIMEOUT:
            # set the server name for assisted gps http calls
            #sendAtCommand('AT!GPSSUPLURL="supl.google.com:7275"')
            sendAtCommand('AT!GPSSUPLURL="supl.google.com:7276"')


        '''try:
            # get the current server port for assisted gps http calls
            #sendAtCommand('AT!GPSPORTID?', expectedResponse='7275')
            sendAtCommand('AT!GPSPORTID?', expectedResponse='7276')
        except TIMEOUT:
            # set the server port for assisted gps http calls
            #sendAtCommand('AT!GPSPORTID=7275')
            sendAtCommand('AT!GPSPORTID=7276')
        try:
            # get the current server port for assisted gps http calls
            #sendAtCommand('AT!GPSPORTID?', expectedResponse='7275')
            sendAtCommand('AT!GPSPORTID?', expectedResponse='0')
        except TIMEOUT:
            # set the server port for assisted gps http calls
            #sendAtCommand('AT!GPSPORTID=7275')
            sendAtCommand('AT!GPSPORTID=0')'''

        # enable transport security (tls) for assisted gps calls
        # disabled
        sendAtCommand('AT!GPSTRANSSEC=0')
        # enabled... ish
        #sendAtCommand('AT!GPSTRANSSEC=1')
        # enabled... tls1.1, sha1, sha256
        #sendAtCommand('AT!GPSTRANSSEC=7')
        #sendAtCommand('AT!GPSTRANSSEC=7')
        ##### GOBIIM ?
        # set the agps supl version to 1
        sendAtCommand('AT!GPSSUPLVER=2')

        sendAtCommand('AT!CUSTOM="GPSLPM",1')

        # When using an external powered antenna
        #AT+WANT=1

        #AT!CUSTOM="GPSREFLOC",1
        #AT!CUSTOM="GPSENABLE",1
        #AT!GPSNMEACONFIG=1,1
        ## orig
        #AT!GPSNMEASENTENCE=3F

    # Whenever we run a "custom"  NV setting AT command, it seems to make the dive reset itself afterwards; lets account for this
    explicitRetryCount:int = None
    try:
        waitForModemGoneAfterCall(noop, pickFirstDevice=True)
    except UsbDeviceFoundError:
        pass
    else:
        # if the device DID disconnect, we want to wait a little longer for it to reboot if needed
        explicitRetryCount = 30
        # otherwise, we just use the defaults

    # we did a lot of things;
    # Reboot the modem to ensure settings are stored
    waitForModemDevice(maxRetries=explicitRetryCount)

    # If the nv settings caused a reboot, we probably need to reset the usb before we can reset the modem
    if explicitRetryCount is not None:
        resetModemUsb()
    resetModem()
    # wait for modem to come back
    waitForModemDevice()

    # auto-discover at device name
    curSerialDevPath = serialDevPath
    if serialDevPath is None:
        curSerialDevPath = waitForModemAtDevice()

    with serial.Serial(curSerialDevPath, 115200, timeout=0) as ser:
        spawn = pexpect.fdpexpect.fdspawn(ser, encoding='utf-8', logfile = sys.stdout)

        sendAtCommand = getSendAtCommand(spawn)

        # unlock "privileged" commands on the modem
        sendAtCommand(f'AT!ENTERCND="{unlockPassword}"', waitTime=10)

        ### Set an APN for gps subsystem to use to download data
        #sendAtCommand('AT!GPSLBSAPN=1,0x1F,"IPV4V6","iot.acsdynamic"')
        #AT!GPSLBSAPN=1,0x1F,"IPV4V6","epc.tmobile.com"

        ## recomended nmea sentance for more satellites, etc
        #sendAtCommand('AT!GPSNMEASENTENCE=29FF')
        sendAtCommand('AT!GPSNMEASENTENCE=7FFF')
        #sendAtCommand('AT!GPSNMEASENTENCE=21FF')
        # Use the aux antenna for shared GPS/RX purposes (GPS/Rx diversity antenna)
        sendAtCommand('AT!CUSTOM="GPSSEL",0') # use 0 if on external antenna only

        ## send a single fix request to get agps seeded
        #spawn.send('AT!GPSFIX=2,30,4294967280\r\n')
        #spawn.expect(['OK', EOF], 5)

        ## start a gps tracking session
        #spawn.send('at!gpstrack=2,200,4294967280,1000,1\r\n')
        #spawn.expect(['OK', EOF], 5)
        #time.sleep(1)
        # fire up the gps attempt every 10 seconds for 10 seconds - any accuracy is fine; this is stand-alone only mode as the agps is controlled by os
        # this mode requires sending the string '$GPS_START' to the gps serial port to trigger the automated sessions creation
        ## note: the google server does not support msa, only msb, hence mode 2 (msb)
        #spawn.send('AT!GPSAUTOSTART=2,2,10,4294967280,15\r\n')
        sendAtCommand('AT!GPSAUTOSTART=2,2,60,4294967280,1')
        
        #AT!GPSFIX=2,255,4294967280
        
        #AT!GPSstatus?
        #at!GPSSATINFO?

        
        #AT!GPSXTRAINITDNLD
        #AT!GPSPOSMODE=7f
        #at!GPSXTRASTATUS?

        # Putting this note here.
        # Fun fact: on boot, the nmea port is at 115200, but when you enable nmea and raw mode with mmcli, it changes to 9600 . -_-  and if you dont knwo that, it looks like it just stops

    # The modem has been seen to disconnect/reconnect at the end of all of this; give some cushion for that to happen before we force reboot it
    # Whenever we run a "custom"  NV setting AT command, it seems to make the dive reset itself afterwards; lets account for this
    explicitRetryCount:int = None
    try:
        waitForModemGoneAfterCall(noop, pickFirstDevice=True)
    except UsbDeviceFoundError:
        pass
    else:
        # if the device DID disconnect, we want to wait a little longer for it to reboot if needed
        explicitRetryCount = 30
        # otherwise, we just use the defaults

    # we did a lot of things;
    # Reboot the modem to ensure settings are stored
    waitForModemDevice(maxRetries=explicitRetryCount)

    # If the nv settings caused a reboot, we probably need to reset the usb before we can reset the modem
    if explicitRetryCount is not None:
        resetModemUsb()
    resetModem()
    # wait for modem to come back
    waitForModemDevice()

    #with serial.Serial(gps_serial_dev_path, 115200, timeout=0) as ser:
    #    ser.write(b'$GPS_START\r\n')


if __name__ == '__main__':
    import logging

    # define file handler and set formatter
    streamHandler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s : %(levelname)s : %(name)s : %(message)s')
    streamHandler.setFormatter(formatter)
    streamHandler.setLevel(logging.DEBUG)

    # add file handler to logger
    logger.addHandler(streamHandler)

    # set log level
    logger.setLevel(logging.DEBUG)

    # Download the firmware images and prep them
    if 'true' != os.getenv('SKIP_FIRMWARE_DL'):
        #  Need to grab Latest T mobile, Verizon, Sprint, and generic; load in that order
        downloadFirmware(SIERRA_WIRELESS_MC74XX_FIRMWARE_URL, '7455')

    # TODO: if this gets more complex, implement click
    # call with $(find /dev -mindepth 1 -maxdepth 1 -type l -iname 'mm-at*' | head -1)
    #serial_dev_path = '/dev/ttyUSB2'
    serial_dev_path = None
    if len(sys.argv) > 1:
        serial_dev_path = sys.argv[1]

    gps_serial_dev_path = '/dev/ttyUSB1'
    if len(sys.argv) > 2:
        gps_serial_dev_path = sys.argv[2]

    configureModem(serial_dev_path)