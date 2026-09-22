#!/bin/sh

# UART0 direct root shell
UART_PID=/run/uart-shell-supervisor.pid
if [ ! -s "$UART_PID" ] || ! kill -0 "$(cat "$UART_PID")" 2>/dev/null; then
    (
        while :; do
            stty -F /dev/ttyS0 115200 cs8 -cstopb -parenb -crtscts clocal cread
            setsid sh -c 'exec /bin/sh -i </dev/ttyS0 >/dev/ttyS0 2>&1'
            sleep 1
        done
    ) >/tmp/uart-shell.log 2>&1 &
    echo $! > "$UART_PID"
fi

# USB Ethernet startup (background)
# /bin/sh /home/root/usbnet-startup.sh </dev/null >/tmp/usbnet-startup.log 2>&1 &
