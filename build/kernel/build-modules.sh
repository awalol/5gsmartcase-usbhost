#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KERNEL=${KERNEL:?set KERNEL to the exact Linux 4.14.98 source/build tree}
ARCH=${ARCH:-arm64}
CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}
OUT="$ROOT/out"

export ARCH CROSS_COMPILE
mkdir -p "$OUT"
cp "$ROOT/config.modules" "$KERNEL/.config"
"$KERNEL/scripts/config" --file "$KERNEL/.config" \
  --enable MODULES --enable NETDEVICES --module MII \
  --enable USB_SUPPORT --enable USB_NET_DRIVERS \
  --module USB_USBNET --module USB_NET_CDCETHER
make -C "$KERNEL" olddefconfig
make -C "$KERNEL" -j"$JOBS" modules_prepare
make -C "$KERNEL" -j"$JOBS" M=drivers/net mii.ko
make -C "$KERNEL" -j"$JOBS" M=drivers/net/usb usbnet.ko cdc_ether.ko

cp "$KERNEL/drivers/net/mii.ko" "$OUT/"
cp "$KERNEL/drivers/net/usb/usbnet.ko" "$OUT/"
cp "$KERNEL/drivers/net/usb/cdc_ether.ko" "$OUT/"
sha256sum "$OUT"/*.ko > "$OUT/SHA256SUMS"
if command -v modinfo >/dev/null 2>&1; then
  modinfo "$OUT/mii.ko" "$OUT/usbnet.ko" "$OUT/cdc_ether.ko" > "$OUT/modinfo.txt" || true
fi
printf '%s\n' "built mii.ko usbnet.ko cdc_ether.ko in $OUT"
