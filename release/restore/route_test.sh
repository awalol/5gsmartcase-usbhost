#!/bin/sh
sleep 5

connmanctl enable gadget
connmanctl tether gadget on

ret=`ifconfig -a | grep "inet addr:192.168.66.1"`
echo $ret
while [ -z "$ret" ]
do
	sleep 1
	ifconfig usb0 add 192.168.66.1
	ifconfig usb0 hw ether CC:E8:AC:C0:00:00
	ip link set dev usb0 up
	ip route add default via 192.168.66.2
	ret=`ifconfig -a | grep "inet addr:192.168.66.1"`
	echo $ret
done
ip link set dev usb0 up

ifconfig sipa_usb0 down

ifconfig usb0 add 192.168.66.1

touch /tmp/sipa_usb0_ok

ifconfig > /dev/kmsg

echo 1 > /proc/net/sfp/enable
echo 1 > /proc/net/sfp/tether_scheme
iptables -I FORWARD -i usb0 -o sipa_eth0 -j DROP
ip6tables -I FORWARD -i usb0 -o sipa_eth0 -j DROP
#echo 1 > /proc/sys/net/ipv6/conf/all/forwarding
