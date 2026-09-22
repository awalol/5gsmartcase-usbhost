#!/bin/sh
sleep 5

killall powerd # 不杀掉系统在网卡上线以后会自动进入睡眠卡死
echo on > /sys/bus/platform/devices/xhci-hcd.0.auto/power/control # USB3需要设置为on以后才能识别到设备
# 但是开启了以后，想要切回USB Gadget会在开机过程卡死

#connmanctl enable gadget
#connmanctl tether gadget on

BASE=/home/root/usbnet
insmod "$BASE/mii.ko"
insmod "$BASE/usbnet.ko"
insmod "$BASE/cdc_ether.ko"

while [ ! -d /sys/class/net/eth0 ]; do
  sleep 1                             
done

connmanctl tether ethernet on

while ! ifconfig eth0 | grep -q 'inet addr:192.168.66.1'; do
    sleep 1
    ifconfig eth0 add 192.168.66.1
    ifconfig eth0 hw ether CC:E8:AC:C0:00:00
    ip link set dev eth0 up
    ip route add default via 192.168.66.2
done
ip link set dev eth0 up

ifconfig sipa_usb0 down

ifconfig eth0 add 192.168.66.1

touch /tmp/sipa_usb0_ok

ifconfig > /dev/kmsg

echo 1 > /proc/net/sfp/enable
echo 1 > /proc/net/sfp/tether_scheme
#iptables -I FORWARD -i usb0 -o sipa_eth0 -j DROP  # 原机为什么要加这个？
#ip6tables -I FORWARD -i usb0 -o sipa_eth0 -j DROP 

#echo 1 > /proc/sys/net/ipv6/conf/all/forwarding
