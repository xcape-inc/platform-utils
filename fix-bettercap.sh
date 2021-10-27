#!/bin/bash
killall wpa_suplicant
ifconfig wlan0 0.0.0.0
ip link set wlan0 down
iw wlan0 set monitor control
ip link set wlan0 up