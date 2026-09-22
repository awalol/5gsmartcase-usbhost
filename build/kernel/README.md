# USB Ethernet kernel modules

本目录包含为 UDX710 Linux 4.14.98 AArch64 模块 ABI 只构建 `mii.ko`、
`usbnet.ko` 和 `cdc_ether.ko` 所需的输入。

本目录用于公开构建输入，不包含完整内核构建流程，也不会构建 `Image`、
`zImage` 或其他完整内核目标。

源码树为仓库中的 `linux-sprd`。`config.modules` 是已解析的 4.14.98
模块构建配置快照；`config.reference` 是历史参考配置，不声称是设备的完整
原始配置。`config.fragment` 记录选择这三个模块的最小配置：

```text
CONFIG_MODULES=y
CONFIG_NETDEVICES=y
CONFIG_MII=m
CONFIG_USB_SUPPORT=y
CONFIG_USB_NET_DRIVERS=y
CONFIG_USB_USBNET=m
CONFIG_USB_NET_CDCETHER=m
```

如条件允许，请使用精确匹配的 4.14.98 厂商内核树及其 `Module.symvers`。
仓库中的 4.14.133/4.14.199 内核树仅供源码参考；vermagic 一致不能证明 ABI
兼容。

## Build

将 `KERNEL` 指向精确的内核源码/构建目录，然后执行：

```sh
KERNEL=/path/to/linux-4.14.98-aarch64 \\
  CROSS_COMPILE=aarch64-linux-gnu- \\
  ./build-modules.sh
```

构建前需要先将 `patches/cdc-ether-remove-realtek-8153-blacklist.patch` 应用到
内核树；该补丁移除 `cdc_ether` 对 `0bda:8153` 的匹配项。然后脚本执行
`olddefconfig`、`modules_prepare`，并只构建三个模块目标：

```sh
cd "$KERNEL"
patch -p1 < /path/to/opensource/kernel/patches/cdc-ether-remove-realtek-8153-blacklist.patch
```

生成的 `.ko` 会复制到 `out/`，并记录 SHA-256 和 `modinfo` 信息。脚本不会
执行完整内核目标。

加载顺序为 `mii`、`usbnet`、`cdc_ether`；`cdc_ether` 依赖 usbnet，
usbnet 视配置使用 MII 相关导出符号。

## usbnet 端点偏移补丁

`patches/usbnet-endpoint-offsets.patch` 记录已构建 `usbnet.ko` 中审计过的两条
AArch64 指令：原模块访问
`ep_in` 使用了 920 而不是 928，`ep_out` 使用了 1048 而不是 1056。
源码级补丁没有臆造；这些是针对不同 `struct usb_device` 布局生成的可重定位
二进制中的偏移。`patch_usbnet_endpoint_abi.py` 已放在本目录，可独立检查
精确输入 SHA-256，只修改这两条指令，并生成清单。

对于精确基线模块，请使用本目录的 `patch_usbnet_endpoint_abi.py`。它会校验
完整输入 SHA-256，并拒绝重复打补丁。除非在目标内核和硬件上验证，否则输出
模块始终标记为 `UNTESTED_ON_DEVICE`。
