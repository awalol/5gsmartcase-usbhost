#!/usr/bin/env python3
"""修正精确已安装 usbnet.ko 中经过审计的两处端点数组访问。

这是尚未在设备上验证的候选模块，不是通用 ABI 转换。DWARF 仍描述原始构建，
本工具不会安装或加载模块。
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

SOURCE_SHA256 = "d18d0fc758a5f23d74f2bbd72bc57cd3db1ddc9f86e59a2ba91edc5040a8e33f"
SOURCE_SIZE = 435872
TEXT_OFFSET = 0x40
PATCHES = (
    (0x2f64, 0xf941cc00, 0xf941d000, "init_status: ep_in base 920 -> 928"),
    (0x3050, 0xf9420c00, 0xf9421000, "usbnet_probe: ep_out base 1048 -> 1056"),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patch_bytes(source):
    if len(source) != SOURCE_SIZE or digest(source) != SOURCE_SHA256:
        raise ValueError("unsupported usbnet module; exact source SHA-256 required")
    if source[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", source, 18)[0] != 183:
        raise ValueError("expected ELF64 little-endian AArch64")
    result = bytearray(source)
    for address, before, after, _ in PATCHES:
        offset = TEXT_OFFSET + address
        if struct.unpack_from("<I", source, offset)[0] != before:
            raise ValueError(f"instruction mismatch at {offset:#x}")
        struct.pack_into("<I", result, offset, after)
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    report_path = args.destination.with_suffix(args.destination.suffix + ".manifest.json")
    if args.source.resolve() == args.destination.resolve() or args.destination.exists() or report_path.exists():
        parser.error("refusing to overwrite source, destination, or manifest")
    try:
        source = args.source.read_bytes()
        result = patch_bytes(source)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    report = {
        "status": "UNTESTED_ON_DEVICE",
        "purpose": "Correct confirmed endpoint-array ABI offsets, not yet prove USB3 RX repair",
        "input_sha256": digest(source),
        "output_sha256": digest(result),
        "size": len(result),
        "changed_bytes": sum(a != b for a, b in zip(source, result)),
        "original_dwarf_not_updated": True,
        "other_abi_fields_not_certified": True,
        "patches": [dict(text_offset=hex(a), file_offset=hex(a + TEXT_OFFSET),
                         before=hex(b), after=hex(c), purpose=d) for a, b, c, d in PATCHES],
    }
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open("xb") as stream:
        stream.write(result)
    with report_path.open("x", encoding="ascii") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
